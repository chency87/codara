import asyncio
import hashlib
import json
from time import perf_counter
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from codara.core.models import (
    Message, UagOptions, TurnResult, Session, SessionStatus, 
    ProviderType, Account, is_account_enabled_status
)
from codara.database.manager import DatabaseManager
from codara.accounts.pool import AccountPool
from codara.workspace.engine import WorkspaceEngine
from codara.adapters.codex import CodexAdapter
from codara.adapters.gemini import GeminiAdapter
from codara.adapters.opencode import OpenCodeAdapter
from codara.adapters.base import ProviderAdapter
from codara.core.atr import ATRModule
from codara.config import resolve_provider_model
from codara.telemetry import record_event, start_span

class Orchestrator:
    def __init__(self, db_manager: DatabaseManager, max_concurrency: int = 10):
        self.db = db_manager
        self.account_pool = AccountPool(db_manager)
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._adapters: dict[ProviderType, ProviderAdapter] = {}

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    def _dedupe_actions(self, actions: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[str] = set()
        for action in actions:
            key = json.dumps(
                {k: v for k, v in action.items() if k != "action_id"},
                sort_keys=True,
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(action))
        for index, action in enumerate(deduped, start=1):
            action["action_id"] = f"atr_{index}"
        return deduped

    async def handle_request(
        self,
        options: UagOptions,
        messages: List[Message],
        provider_model: Optional[str] = None,
    ) -> TurnResult:
        # 1. Session Lookup
        session_id = options.client_session_id or str(uuid4())
        resolved_provider_model = resolve_provider_model(options.provider, provider_model)
        record_event(
            "orchestrator.request.bound",
            component="orchestrator",
            db=self.db,
            attributes={
                "session_id": session_id,
                "provider": options.provider.value,
                "workspace_root": options.workspace_root,
            },
        )
        
        async with self._get_session_lock(session_id):
            session = self.db.get_session(session_id)
            
            # 2. Workspace Engine Setup
            if not options.workspace_root:
                raise ValueError("workspace_root is required")
            
            ws_engine = WorkspaceEngine(options.workspace_root)
            is_git_repo = ws_engine.is_git_repo()
            
            # Prefix Hash Calculation
            tree_metadata = ws_engine.get_file_tree_metadata()
            # In a real impl, we'd also include the system prompt in the hash
            prefix_hash = hashlib.sha256(tree_metadata.encode()).hexdigest()

            # --- Provider-specific account handling ---
            use_account_pool = options.provider == ProviderType.CODEX
            account: Optional[Account] = None

            if not session:
                # 3. Account Selection for new session
                if use_account_pool:
                    account = self.account_pool.acquire_account(options.provider)
                    if not account:
                        raise RuntimeError(f"No available account for provider {options.provider}")
                
                session = Session(
                    client_session_id=session_id,
                    backend_id="", # Initially empty for new session
                    provider=options.provider,
                    # For non-pooled providers, use the static system account ID
                    account_id=account.account_id if use_account_pool and account else f"{options.provider.value}-system",
                    user_id=options.user_id,
                    api_key_id=options.api_key_id,
                    cwd_path=options.workspace_root,
                    prefix_hash=prefix_hash,
                    status=SessionStatus.IDLE,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    expires_at=datetime.now() + timedelta(hours=24),
                    last_context_tokens=0,
                )
            else:
                if session.provider != options.provider:
                    session.provider = options.provider
                    session.backend_id = ""
                    session.account_id = f"{options.provider.value}-system"
                    session.prefix_hash = prefix_hash
                    session.last_context_tokens = 0
                    session.status = SessionStatus.IDLE
                    session.updated_at = datetime.now()
                    session.expires_at = datetime.now() + timedelta(hours=24)
                session.cwd_path = options.workspace_root

            if use_account_pool and session:
                account = self.db.get_account(session.account_id)
                # Check if current account is valid and not in cooldown
                now = datetime.now()
                if not account or not is_account_enabled_status(account.status) or (account.cooldown_until and account.cooldown_until > now):
                    account = self.account_pool.acquire_account(options.provider)
                    if not account:
                        raise RuntimeError(f"No available account for provider {options.provider}")
                    session.account_id = account.account_id
            elif session:
                session.account_id = f"{options.provider.value}-system"
            
            if options.user_id:
                session.user_id = options.user_id
            if options.api_key_id:
                session.api_key_id = options.api_key_id

            # For providers not using the pool, ensure account object exists for downstream logic
            if not account and use_account_pool:
                 account = self.db.get_account(session.account_id)

            # 4. Dispatch (Semaphore gated)
            async with self.semaphore:
                max_retries = 3
                attempt = 0
                
                while attempt < max_retries:
                    if not ws_engine.acquire_lock():
                        raise RuntimeError("Failed to acquire workspace lock")
                    
                    try:
                        if not options.manual_mode and not is_git_repo:
                            ws_engine.take_snapshot()

                        # 6. Adapter Selection
                        adapter = self._get_adapter(options.provider)
                        
                        # Execute Turn
                        user_id = options.user_id or session.user_id
                        if user_id:
                            async with self._get_user_lock(user_id):
                                user = self.db.get_user(user_id)
                                if user:
                                    active_sessions = self.db.count_active_user_sessions(
                                        user_id,
                                        exclude_session_id=session.client_session_id,
                                    )
                                    if active_sessions >= user.max_concurrency:
                                        raise RuntimeError(
                                            f"User concurrency limit reached for {user_id}"
                                        )
                                session.status = SessionStatus.ACTIVE
                                if use_account_pool and account:
                                    session.account_id = account.account_id
                                self.db.save_session(session)
                        else:
                            session.status = SessionStatus.ACTIVE
                            if use_account_pool and account:
                                session.account_id = account.account_id
                            self.db.save_session(session)
                        
                        # Add retry hint for mock/telemetry
                        current_messages = messages
                        if attempt > 0:
                            current_messages = messages + [Message(role="system", content=f"UAG_RETRY_ATTEMPT={attempt}")]

                        try:
                            execution_started = perf_counter()
                            actor = f"user:{user_id}" if user_id else "system:orchestrator"
                            record_event(
                                "adapter.execution.started",
                                component=f"adapter.{options.provider.value}",
                                db=self.db,
                                attributes={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": session.backend_id or None,
                                    "workspace_root": session.cwd_path,
                                    "manual_mode": options.manual_mode,
                                    "attempt": attempt,
                                    "message_count": len(current_messages),
                                },
                            )
                            self.db.record_audit(
                                actor=actor,
                                action="adapter.execution.started",
                                target_type="session",
                                target_id=session.client_session_id,
                                after={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": session.backend_id or None,
                                    "workspace_root": session.cwd_path,
                                    "manual_mode": options.manual_mode,
                                    "attempt": attempt,
                                    "message_count": len(current_messages),
                                },
                            )
                            async with start_span(
                                "adapter.send_turn",
                                component=f"adapter.{options.provider.value}",
                                db=self.db,
                                attributes={
                                    "provider": options.provider.value,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "attempt": attempt,
                                },
                            ):
                                turn_result = await adapter.send_turn(session, current_messages, resolved_provider_model)
                            duration_ms = round((perf_counter() - execution_started) * 1000, 2)
                            record_event(
                                "adapter.execution.completed",
                                component=f"adapter.{options.provider.value}",
                                db=self.db,
                                status="ok",
                                attributes={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": turn_result.backend_id,
                                    "finish_reason": turn_result.finish_reason,
                                    "reported_context_tokens": turn_result.context_tokens,
                                    "duration_ms": duration_ms,
                                    "attempt": attempt,
                                },
                            )
                            self.db.record_audit(
                                actor=actor,
                                action="adapter.execution.completed",
                                target_type="session",
                                target_id=session.client_session_id,
                                after={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": turn_result.backend_id,
                                    "finish_reason": turn_result.finish_reason,
                                    "reported_context_tokens": turn_result.context_tokens,
                                    "duration_ms": duration_ms,
                                    "attempt": attempt,
                                },
                            )
                        except Exception as e:
                            duration_ms = round((perf_counter() - execution_started) * 1000, 2)
                            record_event(
                                "adapter.execution.failed",
                                component=f"adapter.{options.provider.value}",
                                db=self.db,
                                level="ERROR",
                                status="error",
                                attributes={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": session.backend_id or None,
                                    "duration_ms": duration_ms,
                                    "attempt": attempt,
                                    "error": str(e),
                                },
                            )
                            self.db.record_audit(
                                actor=actor,
                                action="adapter.execution.failed",
                                target_type="session",
                                target_id=session.client_session_id,
                                after={
                                    "provider": options.provider.value,
                                    "provider_model": resolved_provider_model,
                                    "adapter": adapter.__class__.__name__,
                                    "account_id": session.account_id,
                                    "backend_id": session.backend_id or None,
                                    "duration_ms": duration_ms,
                                    "attempt": attempt,
                                    "error": str(e),
                                },
                            )
                            # Check for 429 (Rate Limit)
                            err_msg = str(e).lower()
                            if use_account_pool and account and ("429" in err_msg or "rate limit" in err_msg or "rate_limit" in err_msg):
                                previous_account_id = account.account_id
                                self.account_pool.mark_429(account.account_id)
                                # Try to get a new account
                                next_account = self.account_pool.acquire_account(options.provider)
                                if next_account:
                                    if (
                                        options.provider == ProviderType.CODEX
                                        and isinstance(adapter, CodexAdapter)
                                        and session.backend_id
                                    ):
                                        adapter.sync_account_session_state(previous_account_id, next_account.account_id)
                                    account = next_account
                                    session.account_id = next_account.account_id
                                    attempt += 1
                                    continue # Retry with next account
                            # If not 429 or no more accounts, raise to be caught by outer try
                            raise e

                        atr = ATRModule()
                        actions = atr.extract_actions(turn_result.output)

                        # 8. Workspace Diff (Skip in manual_mode)
                        if not options.manual_mode:
                            modified_files, diff = ws_engine.generate_diff()
                            turn_result.modified_files = modified_files
                            turn_result.diff = diff
                            if diff:
                                actions.extend(atr.extract_actions(f"```diff\n{diff}\n```"))
                        turn_result.actions = self._dedupe_actions(actions)

                        # 9. Update Session State
                        # Calculate token delta for accurate usage recording if context_tokens are cumulative
                        current_cumulative = turn_result.context_tokens or 0
                        tokens_delta = max(0, current_cumulative - session.last_context_tokens)
                        
                        session.backend_id = turn_result.backend_id
                        session.status = SessionStatus.IDLE
                        session.updated_at = datetime.now()
                        session.expires_at = datetime.now() + timedelta(hours=24)
                        session.prefix_hash = prefix_hash
                        session.last_context_tokens = current_cumulative
                        self.db.save_session(session)
                        
                        # 10. Release Account (update usage)
                        if use_account_pool and account:
                            self.account_pool.release_account(account.account_id, tokens_used=tokens_delta)
                        
                        # 11. Record Turn History
                        user_id = options.user_id or session.user_id
                        self.db.record_turn(
                            turn_id=f"trn_{uuid4().hex[:12]}",
                            session_id=session.client_session_id,
                            user_id=user_id,
                            provider=options.provider.value,
                            account_id=session.account_id, # Use session's account_id
                            input_tokens=0, # In production, count from compressed_messages
                            output_tokens=tokens_delta,
                            finish_reason=turn_result.finish_reason,
                            duration_ms=0, # Should be calculated
                            diff=turn_result.diff,
                            actions=turn_result.actions
                        )
                        if user_id:
                            self.db.record_user_usage(
                                user_id=user_id,
                                provider=options.provider,
                                input_tokens=0,
                                output_tokens=tokens_delta,
                                cache_hit_tokens=0,
                                request_count=1,
                            )
                        record_event(
                            "orchestrator.request.completed",
                            component="orchestrator",
                            db=self.db,
                            status="ok",
                            attributes={
                                "session_id": session.client_session_id,
                                "provider": options.provider.value,
                                "finish_reason": turn_result.finish_reason,
                                "output_tokens": tokens_delta,
                                "dirty": turn_result.dirty,
                            },
                        )

                        return turn_result

                    except Exception as e:
                        # If we used a 'continue', we don't reach here for 429s that have next accounts
                        if attempt >= max_retries - 1:
                            session.status = SessionStatus.DIRTY
                            self.db.save_session(session)
                            record_event(
                                "orchestrator.request.failed",
                                component="orchestrator",
                                db=self.db,
                                level="ERROR",
                                status="error",
                                attributes={
                                    "session_id": session.client_session_id,
                                    "provider": options.provider.value,
                                    "attempt": attempt,
                                    "error": str(e),
                                },
                            )
                            raise e
                        attempt += 1
                    finally:
                        ws_engine.release_lock()

    def _get_adapter(self, provider: ProviderType) -> ProviderAdapter:
        adapter = self._adapters.get(provider)
        if adapter is not None:
            return adapter
        if provider == ProviderType.CODEX:
            adapter = CodexAdapter(db_manager=self.db)
        elif provider == ProviderType.GEMINI:
            adapter = GeminiAdapter()
        elif provider == ProviderType.OPENCODE:
            adapter = OpenCodeAdapter()
        else:
            raise NotImplementedError(f"Adapter for {provider} not implemented")
        self._adapters[provider] = adapter
        return adapter
