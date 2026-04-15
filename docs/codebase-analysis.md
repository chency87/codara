# Codebase Analysis Report

**Date:** 2026-04-14  
**Analysis Scope:** Documentation alignment, bug detection, and performance analysis

> Status note: this report is a point-in-time triage document. Compression references are historical, and the lock findings for BUG-1 and BUG-3 were addressed in the runtime cleanup on 2026-04-14.

---

## 1. Documentation vs Codebase Alignment

### 1.1 Architecture Documentation

| Documented Behavior | Implementation Status | Notes |
|---------------------|----------------------|-------|
| Middle-Out Compression at 50% context | ❌ Removed | Compression was stubbed and has since been deleted from the active runtime |
| Prefix Hash Calculation | ⚠️ Partial | `engine.py:58` only hashes file tree metadata, not system prompt (doc says `SHA256(system_prompt \|\| file_tree_metadata)`) |
| ATR Module | ✅ Implemented | `atr.py` exists |
| Session locks per client_session_id | ✅ Implemented | `orchestrator/engine.py:30-33` |
| Global semaphore (max 10) | ✅ Implemented | `orchestrator/engine.py:26` |
| Workspace lock via `.uag_lock` | ✅ Implemented | `workspace/engine.py:105-119` |

### 1.2 Technical Documentation Discrepancies

1. **Concurrency Model (docs line 176):** States "Per-Session Binding: Each `client_session_id` bound to specific worker"
   - **Reality:** The code uses in-memory `asyncio.Lock` per session (`engine.py:30-33`). This works for single-process deployments but would fail in multi-process deployments (documented production architecture shows multiple UAG nodes).

2. **Database Schema (docs line 590):** Shows `sessions.status TEXT NOT NULL DEFAULT 'idle'`
   - **Reality:** Code uses `SessionStatus.IDLE` enum but stores as string - OK

3. **Documentation states session expires_at tracking** but code creates sessions without proper expiration checking on resume.

---

## 2. Identified Bugs

### 2.1 Critical Bugs

#### BUG-1: Race Condition in Session Lock Creation
**Location:** `orchestrator/engine.py:30-33`
```python
def _get_session_lock(self, session_id: str) -> asyncio.Lock:
    return self._session_locks.setdefault(session_id, asyncio.Lock())
```
**Status:** Fixed. The runtime now uses keyed `setdefault()` lock creation.

#### BUG-2: Prefix Hash Missing System Prompt
**Location:** `orchestrator/engine.py:56-58`
```python
tree_metadata = ws_engine.get_file_tree_metadata()
# In a real impl, we'd also include the system prompt in the hash
prefix_hash = hashlib.sha256(tree_metadata.encode()).hexdigest()
```
**Issue:** Documentation states `SHA256(system_prompt || file_tree_metadata)` but code only hashes file tree. This breaks the token cache optimization described in the architecture docs.

#### BUG-3: User Concurrency Check Uses Global Lock
**Location:** Historical `orchestrator/engine.py` path before keyed user locks
```python
async with self._get_user_lock(user_id):
    user = self.db.get_user(user_id)
    if user:
        active_sessions = self.db.count_active_user_sessions(...)
```
**Status:** Fixed. User concurrency checks now use per-user locks so unrelated users no longer serialize through a single global lock.

#### BUG-4: Workspace Snapshot in Manual Mode
**Location:** `orchestrator/engine.py:116-117`
```python
if not options.manual_mode and not is_git_repo:
    ws_engine.take_snapshot()
```
**Issue:** Snapshot is skipped in manual_mode, but if repo IS a git repo, snapshot is still taken in manual_mode. The logic should be:
```python
if not options.manual_mode:
    if not is_git_repo:
        ws_engine.take_snapshot()
```

#### BUG-5: Token Count Set to 0 in Turn Recording
**Location:** `orchestrator/engine.py:208-209`
```python
input_tokens=0,  # In production, count from compressed_messages
output_tokens=tokens_delta,
```
**Issue:** Input tokens are always recorded as 0, breaking usage analytics.

### 2.2 Medium Bugs

