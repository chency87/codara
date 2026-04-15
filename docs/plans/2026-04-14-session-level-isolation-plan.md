# Session-Level Isolation and Gemini Auth Fix Plan

> Superseded on 2026-04-14 by account-scoped persistent isolation under `isolated_envs_root`. Pooled managed accounts now reuse one provider/account-scoped home across users instead of per-session homes under each workspace.

## Objective
Fix Gemini authentication failures by aligning token extraction keys, and refactor isolated environment management to support session-level persistence for Codex and Gemini adapters. Also, allow configuring the base directory for these isolated environments.

## Research Summary
- **Gemini Auth**: `src/codara/adapters/base.py` is missing several token extraction keys compared to `src/codara/adapters/gemini.py`.
- **Session Persistence**: Current `setup_isolated_env` uses `tempfile.mkdtemp()` every turn and `send_turn` cleans it up in `finally`. This prevents local session state (like CLI caches or cookies) from persisting across turns.
- **Config Restriction**: Isolated environments are hardcoded to use `tempfile.gettempdir()`. Some environments (like restricted containers) might need a different path.

## Proposed Architecture
- **Settings Enhancement**: Add `isolated_envs_root` to `Settings` to allow custom path configuration.
- **Enhanced Isolation Mixin**: Update `ConfigIsolationMixin` to support deterministic home directories based on `Session.client_session_id`.
- **Stable Adapter Logic**: Update `GeminiAdapter` and `CodexAdapter` to pass the session to the isolation setup and skip cleanup during the turn to maintain persistence.

## Implementation Approach

### 1. Configuration Update
- Update `src/codara/config.py`:
    - Add `isolated_envs_root: Optional[str] = None` to `Settings`.
    - Map it to `UAG_ISOLATED_ENVS_ROOT` in `_FIELD_ENV_MAP`.

### 2. Base Adapter Alignment
- Update `src/codara/adapters/base.py`:
    - Align `_deep_find_token` keys with the comprehensive list from `gemini.py`.
    - Refactor `setup_isolated_env(self, provider_name: str, account_id: str, session: Optional[Session] = None)`:
        - Use `settings.isolated_envs_root` as the base directory if provided, falling back to `tempfile.gettempdir()`.
        - If `session` is provided, use `uag-{provider}-{hashed_session_id}` as the directory name.
        - Ensure directory existence.

### 3. Gemini/Codex Adapter Updates
- Update `src/codara/adapters/gemini.py` and `src/codara/adapters/codex.py`:
    - Pass `session` to `setup_isolated_env`.
    - Update `send_turn` to skip `cleanup_isolated_env(temp_dir)` if a session-level environment was used.
    - Note: Throwaway environments (e.g., for usage collection) should still be cleaned up.

### 4. Cleanup Strategy
- For now, session-level environments will persist until manual deletion or system reboot (if in `/tmp`).
- Future work could integrate cleanup into session expiration logic in the database manager.

## Validation Strategy
- **Failing Test**: Create `tests/test_isolation_persistence.py`.
    - Test 1: Verify that two consecutive `send_turn` calls for the same session use the same `HOME` directory.
    - Test 2: Verify that `isolated_envs_root` configuration is respected.
    - Test 3: Verify Gemini auth works with `accessToken` and `bearer_token` keys in `base.py`.

## Success Criteria
- [ ] Gemini authentication succeeds with varied token keys.
- [ ] `HOME` directory is stable across turns for a given `client_session_id`.
- [ ] `UAG_ISOLATED_ENVS_ROOT` environment variable allows changing the base directory.
- [ ] Automated tests pass.

## Risks and Assumptions
- **Risk**: Concurrent requests for the same session might lead to race conditions when writing auth files.
- **Mitigation**: The orchestrator already uses a per-session lock (`_session_locks`) in `engine.py`.
- **Assumption**: `client_session_id` is unique enough (namespaced by gateway) to avoid collisions between users.

## Sources
- `src/codara/adapters/base.py`
- `src/codara/adapters/gemini.py`
- `src/codara/config.py`
- `src/codara/orchestrator/engine.py`
