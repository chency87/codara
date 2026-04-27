import asyncio
import hashlib
import json
import os
from time import perf_counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4
from pathlib import Path

from codara.core.models import (
    Message, UagOptions, TurnResult, Session, SessionStatus, 
    ProviderType, Workspace, Task,
    User, UserStatus
)
from codara.database.manager import DatabaseManager
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
        workspace_id: Optional[str] = None,
    ) -> TurnResult:
        # 1. Workspace Lookup
        if not workspace_id:
             workspace_id = options.workspace_id or "default"
        
        workspace = self.db.get_workspace_v2(workspace_id)
        if not workspace:
             # Try fallback for "default" if it was implicit
             if workspace_id == "default":
                  # See if we can find it by name for this user.
                  all_w = self.db.list_workspaces_v2(user_id=options.user_id)
                  for w in all_w:
                       if w.name == "default":
                            workspace = w
                            break
                  
                  if not workspace:
                       # Auto-create "default" workspace if it doesn't exist and we have enough info
                       user_id = options.user_id or "system-user"
                       # Ensure user exists for foreign key
                       if not self.db.get_user(user_id):
                            self.db.save_user(User(
                                 user_id=user_id,
                                 email=f"{user_id}@example.com",
                                 display_name=user_id,
                                 status=UserStatus.ACTIVE,
                                 workspace_path=str(Path(options.workspace_root or "workspaces") / user_id),
                                 created_at=datetime.now(timezone.utc),
                                 created_by="orchestrator",
                                 updated_at=datetime.now(timezone.utc)
                            ))
                       
                       path = options.workspace_root or str(Path("workspaces") / user_id / "default")
                       Path(path).mkdir(parents=True, exist_ok=True)
                       
                       now = datetime.now(timezone.utc)
                       workspace = Workspace(
                            workspace_id=workspace_id,
                            name="default",
                            path=path,
                            user_id=user_id,
                            created_at=now,
                            updated_at=now,
                       )
                       self.db.save_workspace(workspace)
        
        if not workspace:
             raise ValueError(f"Workspace {workspace_id} not found")

        workspace_root = workspace.path
        
        # 2. Session Lookup
        client_session_id = options.client_session_id
        session: Optional[Session] = None
        if client_session_id:
             session = self.db.get_session(client_session_id)
        
        if not session:
             session = Session(
                session_id=self.db._generate_ulid_like("ses"),
                workspace_id=workspace.workspace_id,
                client_session_id=client_session_id,
                backend_id="",
                provider=options.provider,
                user_id=workspace.user_id,
                api_key_id=options.api_key_id,
                cwd_path=workspace.path,
                status=SessionStatus.IDLE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
             )
             self.db.save_session(session)
        elif session.provider != options.provider:
             # Provider changed, must reset backend_id and other state
             session.provider = options.provider
             session.backend_id = ""
             session.status = SessionStatus.IDLE
             session.updated_at = datetime.now(timezone.utc)
             self.db.save_session(session)

        session_id = session.session_id
        resolved_provider_model = resolve_provider_model(options.provider, provider_model)
        
        async with self._get_session_lock(session_id):
            # Refresh session from DB after lock
            session = self.db.get_session(session_id)
            
            # 3. Task Creation
            prompt = messages[-1].content if messages else ""
            task = Task(
                task_id=self.db._generate_ulid_like("tsk"),
                session_id=session_id,
                workspace_id=workspace.workspace_id,
                user_id=session.user_id,
                prompt=prompt,
                status="running",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            self.db.save_task(task)

            # 4. Workspace Engine Setup
            ws_engine = WorkspaceEngine(workspace_root)
            is_git_repo = ws_engine.is_git_repo()
            
            # --- Dispatch (Semaphore gated) ---
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
                        user_id = session.user_id
                        async with self._get_user_lock(user_id):
                            user = self.db.get_user(user_id)
                            if user:
                                active_sessions = self.db.count_active_user_sessions(
                                    user_id,
                                    exclude_session_id=session.session_id,
                                )
                                if active_sessions >= user.max_concurrency:
                                    raise RuntimeError(
                                        f"User concurrency limit reached for {user_id}"
                                    )
                            session.status = SessionStatus.ACTIVE
                            self.db.save_session(session)
                        
                        # Add retry hint for mock/telemetry
                        current_messages = messages
                        if attempt > 0:
                            current_messages = messages + [Message(role="system", content=f"UAG_RETRY_ATTEMPT={attempt}")]

                        try:
                            execution_started = perf_counter()
                            actor = f"user:{user_id}"
                            async with start_span(
                                "adapter.send_turn",
                                component=f"adapter.{options.provider.value}",
                                db=self.db,
                                attributes={
                                    "provider": options.provider.value,
                                    "adapter": adapter.__class__.__name__,
                                    "attempt": attempt,
                                },
                            ):
                                turn_result = await adapter.send_turn(session, current_messages, resolved_provider_model)
                            duration_ms = round((perf_counter() - execution_started) * 1000, 2)
                        except Exception as e:
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
                        session.backend_id = turn_result.backend_id
                        session.status = SessionStatus.IDLE
                        session.updated_at = datetime.now(timezone.utc)
                        session.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
                        self.db.save_session(session)
                        
                        # 10. Update Task State
                        task.status = "completed"
                        task.result = turn_result
                        task.updated_at = datetime.now(timezone.utc)
                        self.db.save_task(task)

                        # 11. Record Turn History
                        self.db.record_turn(
                            turn_id=f"trn_{uuid4().hex[:12]}",
                            session_id=session.session_id,
                            user_id=user_id,
                            provider=options.provider.value,
                            finish_reason=turn_result.finish_reason,
                            duration_ms=int(duration_ms),
                            diff=turn_result.diff,
                            actions=turn_result.actions
                        )
                        return turn_result

                    except Exception as e:
                        if attempt >= max_retries - 1:
                            session.status = SessionStatus.DIRTY
                            self.db.save_session(session)
                            task.status = "failed"
                            task.updated_at = datetime.now(timezone.utc)
                            self.db.save_task(task)
                            raise e
                        attempt += 1
                    finally:
                        ws_engine.release_lock()

    def _get_adapter(self, provider: ProviderType) -> ProviderAdapter:
        adapter = self._adapters.get(provider)
        if adapter is not None:
            return adapter
        if provider == ProviderType.CODEX:
            adapter = CodexAdapter()
        elif provider == ProviderType.GEMINI:
            adapter = GeminiAdapter()
        elif provider == ProviderType.OPENCODE:
            adapter = OpenCodeAdapter()
        else:
            raise NotImplementedError(f"Adapter for {provider} not implemented")
        self._adapters[provider] = adapter
        return adapter