#### BUG-6: Database Connection Per Query
**Location:** `database/manager.py:27-31`
```python
def _get_connection(self):
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```
**Issue:** Every database operation creates a new connection. With high concurrency, this adds significant overhead. Should use connection pooling.

#### BUG-7: Auth Caching Not Invalidated on Config Change
**Location:** `gateway/app.py:152-178`
```Operator passkey cached at module level with no invalidation mechanism. If config changes at runtime, cached value becomes stale.

#### BUG-8: Dotenv File Reading on Every Request
**Location:** `gateway/app.py:119-149`
```python
def _dotenv_value(name: str) -> Optional[str]:
    ...
    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
```
**Issue:** Reading `.env` files on every `_dotenv_value()` call. This is called by `_operator_passkey()` which can be invoked per request. Should be cached.

#### BUG-9: Account Pool Query Returns Non-Codex Accounts Empty
**Location:** `database/manager.py:414-437`
```python
def get_all_accounts(self, provider: Optional[str] = None, ...):
    ...
    if provider and provider.lower() != 'codex':
        return [] # Return empty if a non-codex provider is specifically requested
```
**Issue:** This prevents retrieving gemini/opencode accounts from `get_all_accounts()`, but these providers use "system" accounts. The logic contradicts the documented pool design.

---

## 3. Performance Issues Causing Slow Webpage Responses

### 3.1 High-Impact Performance Problems

#### PERF-1: Synchronous File Iteration for Large Workspaces
**Location:** `workspace/engine.py:20-27`
```python
def _iter_workspace_files(self) -> List[Path]:
    files: List[Path] = []
    for root, _, filenames in os.walk(self.workspace_root):
        if ".git" in root:
            continue
        for filename in filenames:
            files.append(Path(root) / filename)
    return files
```
**Impact:** For large workspaces with thousands of files, this is slow. Called during:
- `get_file_tree_metadata()` - for EVERY request to compute prefix_hash
- `take_snapshot()` - to compute file hashes for diff generation

**Recommendation:** Use `os.scandir()` or add filesystem caching with invalidation.

#### PERF-2: Git Commands Spawned Per Request
**Location:** `workspace/engine.py:29-40, 62-88`
```python
def _git_tracked_and_untracked_files(self) -> Optional[List[str]]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=self.workspace_root,
            ...
        )
```
**Impact:** For every request, git commands are spawned. This is expensive. Should cache git file list with hash-based invalidation.

#### PERF-3: SQLite Without WAL Mode
**Location:** `database/manager.py:27-31`
**Impact:** Default SQLite journal mode is rollback, which has poor concurrency. Should use WAL mode:
```python
conn.execute("PRAGMA journal_mode=WAL")
```

#### PERF-4: No Database Query Caching
**Location:** Throughout `database/manager.py`
**Impact:** Every request hits the database. Frequently-read data like user sessions, account status should be cached.

#### PERF-5: File Hash Calculation on Every Diff
**Location:** `workspace/engine.py:91`
```python
def _generate_hash_diff(self) -> Tuple[List[str], Optional[str]]:
    new_snapshot = self.take_snapshot()  # Re-hashes ALL files
```
**Impact:** For non-git repos, every request re-hashes the entire workspace. Should compare only against stored snapshot.

### 3.2 Medium-Impact Performance Problems

#### PERF-6: Synchronous HTTP Calls in Adapter
**Location:** `codex.py:115-125`
```python
async with httpx.AsyncClient(timeout=30.0) as client:
    weekly_resp = await client.get(...)
    hourly_resp = await client.get(...)
```
**Impact:** These usage collection calls happen during account selection but are not awaited asynchronously in the main flow (they block). However, they only run periodically, not per-request.

#### PERF-7: Large Response Serialization
**Location:** `gateway/app.py:516-542`
**Impact:** `_serialize_user()` performs multiple database queries (lines 518-522) for usage aggregation. Should batch these queries.

---

## 4. Summary Matrix

| Category | Count | Critical Impact Issues |
|----------|-------|------------------------|
| Alignment Issues | 3 | 1 (prefix hash) |
| Bugs | 9 | 5 |
| Performance Issues | 7 | 5 |

### Priority Fixes

1. **PERF-1 + PERF-2:** Cache file tree metadata and git outputs
2. **BUG-3:** Remove global user lock bottleneck
3. **PERF-3:** Enable SQLite WAL mode
4. **BUG-1:** Fix session lock race condition
5. **BUG-4:** Review manual-mode workspace snapshot behavior

---

## 5. Stale/Useless Test Functions

The following test functions are stale, invalid, or provide no value:

### 5.1 Completely Empty Tests (just `pass`)

| File | Test Function | Reason |
|------|---------------|--------|
| `test_usage_monitor.py:131-135` | `test_usage_monitor_prefers_oauth_session_token_before_billing_key` | Test is empty, marked invalid in comments - "get_all_accounts is hard-filtered to codex only" |
| `test_usage_monitor.py:139-142` | `test_usage_monitor_uses_oauth_session_token_when_no_billing_key` | Test is empty, marked invalid in comments |
| `test_gemini_usage_e2e.py:14-17` | `test_gemini_usage_uses_cli_stats_session_flow` | Test is empty, marked invalid - "we no longer manage Gemini accounts" |
| `test_gemini_usage_e2e.py:34-35` | `test_gemini_isolation_filesystem_structure` | Test is empty, marked invalid - "Gemini adapter no longer uses isolation" |

### 5.2 Tests That Don't Assert Anything Meaningful

| File | Test Function | Issue |
|------|---------------|-------|
| `test_gemini_usage_e2e.py:39-67` | `test_gemini_usage_returns_none_when_cli_stats_unavailable` | Tests that a non-existent provider returns null usage. This test doesn't actually verify any useful behavior since it relies on a non-existent code path (Gemini accounts aren't managed). |

### 5.3 Tests With Broken Mocking Patterns

| File | Test Function | Issue |
|------|---------------|-------|
| `test_adapters.py:90-100` | `test_gemini_adapter_collect_usage_uses_system_cli_stats` | Calls `adapter.collect_usage()` which returns `None` for Gemini. The test asserts `usage is None` but the adapter logic has changed significantly and doesn't test meaningful behavior. |
| `test_usage_monitor.py:82-128` | `test_usage_monitor_uses_configured_codex_billing_key_for_oauth_accounts` | Uses `FakeClient` that doesn't properly mock httpx - may not work with current adapter code structure |
| `test_usage_monitor.py:131-191` | Multiple tests using FakeClient | The `FakeClient` doesn't properly implement async context manager for current httpx usage patterns |

### 5.4 Summary

- The empty Gemini usage test file and the empty usage-monitor placeholders were removed on 2026-04-14.
- Remaining test concerns should be evaluated case by case; passing tests with active code coverage were kept.

---

## 6. Subprocess Execution Performance Optimization

### 6.1 Current Problem Analysis

**Observed latency:** ~60s for simple commands

The current implementation spawns a new subprocess for every request:
- `codex.py:44-50` - `asyncio.create_subprocess_exec()` for each turn
- `gemini.py:36-42` - Same pattern

**Root causes of slow startup:**
1. **Process spawn overhead** - Every request spawns a new process (fork+exec)
2. **CLI initialization** - Each CLI tool initializes from scratch (auth check, config load, network fetch)
3. **No caching** - No session/credential warming between requests
4. **Isolated env setup** - `ConfigIsolationMixin` creates temp directories per request (Codex)

### 6.2 Optimization Approaches

#### Approach 1: Process Pre-warming (Recommended - High Impact)

Keep a long-lived subprocess running and reuse it for multiple requests.

**Implementation pattern:**
```python
class PrewarmedProcess:
    def __init__(self, command: list[str], cwd: str, env: dict):
        self.command = command
        self.cwd = cwd
        self.env = env
        self._proc: Optional[asyncio.subprocess.Process] = None
    
    async def ensure_running(self):
        if self._proc is None or self._proc.returncode is not None:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
            )
        return self._proc
    
    async def communicate(self, input_data: bytes) -> tuple[bytes, bytes]:
        proc = await self.ensure_running()
        proc.stdin.write(input_data)
        await proc.stdin.drain()
        return await proc.communicate()
```

**Benefits:**
- Eliminates process spawn overhead after first request
- CLI stays warm (auth, config cached)
- Similar to Claude SDK's `startup()` approach

**Considerations:**
- Session state management (backend_id handling)
- Process lifecycle (health checks, restart on failure)
- Memory usage for long-running processes

#### Approach 2: Session Persistence with CLI Resume

Instead of spawning new processes, maintain session continuity:

**Codex:** Use `--resume` to continue existing thread
- Current code already supports this (`codex.py:36-37`)
- Ensure session is always passed

**Gemini:** Use `--resume` with session_id  
- Already implemented (`gemini.py:490-491`)

**Issue:** Current implementation may spawn new processes even when resuming because the session state might not be properly maintained.

#### Approach 3: Reduce Environment Setup Overhead

Current `codex.py:20` runs `setup_isolated_env()` for EVERY request:
```python
temp_dir, env = self.setup_isolated_env("codex", session.account_id)
```

**Optimization:**
- Cache the isolated environment for the same account_id
- Only recreate if credential changes

```python
_env_cache: dict[str, tuple[str, dict]] = {}

def _get_cached_env(self, account_id: str) -> tuple[str, dict]:
    if account_id not in self._env_cache:
        self._env_cache[account_id] = self.setup_isolated_env("codex", account_id)
    return self._env_cache[account_id]
```

#### Approach 4: Lazy CLI Detection

Move CLI availability check out of hot path:

```python
# Instead of checking on every request:
if shutil.which("gemini") is None:  # Called every time!
    raise RuntimeError("...")

# Check once at startup, cache result
_CLI_AVAILABLE = {
    "codex": shutil.which("codex") is not None,
    "gemini": shutil.which("gemini") is not None,
}
```

#### Approach 5: Background Session Initialization

Start the CLI process in background while handling auth/workspace:

```python
async def send_turn(self, session, messages, provider_model):
    # Start CLI warmup in background (non-blocking)
    warmup_task = asyncio.create_task(self._ensure_process_warm(session.account_id))
    
    # Do other preparation in parallel
    prompt = self._messages_to_prompt(messages)
    # ... other setup ...
    
    # Wait for warmup to complete
    await warmup_task
    
    # Now execute with warm process
    return await self._execute_with_warm_process(...)
```

### 6.3 Recommended Implementation Priority

| Priority | Approach | Estimated Impact | Complexity |
|----------|----------|------------------|------------|
| 1 | Process Pre-warming | 20-40s reduction | Medium |
| 2 | Environment caching | 2-5s reduction | Low |
| 3 | CLI availability cache | 0.5-1s reduction | Low |
| 4 | Background warmup | 0-2s reduction | Medium |

### 6.4 Implementation Sketch

```python
# adapters/base.py additions
class ProcessPool:
    def __init__(self, max_size: int = 3):
        self._pools: dict[str, asyncio.Queue] = {}
        self._max_size = max_size
    
    async def acquire(self, key: str, factory: Callable) -> Any:
        if key not in self._pools:
            self._pools[key] = asyncio.Queue(maxsize=self._max_size)
        
        queue = self._pools[key]
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            proc = await factory()
            return proc
    
    async def release(self, key: str, proc):
        try:
            self._pools[key].put_nowait(proc)
        except asyncio.QueueFull:
            proc.terminate()
            await proc.wait()

# In adapter:
async def send_turn(self, session, messages, provider_model):
    pool_key = f"{session.account_id}:{session.cwd_path}"
    
    # Try to get warm process
    proc = await self._process_pool.acquire(
        pool_key, 
        lambda: self._create_warm_process(session)
    )
    
    try:
        result = await self._execute_with_process(proc, session, messages)
    finally:
        # Return process to pool for reuse
        await self._process_pool.release(pool_key, proc)
    
    return result
```

### 6.5 Risks and Mitigations

| Risk | Mitigation |
|------|-------------|
| Stale session state | Add health check; recreate on error |
| Memory leaks | Limit pool size; periodic restart |
| Auth token expiry | Refresh credentials before use |
| Process deadlock | Timeout + force kill |
