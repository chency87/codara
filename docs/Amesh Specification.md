# Amesh Framework — Technical Specification

> Agent Mesh Runtime for ACP & CLI Agents
## Key Innovations: Detailed Design & Data Structures

> This document specifies the six most critical and novel parts of the amesh framework in precise detail. Every data structure, invariant, protocol message, and failure mode is defined here. Developers must read this before implementing any of these modules.

---

## Table of Contents

1. [Typed DAG & Port System](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#1-typed-dag--port-system)
2. [WAL-First State Protocol](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#2-wal-first-state-protocol)
3. [Staging Branch Chain](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#3-staging-branch-chain)
4. [Write-Set Conflict Detection](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#4-write-set-conflict-detection)
5. [Context Assembly & Token Budget](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#5-context-assembly--token-budget)
6. [Sentinel Protocol](https://claude.ai/chat/4385c680-0391-47d5-a5a5-20e21225368f#6-sentinel-protocol)

---

## 1. Typed DAG & Port System

### Motivation

Most agent orchestrators pass untyped strings between agents. This means:

- A node can be dispatched before its inputs are actually ready
- Type mismatches between producers and consumers are caught at runtime
- There is no way to validate a plan before executing it

The port system gives every inter-node data flow a declared type. The scheduler uses port satisfaction as the only dispatch trigger. The validator checks type compatibility at planning time, before any agent runs.

---

### Core invariant

> **A node may only be dispatched when every port in `data_in` is satisfied.** A port is satisfied when a payload of the declared type has been written to its `payload_ref` key in RocksDB.

This is the entire scheduling contract. Everything else is derived from it.

---

### Data structures

```python
# ─────────────────────────────────────────────────────────────
# EdgePayloadType — the type system for inter-node data flow
# ─────────────────────────────────────────────────────────────

class EdgePayloadType(str, Enum):
    INTERFACE_STUB     = "interface_stub"
    # Compressed representation of a module's public API.
    # Contains signatures, types, exports — NO implementation.
    # Produced by: STUB, IMPLEMENT nodes
    # Consumed by: downstream IMPLEMENT nodes that call this module

    GIT_DIFF           = "git_diff"
    # Full unified diff of all changes in a node's worktree.
    # Stored in RocksDB but NOT injected into downstream agent contexts.
    # Used only by: MergePipeline, ReviewNode, audit trail
    # Produced by: any node that modifies files
    # Consumed by: MERGE node, REVIEW node

    TEST_REPORT        = "test_report"
    # Structured test results from a TEST node.
    # Produced by: TEST nodes
    # Consumed by: REVIEW nodes, final MERGE node

    CLARIFICATION_REQ  = "clarification_request"
    # A question an agent needs answered before continuing.
    # Produced by: any node emitting SENTINEL:CONTROL:blocked
    # Consumed by: TelegramChannel, which resolves it to CLARIFICATION_RESP

    CLARIFICATION_RESP = "clarification_response"
    # Human answer to a clarification request.
    # Produced by: TelegramChannel on human reply
    # Consumed by: the blocked node when it resumes

    WORKSPACE_SNAPSHOT = "workspace_snapshot"
    # A git tree hash pointing to a specific staging state.
    # Used to tell a downstream node exactly which branch to check out from.
    # Produced by: MergePipeline after staging merge
    # Consumed by: WorktreeManager when creating downstream worktrees

    TYPE_SIGNATURES    = "type_signatures"
    # Subset of INTERFACE_STUB: only type definitions (no function stubs).
    # Used for nodes that need type information but not full API surface.

    ERROR_LOG          = "error_log"
    # Structured error output from a failed node.
    # Produced by: any node on failure path
    # Consumed by: retry logic, human escalation
```

```python
# ─────────────────────────────────────────────────────────────
# Port — one typed input or output slot on a node
# ─────────────────────────────────────────────────────────────

class Port(BaseModel):
    name:         str
    # Unique within a node's data_in or data_out list.
    # Naming convention: "{source_node_id}_{payload_type}"
    # Example: "auth_stub" for an interface stub from the auth node

    payload_type: EdgePayloadType
    # The declared type. Must match the EdgePayloadType on the Edge
    # connecting this port to its producer/consumer.

    satisfied:    bool = False
    # True when payload_ref has been written by the MergePipeline.
    # The scheduler checks this field to determine dispatch readiness.
    # NEVER set this directly — always go through StateStore.satisfy_port()

    payload_ref:  Optional[str] = None
    # RocksDB key where the payload content is stored.
    # Format: "payload:{dag_id}:{producer_node_id}:{port_name}"
    # Example: "payload:dag123:auth_impl:stub_out"
    # Set atomically alongside satisfied=True by StateStore.satisfy_port()
```

```python
# ─────────────────────────────────────────────────────────────
# Edge — a directed typed data flow between two nodes
# ─────────────────────────────────────────────────────────────

class Edge(BaseModel):
    src:          str
    # node.id of the producer

    dst:          str
    # node.id of the consumer

    payload_type: EdgePayloadType
    # Must match: src.data_out[*].payload_type AND dst.data_in[*].payload_type
    # Validated by Dag._check_edge_types() at construction time

    payload_ref:  Optional[str] = None
    # Populated by MergePipeline after src node completes.
    # Points to same RocksDB key as the dst port's payload_ref.
```

```python
# ─────────────────────────────────────────────────────────────
# Node — a unit of work executed by one agent
# ─────────────────────────────────────────────────────────────

class Node(BaseModel):
    id:   str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    # Short unique ID within a DAG. Used in branch names, RocksDB keys,
    # worktree paths. Keep short — it appears everywhere.
    # Format: 8 hex chars from UUID4. Example: "a3f2b1c9"

    node_type: NodeType
    # Determines: which agents can handle it (see AgentRouter affinities)
    # and which payload types it is expected to produce.
    #
    # NodeType → expected data_out payload_types:
    #   STUB        → [INTERFACE_STUB]
    #   IMPLEMENT   → [GIT_DIFF, INTERFACE_STUB]
    #   TEST        → [TEST_REPORT]
    #   REVIEW      → [GIT_DIFF]  (review comments as diff annotations)
    #   MERGE       → []          (no output, terminal node)
    #   CLARIFY     → [CLARIFICATION_RESP]

    agent: AgentKind = AgentKind.AUTO
    # AUTO = scheduler picks based on AFFINITIES map.
    # Override only when you have a specific reason (e.g. long context → Gemini)

    status: NodeStatus = NodeStatus.PENDING
    # State machine:
    #
    #   PENDING ──(all ports satisfied)──► READY
    #   READY   ──(scheduler dispatches)──► RUNNING
    #   RUNNING ──(SENTINEL:CONTROL:success)──► DONE
    #   RUNNING ──(SENTINEL:CONTROL:failure)──► FAILED
    #   RUNNING ──(SENTINEL:CONTROL:blocked)──► BLOCKED
    #   FAILED  ──(retry_count < max_retries)──► PENDING
    #   BLOCKED ──(clarification received)──► RUNNING
    #
    # ONLY StateStore may transition this field.
    # Direct assignment anywhere else is a bug.

    token_budget: int = 50_000
    # Maximum tokens this node's context may consume.
    # ContextBuilder enforces this before dispatch.
    # If estimated context exceeds this, the planner must re-decompose.
    # Rough estimation: 1 token ≈ 4 characters of UTF-8 text.

    write_set: list[str] = []
    # DECLARED list of file paths this node intends to modify.
    # Must be declared by the planner — the scheduler uses this
    # for conflict detection BEFORE dispatch (not after).
    # Paths are relative to repo root. Example: ["src/auth/mod.py"]
    #
    # CRITICAL: if a node modifies a file not in write_set, the
    # MergePipeline will detect this and fail the node.
    # Agents are told their write_set in their context prompt.

    data_in:  list[Port] = []
    # Input ports. Each must be satisfied before dispatch.
    # Port names must be unique within data_in.

    data_out: list[Port] = []
    # Output ports. Populated by MergePipeline after node completes.
    # Port names must be unique within data_out.

    worktree_ref:       Optional[str] = None
    # Absolute filesystem path to this node's git worktree.
    # Set by WorktreeManager.create() at dispatch time.
    # Example: "/tmp/amesh/worktrees/a3f2b1c9"

    base_branch:        Optional[str] = None
    # The branch this node's worktree was checked out from.
    # Set by StagingManager.get_base_branch() at dispatch time.
    # For leaf nodes (no deps): "main"
    # For other nodes: "staging/after_{dep_node_id}"

    staging_ref:        Optional[str] = None
    # The staging branch created after this node's diff was merged.
    # Set by MergePipeline.on_node_complete().
    # Format: "staging/after_{node_id}"
    # Downstream nodes use this as their base_branch.

    critical_path_cost: float = 0.0
    # Estimated total token cost from this node to the terminal node.
    # Computed by CriticalPathEstimator before scheduling.
    # Higher = higher priority. Nodes on the critical path are
    # dispatched before nodes that are not.

    retry_count: int = 0
    max_retries: int = 3
    # On FAILED → if retry_count < max_retries: increment and reset to PENDING
    # On FAILED → if retry_count >= max_retries: escalate to human
```

---

### Port satisfaction protocol

The scheduler's readiness check is the inner loop of the entire system. It must be correct and efficient.

```
RocksDB key schema for port state:

  port:{dag_id}:{node_id}:{port_name}
    HASH fields:
      satisfied:   "0" | "1"
      payload_ref: "payload:{dag_id}:{producer_id}:{port_name}" | ""

  Example:
    port:dag123:main_impl:auth_stub
      satisfied:   "1"
      payload_ref: "payload:dag123:auth_stub_node:stub_out"
```

```python
# Correct readiness check — checks RocksDB, not in-memory Node object
async def is_node_ready(r: RocksDB.RocksDB, dag_id: str, node: Node) -> bool:
    """
    A node is ready iff ALL of its data_in ports are satisfied.
    Uses RocksDB as the source of truth, not the in-memory Node object,
    because port satisfaction happens in a different process/coroutine.
    """
    if node.status != NodeStatus.PENDING:
        return False
    for port in node.data_in:
        key = f"port:{dag_id}:{node.id}:{port.name}"
        val = await r.hget(key, "satisfied")
        if val != b"1":
            return False
    return True

# WRONG — do not use:
# def is_node_ready(node: Node) -> bool:
#     return all(p.satisfied for p in node.data_in)
# This reads stale in-memory state. The authoritative state is in RocksDB.
```

---

### Validation — what must be checked at planning time

```python
def validate_dag(dag: Dag) -> None:
    """
    Run all four constraint checks. Raise ValueError on first failure.
    Called by Dag.__init__ via model_validator.
    NEVER skip this — invalid DAGs cause silent corruption at runtime.
    """

    # 1. Acyclicity
    # A cycle means a node transitively depends on itself.
    # Result: infinite scheduling loop, never completes.
    _check_acyclic(dag)

    # 2. Edge type compatibility
    # Every edge must connect a producer port and consumer port
    # with the same EdgePayloadType.
    # Result of violation: scheduler satisfies a port with wrong payload type,
    # agent receives malformed context.
    _check_edge_types(dag)

    # 3. Write-set disjointness for parallel nodes
    # Two nodes that can run in parallel (neither is an ancestor of the other)
    # must not declare overlapping write sets.
    # Result of violation: two agents edit the same file simultaneously,
    # producing an unresolvable merge conflict.
    # NOTE: Check at planning time. Do NOT rely on merge conflict detection.
    _check_write_sets(dag)

    # 4. Token budget feasibility
    # Each node's data_in payload sizes must fit within its token_budget.
    # This is an estimate — exact sizes are unknown at plan time.
    # Use conservative upper bounds.
    # Result of violation: ContextBuilder truncates context, agent produces
    # incomplete or incorrect output.
    _check_token_budgets(dag)
    
    # 5. ideally check if any file in a node's `read_set` (if you track that) is in an upstream node's `write_set`
```

---

### Example: correct DAG for a two-module task

```
Task: "Add JWT authentication to the API"

Files involved:
  src/auth/jwt.py       ← new module (no existing deps)
  src/api/routes.py     ← depends on auth/jwt.py
  tests/test_auth.py    ← tests for auth/jwt.py
  tests/test_routes.py  ← tests for api/routes.py

DAG:

  [stub_auth]  ─── InterfaceStub ──► [impl_routes]
      │                                    │
      │ InterfaceStub                      │ GIT_DIFF
      ▼                                    ▼
  [impl_auth]                         [test_routes]
      │                                    │
      │ GIT_DIFF                           │ TEST_REPORT
      ▼                                    ▼
  [test_auth]                          [merge]
      │                                    ▲
      └─────────── TEST_REPORT ────────────┘

Nodes:
  stub_auth:
    node_type: STUB
    write_set: ["src/auth/jwt.py"]   ← emits only stubs, no impl
    data_in:   []
    data_out:  [Port(name="stub_out", payload_type=INTERFACE_STUB)]

  impl_auth:
    node_type: IMPLEMENT
    write_set: ["src/auth/jwt.py"]
    data_in:   [Port(name="stub_in", payload_type=INTERFACE_STUB)]
               ← waits for stub_auth to define the API contract
    data_out:  [Port(name="diff_out", payload_type=GIT_DIFF)]

  impl_routes:
    node_type: IMPLEMENT
    write_set: ["src/api/routes.py"]
    data_in:   [Port(name="auth_stub", payload_type=INTERFACE_STUB)]
               ← waits for stub_auth, NOT impl_auth
               ← routes only needs the interface, not the implementation
    data_out:  [Port(name="diff_out", payload_type=GIT_DIFF)]

  PARALLEL: impl_auth and impl_routes can run simultaneously
  because:
    - neither is an ancestor of the other ✓
    - write sets are disjoint:
        {"src/auth/jwt.py"} ∩ {"src/api/routes.py"} = ∅ ✓

  test_auth:
    node_type: TEST
    write_set: ["tests/test_auth.py"]
    data_in:   [Port(name="impl_diff", payload_type=GIT_DIFF)]
    data_out:  [Port(name="report_out", payload_type=TEST_REPORT)]

  test_routes:
    node_type: TEST
    write_set: ["tests/test_routes.py"]
    data_in:   [Port(name="impl_diff", payload_type=GIT_DIFF)]
    data_out:  [Port(name="report_out", payload_type=TEST_REPORT)]

  PARALLEL: test_auth and test_routes can run simultaneously ✓

  merge:
    node_type: MERGE
    write_set: []
    data_in:
      [Port(name="auth_test",   payload_type=TEST_REPORT),
       Port(name="routes_test", payload_type=TEST_REPORT)]
    data_out: []
```

---

## 2. WAL-First State Protocol

### Motivation

Without a WAL, a crash between "agent completes" and "state written to RocksDB" loses work permanently. The system cannot distinguish "node never ran" from "node ran but crash prevented recording." Retrying blindly may corrupt the workspace.

The WAL provides a durable, ordered, append-only record of every state transition. On restart, the system replays the WAL to reconstruct exact state.

---

### Core invariant

> **Every state mutation must be preceded by a WAL append.** If you write to RocksDB before writing to WAL, you have violated the protocol. The WAL entry is the intent. The RocksDB write is the effect. On crash, uncommitted effects are detected and corrected via replay.

---

### WAL entry schema

```
RocksDB Stream: "amesh:wal"
Stream entry fields (all strings):

  event:     WalEvent value (see enum below)
  dag_id:    DAG identifier
  timestamp: ISO 8601 UTC datetime
  data:      JSON-encoded event-specific payload (see per-event schemas)

Example raw RocksDB entry:
  1704067200000-0 →
    event:     "node_dispatched"
    dag_id:    "dag123"
    timestamp: "2024-01-01T00:00:00.000Z"
    data:      '{"node_id":"a3f2b1c9","agent":"codex","worktree":"/tmp/amesh/worktrees/a3f2b1c9"}'
```

```python
class WalEvent(str, Enum):
    DAG_CREATED       = "dag_created"
    NODE_CREATED      = "node_created"
    NODE_DISPATCHED   = "node_dispatched"
    PORT_SATISFIED    = "port_satisfied"
    NODE_COMPLETED    = "node_completed"
    NODE_FAILED       = "node_failed"
    NODE_RETRYING     = "node_retrying"
    NODE_BLOCKED      = "node_blocked"
    NODE_RESUMED      = "node_resumed"
    MERGE_STARTED     = "merge_started"
    MERGE_COMPLETED   = "merge_completed"
    CONFLICT_DETECTED = "conflict_detected"
    DAG_COMPLETE      = "dag_complete"
```

---

### Per-event data payloads

```python
# Every event's "data" field must conform to one of these schemas.
# These are not Pydantic models — they are plain dicts serialized to JSON.
# Keep them minimal. The WAL is append-only and must be compact.

WAL_SCHEMAS = {

    "dag_created": {
        "dag_id":    str,   # same as top-level dag_id
        "task":      str,   # original task text
        "node_ids":  list,  # [str] — all node IDs in this DAG
    },

    "node_dispatched": {
        "node_id":   str,
        "agent":     str,   # AgentKind value
        "worktree":  str,   # absolute path to worktree
    },

    "port_satisfied": {
        "node_id":     str,
        "port_name":   str,
        "payload_ref": str,  # RocksDB key where payload is stored
    },

    "node_completed": {
        "node_id":     str,
        "diff_ref":    str,  # RocksDB key where full diff text is stored
        "staging_ref": str,  # git branch name: "staging/after_{node_id}"
        "tokens_used": int,  # actual tokens consumed (from agent response)
    },

    "node_failed": {
        "node_id":     str,
        "error":       str,  # error message or category
        "retry_count": int,  # how many times this node has been retried
    },

    "node_blocked": {
        "node_id":     str,
        "question":    str,  # what the agent needs clarified
        "chat_id":     int,  # Telegram chat ID to send clarification to
    },

    "node_resumed": {
        "node_id":     str,
        "answer":      str,  # human's clarification answer
    },

    "merge_completed": {
        "node_id":     str,
        "staging_ref": str,
        "stubs":       dict,  # {module_path: stub_text} extracted from diff
    },

    "conflict_detected": {
        "node_id":          str,
        "conflicting_node": str,   # other node involved in conflict
        "files":            list,  # [str] conflicting file paths
        "conflict_type":    str,   # "interface" | "logic" | "style"
    },

    "dag_complete": {
        "final_branch": str,  # the staging branch containing all changes
        "pr_url":       str,  # GitHub PR URL if created
        "total_tokens": int,
        "duration_sec": float,
    },
}
```

---

### Correct write sequence

Every state mutation follows this exact three-step sequence. No exceptions. No shortcuts.

```python
# ✅ CORRECT — WAL first, then RocksDB
async def dispatch_node(dag_id, node_id, agent, worktree):
    # Step 1: Write intent to WAL (durable)
    await wal.append(WalEvent.NODE_DISPATCHED, dag_id,
                     node_id=node_id, agent=agent, worktree=worktree)

    # Step 2: Write effect to RocksDB (may not complete if crash occurs here)
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(f"node:{dag_id}:{node_id}", mapping={
            "status":  "running",
            "agent":   agent,
            "worktree": worktree,
        })
        pipe.sadd(f"dag:{dag_id}:running", node_id)
        pipe.srem(f"dag:{dag_id}:ready",   node_id)
        await pipe.execute()

    # Step 3: In-memory update (last — purely for performance)
    dag.nodes[node_id].status = NodeStatus.RUNNING


# ❌ WRONG — RocksDB before WAL
async def dispatch_node_wrong(dag_id, node_id, agent, worktree):
    await r.hset(f"node:{dag_id}:{node_id}", "status", "running")  # WRONG ORDER
    await wal.append(WalEvent.NODE_DISPATCHED, dag_id, ...)         # too late
```

---

### Recovery procedure

On any startup (normal or after crash), the recovery procedure runs before any new work begins.

```python
async def recover_dag(dag_id: str, wal: WAL, store: StateStore,
                       worktrees: WorktreeManager) -> Dag:
    """
    Replay WAL for this dag_id to reconstruct consistent state.
    Returns the DAG in its last known good state.
    """
    events = await wal.replay()
    dag_events = [e for e in events if e["dag_id"] == dag_id]

    dag = None

    for event in dag_events:
        e = event["event"]
        d = json.loads(event["data"])

        if e == "dag_created":
            dag = load_dag_schema(dag_id)  # load from persistent store

        elif e == "node_dispatched":
            # Node was dispatched. Check if it completed.
            completed = await store.r.hget(
                f"node:{dag_id}:{d['node_id']}", "status"
            )
            if completed != b"done":
                # Node was running when crash occurred.
                # The worktree may or may not exist.
                # Mark as FAILED so it retries with a fresh worktree.
                await store.fail_node(dag_id, d["node_id"],
                                       "recovered_from_crash")
                # Clean up any orphaned worktree.
                worktrees.cleanup(d["node_id"])

        elif e == "merge_started":
            # Merge was in progress. The staging branch may be in a
            # partially merged state. Delete it and let MergePipeline redo it.
            try:
                store.r.git("branch", "-D", f"staging/after_{d['node_id']}")
            except Exception:
                pass  # branch didn't exist yet, no problem

        elif e == "node_completed":
            # Node completed successfully. Ensure RocksDB reflects this.
            await store.r.hset(f"node:{dag_id}:{d['node_id']}",
                                "status", "done")

    return dag


# On every startup:
async def startup():
    active_dag_ids = await r.smembers("amesh:active_dags")
    for dag_id in active_dag_ids:
        dag = await recover_dag(dag_id.decode(), wal, store, worktrees)
        if dag and not dag.is_complete():
            asyncio.create_task(run_dag(dag))
```

---

## 3. Staging Branch Chain

### Motivation

This is the most critical innovation. Existing tools create a worktree per agent branching from `main`, then merge all branches back to `main` at the end. This means parallel agents are completely blind to each other's outputs during execution. If agent A adds a new function and agent B needs to call that function, B will produce incorrect code because it cannot see A's output.

The staging chain solves this: each node's worktree branches from the **merged output of all its direct dependencies**. Downstream agents always see upstream outputs before starting.

---

### Core invariant

> **A node's worktree must branch from a staging ref that contains the committed output of ALL nodes it depends on — transitively.**
> 
> If node B depends on node A, then: B.base_branch must be a descendant of A.staging_ref

---

### Branch naming convention

```
main                        ← production branch, never touched during execution
  │
  └── node/{node_id}        ← agent's working branch (ephemeral, deleted after merge)
        │
        └── staging/after_{node_id}   ← permanent staging snapshot after merge

For diamond merges (node with 2+ deps):
  staging/merge_{hash}      ← temporary merge of multiple staging refs
                               used as base for the downstream node
                               hash = first 16 chars of sorted dep staging refs
```

---

### Staging ref computation — the key algorithm

```python
def get_base_branch(dag: Dag, node_id: str,
                    worktrees: WorktreeManager) -> str:
    """
    Computes the correct base branch for a node's worktree.
    This is the staging ref that contains all dependency outputs.

    Cases:
      1. Leaf node (no deps)       → "main"
      2. Single dep node           → dep.staging_ref
      3. Diamond node (2+ deps)    → merged staging ref of all deps
    """
    dep_ids = [e.src for e in dag.edges if e.dst == node_id]

    # Case 1: leaf node
    if not dep_ids:
        return "main"

    dep_nodes = [dag.nodes[d] for d in dep_ids]

    # Validate all deps have completed and have staging refs
    for dep in dep_nodes:
        if dep.status != NodeStatus.DONE:
            raise RuntimeError(
                f"Cannot compute base branch for {node_id}: "
                f"dependency {dep.id} is not done (status={dep.status}). "
                f"This should never happen — scheduler bug."
            )
        if not dep.staging_ref:
            raise RuntimeError(
                f"Dependency {dep.id} has no staging_ref. "
                f"MergePipeline did not complete correctly."
            )

    staging_refs = [dep.staging_ref for dep in dep_nodes]

    # Case 2: single dependency
    if len(staging_refs) == 1:
        return staging_refs[0]

    # Case 3: diamond — create merged staging base
    return _create_diamond_base(staging_refs, worktrees)


def _create_diamond_base(staging_refs: list[str],
                          worktrees: WorktreeManager) -> str:
    """
    Creates a temporary merge branch combining all staging refs.
    This becomes the base for the diamond node's worktree.

    The merge must be conflict-free — if it isn't, the planner
    produced overlapping write sets, which is a planning error.
    """
    # Deterministic branch name based on sorted refs
    key = "+".join(sorted(staging_refs))[:48]  # max branch name length
    branch = f"staging/merge_{key}"

    # Check if this merge already exists (idempotent)
    try:
        worktrees._git("rev-parse", "--verify", branch)
        return branch  # already exists
    except subprocess.CalledProcessError:
        pass  # doesn't exist, create it

    # Create merge branch from first staging ref
    worktrees._git("checkout", "-b", branch, staging_refs[0])

    # Merge in each additional staging ref
    for ref in staging_refs[1:]:
        result = subprocess.run(
            ["git", "merge", "--no-ff", ref,
             "-m", f"diamond base: merging {ref}"],
            cwd=worktrees.repo,
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Conflict in diamond base — this means two upstream nodes
            # modified the same file, which should have been caught by
            # write-set conflict detection at planning time.
            raise PlanningError(
                f"Diamond base merge conflict between {staging_refs}. "
                f"This indicates overlapping write sets that were not "
                f"caught at planning time. Check validate_dag()."
            )

    return branch
```

---

### Staging merge — what happens after a node completes

```python
def merge_node_to_staging(node: Node, worktrees: WorktreeManager) -> str:
    """
    After a node commits its work, merge it to a staging snapshot.
    Returns the new staging branch name.

    This staging branch becomes the base_branch for all nodes
    that directly depend on this node.
    """
    node_branch    = f"node/{node.id}"
    staging_branch = f"staging/after_{node.id}"
    base           = node.base_branch  # what this node branched from

    # Create staging branch from the same base this node branched from
    # (not from main — this preserves the chain)
    worktrees._git("checkout", "-b", staging_branch, base)

    # Merge the node's work into staging
    result = subprocess.run(
        ["git", "merge", "--no-ff", node_branch,
         "-m", f"staging: merge node/{node.id} ({node.node_type})"],
        cwd=worktrees.repo,
        capture_output=True, text=True
    )

    if result.returncode != 0:
        # Unexpected conflict. The node modified a file it didn't declare
        # in its write_set, or the conflict detection missed an overlap.
        raise MergeConflictError(
            node_id=node.id,
            branch=node_branch,
            stderr=result.stderr,
            hint="Check node.write_set declaration and validate_dag()"
        )

    return staging_branch
```

---

### Visual example: correct staging chain

```
Initial state: main at commit C0

Task decomposes into:
  stub_auth  → impl_auth  → test_auth  → merge
                           ↗
  (stub_auth also feeds impl_routes)
  stub_auth  → impl_routes → test_routes → merge

Execution timeline:

  T=0: stub_auth dispatched
       worktree: /tmp/worktrees/stub_auth
       base_branch: "main" (no deps)

  T=1: stub_auth completes
       commits to node/stub_auth (commit C1)
       MergePipeline:
         creates staging/after_stub_auth from main
         merges node/stub_auth into it (commit C2)
         stub_auth.staging_ref = "staging/after_stub_auth"
       Port satisfaction:
         impl_auth.data_in[auth_stub].satisfied = True
         impl_routes.data_in[auth_stub].satisfied = True
       Both impl_auth and impl_routes now READY

  T=2: impl_auth AND impl_routes dispatched (PARALLEL)
       impl_auth:
         base_branch = "staging/after_stub_auth"   ← sees stub_auth output
         worktree branches from C2
       impl_routes:
         base_branch = "staging/after_stub_auth"   ← sees stub_auth output
         worktree branches from C2

  T=3: impl_auth completes first
       commits to node/impl_auth (commit C3, branched from C2)
       MergePipeline:
         creates staging/after_impl_auth from staging/after_stub_auth
         merges node/impl_auth (C2 → C2+auth changes = C4)
         impl_auth.staging_ref = "staging/after_impl_auth"

  T=4: impl_routes completes
       commits to node/impl_routes (C5, branched from C2)
       MergePipeline:
         creates staging/after_impl_routes from staging/after_stub_auth
         merges node/impl_routes (C2 → C2+routes changes = C6)
         impl_routes.staging_ref = "staging/after_impl_routes"

  T=5: test_auth dispatched
       base_branch = "staging/after_impl_auth"   ← sees auth implementation
       NOT from main, NOT from staging/after_stub_auth

  T=6: test_routes dispatched
       base_branch = "staging/after_impl_routes" ← sees routes implementation

  T=7: both tests complete, merge node dispatched
       DIAMOND: merge node depends on test_auth AND test_routes
       get_base_branch computes diamond base:
         creates staging/merge_after_impl_auth+after_impl_routes
         merges staging/after_impl_auth + staging/after_impl_routes
       merge node worktree branches from diamond base
       ← sees BOTH auth AND routes implementations

  T=8: merge node creates final PR from its staging branch

WRONG version (what existing tools do):
  All nodes branch from "main"
  impl_routes branches from main → CANNOT SEE stub_auth output
  Agent generates routes code using undefined/wrong auth API
  Merge conflicts or silent semantic errors at T=8
```

---

## 4. Write-Set Conflict Detection

### Motivation

If two parallel nodes modify the same file, their git diffs will conflict at merge time. This conflict is expensive: it requires a merge resolver agent, may invalidate downstream work, and is hard to diagnose. The right place to catch it is at planning time, before any agent runs.

---

### Core invariant

> **Two nodes that may run in parallel must have disjoint write sets.** Two nodes "may run in parallel" iff neither is an ancestor of the other in the DAG.

---

### Parallel pair detection

```python
def get_parallel_pairs(dag: Dag) -> list[tuple[str, str]]:
    """
    Returns all pairs of nodes that may execute concurrently.
    A pair (A, B) is parallel iff:
      - A is not an ancestor of B
      - B is not an ancestor of A
    """
    pairs = []
    node_ids = list(dag.nodes.keys())
    for i, a in enumerate(node_ids):
        for b in node_ids[i+1:]:
            if not _is_ancestor(dag, a, b) and not _is_ancestor(dag, b, a):
                pairs.append((a, b))
    return pairs


def _is_ancestor(dag: Dag, src: str, dst: str) -> bool:
    """True if src can reach dst by following edges forward."""
    visited = set()
    queue   = [src]
    while queue:
        node = queue.pop()
        if node == dst:
            return True
        for edge in dag.edges:
            if edge.src == node and edge.dst not in visited:
                visited.add(edge.dst)
                queue.append(edge.dst)
    return False


def check_write_set_conflicts(dag: Dag) -> None:
    """
    For every parallel pair, check that their write sets are disjoint.
    Raises ValueError with detailed conflict information on violation.
    """
    for (aid, bid) in get_parallel_pairs(dag):
        a = dag.nodes[aid]
        b = dag.nodes[bid]
        overlap = set(a.write_set) & set(b.write_set)
        if overlap:
            raise ValueError(
                f"Write-set conflict detected at planning time:\n"
                f"  Node '{aid}' ({a.node_type}) write_set: {a.write_set}\n"
                f"  Node '{bid}' ({b.node_type}) write_set: {b.write_set}\n"
                f"  Overlap: {sorted(overlap)}\n"
                f"  These nodes may run in parallel but share files.\n"
                f"  Fix: add an edge from '{aid}' to '{bid}' (or vice versa)\n"
                f"  to serialize them, OR split the file into separate modules."
            )
```

---

### Runtime enforcement — declared vs actual write set

At planning time, the planner declares what files each node will modify. At runtime, the MergePipeline checks what files the node actually modified. If the actual set is larger than the declared set, the node fails.

```python
def verify_write_set(node: Node, diff_text: str) -> None:
    """
    Called by MergePipeline before merging a node's diff.
    Verifies the node only modified files in its declared write_set.
    """
    actually_modified = _files_from_diff(diff_text)
    declared          = set(node.write_set)
    undeclared        = actually_modified - declared

    if undeclared:
        raise WriteSetViolationError(
            f"Node '{node.id}' modified files outside its declared write_set.\n"
            f"  Declared write_set: {sorted(declared)}\n"
            f"  Actually modified:  {sorted(actually_modified)}\n"
            f"  Undeclared files:   {sorted(undeclared)}\n"
            f"  The node will be failed and must be replanned.\n"
            f"  The context prompt must explicitly state the write_set restriction."
        )


def _files_from_diff(diff_text: str) -> set[str]:
    """Extract set of modified file paths from unified diff."""
    files = set()
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
    return files
```

---

### What the planner must tell agents about write sets

The write set restriction must appear explicitly in every agent's context prompt. Agents that are not told their restriction will modify whatever files they want.

```
# REQUIRED section in every agent context prompt:

## Files you are authorized to modify
You may ONLY modify the following files:
{json.dumps(node.write_set, indent=2)}

If you need to modify a file not on this list, emit:
  SENTINEL:CONTROL:blocked:needs_write_access:{file_path}

Do NOT modify any other files. Your diff will be validated against
this list before merging. Unauthorized modifications will cause the
node to fail.
```


Applying the **MECE (Mutually Exclusive, Collectively Exhaustive)** principle is the most effective way to ensure the **Amesh Framework** is robust, scalable, and free of logical "dead zones." In systems engineering, MECE ensures that your components don't overlap (causing conflicts) and that you haven't forgotten any edge cases (causing crashes).

Here is how to map MECE directly to the key sections of technical specification:

---

### 1. MECE in Write-Set Conflict Detection (The "Mutually Exclusive" Part)

The "Mutually Exclusive" rule is the literal definition of your parallel execution safety. If two agents are running at the same time, their **Write-Sets** must be MECE.

- **Mutually Exclusive (ME):** No two parallel nodes should share a file path. If `Node A` writes to `src/auth.py`, no other node in that parallel branch can touch it. This prevents the "Split-Brain" git conflict.
    
- **Collectively Exhaustive (CE):** The sum of all `write_sets` across the entire DAG must cover every file that needs modification to fulfill the task. If the agent needs to edit a file that wasn't "exhaustively" listed, it triggers the **Proposal Protocol**.
    
---

### 2. MECE in the Typed DAG & Port System

`EdgePayloadType` enum is a prime candidate for a MECE audit.

- **Mutually Exclusive:** Each payload type should have a distinct purpose. For example, `INTERFACE_STUB` should not contain implementation details, and `GIT_DIFF` should not contain type signatures. If a payload could fit into two categories, your agents will get confused about where to look for data.
    
- **Collectively Exhaustive:** Does your `Enum` cover every possible type of data an agent might need to pass?
    
    - _Check:_ Do you have a type for **Environment Configs**? Or **Dependency Graphs**? If an agent needs to pass "Environmental State" and your types don't cover it, the framework isn't CE.
        

---

### 3. MECE in Node Status & State Transitions (RocksDB/WAL)

The state machine in **Section 1** must be MECE to prevent "Ghost States" where a node is neither running nor failed.

- **ME:** A node cannot be `READY` and `RUNNING` at the same time. The transitions must be atomic (enforced by your **RocksDB WriteBatch**).
    
- **CE:** Your list of statuses (`PENDING`, `READY`, `RUNNING`, `DONE`, `FAILED`, `BLOCKED`) must cover every second of a node’s life.
    
    - _Edge Case:_ What about a node that is **REPLANNING**? If you don't have a status for that, the system is not CE, and the Orchestrator won't know how to handle it.
        

---

### 4. MECE in the Sentinel Protocol

Your **Section 6** defines how agents "talk" back to the Orchestrator.

- **ME:** Every sentinel kind (`PROGRESS`, `OUTPUT_READY`, `CONTROL`, `PROPOSE`) should trigger a unique and non-overlapping logic path in the Orchestrator. You shouldn't use `PROGRESS` to send small data payloads; that’s what `OUTPUT_READY` is for.
    
- **CE:** Do these sentinels cover all possible outcomes?
    
    - Success? (Yes: `CONTROL:success`)
        
    - Failure? (Yes: `CONTROL:failure`)
        
    - Human needed? (Yes: `CONTROL:blocked`)
        
    - **External Change?** (No: What if the user manually edits a file while the agent is running? You might need a sentinel for `SYNC_REQUIRED`).
        

---

### Summary Table: MECE Audit for Amesh

|**Framework Component**|**Mutually Exclusive (ME)**|**Collectively Exhaustive (CE)**|
|---|---|---|
|**Write-Sets**|No two parallel nodes edit the same file.|All necessary files are accounted for in the DAG.|
|**Edge Types**|Data only fits into one `EdgePayloadType`.|All data needs (diffs, stubs, reports) are typed.|
|**State Store**|A node has exactly one status in RocksDB.|Every possible node state is defined in the Enum.|
|**Sentinels**|No ambiguity between "Output" and "Control".|Every agent exit reason has a corresponding sentinel.|

By applying MECE,

---

## 5. Context Assembly & Token Budget

### Motivation

Naively giving every agent the full workspace context wastes tokens proportionally to the number of agents. With 10 parallel agents and a 50k token workspace context, you pay 500k tokens on context alone per DAG.

The solution has three parts:

1. **Shared prefix caching** — structural context is identical for all agents
2. **Stub compression** — upstream diffs (large) are replaced with stubs (small)
3. **Lazy file reads** — agents read files on demand via tool calls, not upfront

---

### Context layers and their sizes

```
Layer                           Size      Caching     Scope
──────────────────────────────────────────────────────────────
AGENTS.md                       ~2k       Cached      All agents
File tree (git ls-tree)         ~5k       Cached      All agents
Active DAG summary              ~1k       Refreshed   All agents
Direct dep stubs                ~2k/dep   Per-node    This node only
Task description                ~500      Per-node    This node only
Write set restriction           ~200      Per-node    This node only
Sentinel protocol instructions  ~300      Per-node    This node only
──────────────────────────────────────────────────────────────
Typical total                   ~11-15k tokens per agent

NOT injected (fetched on demand):
  File contents                 varies    Never       Agent reads lazily
  Full diffs from upstream      varies    Never       Stored in RocksDB only
  Non-dep stubs                 varies    Never       Not relevant
```

---

### Context assembly algorithm

```python
async def build_node_context(
    node:         Node,
    dag:          Dag,
    shared_cache: WorkspaceContextCache,
    port_payloads: dict[str, str],   # port_name → payload content
) -> str:
    """
    Assembles the exact context string to inject into an agent's prompt.
    Respects token_budget. Raises ContextBudgetError if budget exceeded
    even after all compression.
    """
    cache = await shared_cache.get()

    # Layer 1: Shared cached prefix (stable across all agents)
    shared_prefix = _build_shared_prefix(cache)

    # Layer 2: Direct dependency stubs ONLY
    # Do NOT inject grandparent or sibling node outputs.
    # Agents should only know what their direct deps produced.
    direct_dep_ids = {e.src for e in dag.edges if e.dst == node.id}
    dep_stubs = {
        module: stub
        for module, stub in cache["interface_stubs"].items()
        if module in direct_dep_ids     # only direct deps
    }
    stubs_section = _build_stubs_section(dep_stubs)

    # Layer 3: Per-node task context
    task_section = _build_task_section(node, dag)

    # Combine and check budget
    full_context = shared_prefix + stubs_section + task_section

    estimated_tokens = len(full_context) // 4  # 4 chars ≈ 1 token
    if estimated_tokens > node.token_budget:
        # Try truncating the file tree (largest component)
        full_context = _truncate_file_tree(
            full_context, node.token_budget
        )
        estimated_tokens = len(full_context) // 4
        if estimated_tokens > node.token_budget:
            raise ContextBudgetError(
                f"Node {node.id} context ({estimated_tokens} tokens) "
                f"exceeds budget ({node.token_budget} tokens) "
                f"even after truncation. Planner must re-decompose "
                f"this node into smaller units."
            )

    return full_context


def _build_shared_prefix(cache: dict) -> str:
    return f"""# Amesh Agent Instructions

{cache.get('agents_md', '')}

## Workspace file structure
{cache.get('file_tree', '')}

## DAG progress (completed nodes)
{json.dumps(cache.get('dag_progress', {}), indent=2)}

"""


def _build_stubs_section(dep_stubs: dict[str, str]) -> str:
    if not dep_stubs:
        return ""
    lines = ["## Interface contracts from upstream nodes",
             "These are the APIs your dependencies expose.",
             "Implement against these contracts exactly.",
             ""]
    for module, stub in dep_stubs.items():
        lines.append(f"### {module}")
        lines.append("```python")
        lines.append(stub)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _build_task_section(node: Node, dag: Dag) -> str:
    return f"""## Your assignment
Node ID:   {node.id}
Node type: {node.node_type}
Agent:     {node.agent}

{node.task_description}

## Files you are authorized to modify
{json.dumps(node.write_set, indent=2)}

Do NOT modify files outside this list.
If you need access to a file not listed, emit:
  SENTINEL:CONTROL:blocked:needs_write_access:<file_path>

## When complete
On success:  emit SENTINEL:CONTROL:success
On failure:  emit SENTINEL:CONTROL:failure:<reason>
On blocked:  emit SENTINEL:CONTROL:blocked:<question>

"""
```

---

### Stub extraction — what stubs look like

```
Full implementation (what gets stored in RocksDB, NOT injected):

  # src/auth/jwt.py
  import hmac, hashlib, json, base64, time

  SECRET_KEY = "..."

  def _encode_header(alg: str) -> str:
      return base64.b64encode(json.dumps({"alg": alg}).encode()).decode()

  def _sign(header: str, payload: str, secret: str) -> str:
      msg = f"{header}.{payload}".encode()
      return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

  def create_token(user_id: int, expires_in: int = 3600) -> str:
      payload = {"sub": user_id, "exp": time.time() + expires_in}
      header  = _encode_header("HS256")
      body    = base64.b64encode(json.dumps(payload).encode()).decode()
      sig     = _sign(header, body, SECRET_KEY)
      return f"{header}.{body}.{sig}"

  def verify_token(token: str) -> dict | None:
      try:
          header, body, sig = token.split(".")
          ...
      except Exception:
          return None

────────────────────────────────────────────────────────

Extracted stub (what gets injected into downstream agents):

  # src/auth/jwt.py — interface stub
  def create_token(user_id: int, expires_in: int = 3600) -> str: ...
  def verify_token(token: str) -> dict | None: ...

────────────────────────────────────────────────────────

Token comparison:
  Full implementation: ~800 tokens
  Extracted stub:      ~40 tokens
  Compression ratio:   20x
```

---

## 6. Sentinel Protocol

### Motivation

Agents communicate with the orchestrator by writing structured sentinel lines to stdout. The orchestrator's subprocess reader parses these in real time to know:

- Whether the agent is still working (progress)
- What outputs the agent has produced (output_ready)
- How the agent finished (control: success/failure/blocked)
- Whether human input is needed (clarify)

The sentinel protocol is the only communication channel between an agent subprocess and the orchestrator. It must be unambiguous and parseable without context.

---

### Core invariant

> **Every agent subprocess must emit exactly one SENTINEL:CONTROL line before exiting, and it must be the last sentinel emitted.** The orchestrator uses SENTINEL:CONTROL to trigger the post-completion pipeline. If it never arrives, the orchestrator assumes the agent crashed.

---

### Sentinel format

```
SENTINEL:{KIND}:{FIELD_1}:{FIELD_2}:...

Fields are colon-separated.
The number of fields depends on KIND.
Colons within field values must be escaped as \c (backslash-c).
Newlines within field values are not permitted.
Every sentinel line must begin with "SENTINEL:" (case-sensitive).
Sentinel lines are interspersed with normal agent output.
The orchestrator ignores all non-sentinel lines for parsing purposes.
```

---

### Sentinel types and exact formats

```
# ─────────────────────────────────────────────────────────────
# SENTINEL:PROGRESS
# Emitted periodically to signal the agent is still alive.
# The orchestrator resets a watchdog timer on each PROGRESS sentinel.
# ─────────────────────────────────────────────────────────────

Format:   SENTINEL:PROGRESS:{message}
Example:  SENTINEL:PROGRESS:analyzing src/auth/jwt.py

  message: human-readable description of current activity
           shown in the Telegram status update and dashboard
           max 120 characters


# ─────────────────────────────────────────────────────────────
# SENTINEL:OUTPUT_READY
# Emitted when the agent has produced a typed output payload.
# May be emitted multiple times (once per output port).
# Must be emitted BEFORE SENTINEL:CONTROL:success.
# ─────────────────────────────────────────────────────────────

Format:   SENTINEL:OUTPUT_READY:{port_name}:{payload_type}:{payload_content}
Examples:
  SENTINEL:OUTPUT_READY:stub_out:interface_stub:def create_token(user_id\c int) -> str\c ...
  SENTINEL:OUTPUT_READY:diff_out:git_diff:DIFF_FOLLOWS_IN_NEXT_LINES

  port_name:       must match one of node.data_out[*].name exactly
  payload_type:    must match node.data_out[*].payload_type exactly
  payload_content: the payload value, colons escaped as \c
                   for large payloads (diffs), use the multiline variant below


# ─────────────────────────────────────────────────────────────
# SENTINEL:OUTPUT_READY multiline variant
# For large payloads (diffs, reports) that cannot fit on one line.
# ─────────────────────────────────────────────────────────────

Format:
  SENTINEL:OUTPUT_READY:{port_name}:{payload_type}:MULTILINE_BEGIN
  {content line 1}
  {content line 2}
  ...
  SENTINEL:OUTPUT_END:{port_name}

Example:
  SENTINEL:OUTPUT_READY:diff_out:git_diff:MULTILINE_BEGIN
  diff --git a/src/auth/jwt.py b/src/auth/jwt.py
  +++ b/src/auth/jwt.py
  @@ -0,0 +1,30 @@
  +def create_token(user_id: int) -> str:
  +    ...
  SENTINEL:OUTPUT_END:diff_out


# ─────────────────────────────────────────────────────────────
# SENTINEL:CONTROL
# Final sentinel. Always the last line before the agent exits.
# Triggers the post-completion pipeline in the orchestrator.
# ─────────────────────────────────────────────────────────────

Format (success):
  SENTINEL:CONTROL:success

Format (failure):
  SENTINEL:CONTROL:failure:{reason}
  Example: SENTINEL:CONTROL:failure:compilation_error\c undefined variable 'user'

  reason: human-readable error description, colons escaped as \c

Format (blocked — needs human input):
  SENTINEL:CONTROL:blocked:{question}
  Example: SENTINEL:CONTROL:blocked:Should auth tokens expire after 1h or 24h?

  question: the question to send to the human via Telegram
            The agent will be resumed when the human answers.
            The answer is injected as a new user turn in the agent's context.

Format (needs write access to undeclared file):
  SENTINEL:CONTROL:blocked:needs_write_access:{file_path}
  Example: SENTINEL:CONTROL:blocked:needs_write_access:src/utils/crypto.py

  This triggers replanning: the planner either adds file_path to this
  node's write_set (if safe) or creates a new dependency node for it.


# ─────────────────────────────────────────────────────────────
# SENTINEL:CLARIFY
# Emitted when the agent needs clarification but can partially continue.
# Unlike CONTROL:blocked, the agent does NOT stop — it continues with
# a best-guess and marks the output as needing human review.
# ─────────────────────────────────────────────────────────────

Format:   SENTINEL:CLARIFY:{question}:{default_assumption}
Example:  SENTINEL:CLARIFY:Use bcrypt or argon2 for password hashing?:defaulting to bcrypt

  question:           sent to human via Telegram (non-blocking)
  default_assumption: what the agent will do if no answer received
                      included in the PROGRESS updates and final output
```

---

### Orchestrator sentinel parser

```python
class SentinelParser:
    """
    Parses sentinel lines emitted by agent subprocesses.
    Handles both single-line and multiline output payloads.
    """

    def __init__(self):
        self._multiline_port:    str | None = None
        self._multiline_type:    str | None = None
        self._multiline_buffer:  list[str]  = []
        self.outputs:            dict[str, str] = {}  # port_name → content
        self.control:            str | None = None    # "success"|"failure"|"blocked"
        self.control_detail:     str | None = None
        self.clarifications:     list[dict] = []
        self.progress_messages:  list[str]  = []

    def feed_line(self, line: str) -> "SentinelEvent | None":
        """
        Feed one line of agent stdout. Returns a SentinelEvent if
        the line is a sentinel, None otherwise.
        """
        line = line.rstrip("\n")

        # Handle ongoing multiline capture
        if self._multiline_port is not None:
            if line.startswith(f"SENTINEL:OUTPUT_END:{self._multiline_port}"):
                content = "\n".join(self._multiline_buffer)
                content = self._unescape(content)
                self.outputs[self._multiline_port] = content
                port = self._multiline_port
                self._multiline_port   = None
                self._multiline_type   = None
                self._multiline_buffer = []
                return SentinelEvent("output_ready", port=port, content=content)
            else:
                self._multiline_buffer.append(line)
                return None

        if not line.startswith("SENTINEL:"):
            return None

        parts = line.split(":", 3)  # at most 4 parts
        if len(parts) < 2:
            return None

        kind = parts[1]

        if kind == "PROGRESS":
            msg = parts[2] if len(parts) > 2 else ""
            self.progress_messages.append(msg)
            return SentinelEvent("progress", message=msg)

        elif kind == "OUTPUT_READY":
            if len(parts) < 4:
                return None
            port, ptype, content = parts[1], parts[2], parts[3]
            # Remove the "OUTPUT_READY" from port
            port  = parts[2]
            ptype = parts[3] if len(parts) > 3 else ""
            content_or_marker = parts[4] if len(parts) > 4 else ""

            # Reconstruct: SENTINEL:OUTPUT_READY:{port}:{ptype}:{content}
            raw_parts = line.split(":", 4)
            port     = raw_parts[2]
            ptype    = raw_parts[3]
            content  = raw_parts[4] if len(raw_parts) > 4 else ""

            if content == "MULTILINE_BEGIN":
                self._multiline_port   = port
                self._multiline_type   = ptype
                self._multiline_buffer = []
                return None  # wait for SENTINEL:OUTPUT_END

            content = self._unescape(content)
            self.outputs[port] = content
            return SentinelEvent("output_ready", port=port, content=content)

        elif kind == "CONTROL":
            status = parts[2] if len(parts) > 2 else ""
            detail = parts[3] if len(parts) > 3 else None
            if detail:
                detail = self._unescape(detail)
            self.control        = status
            self.control_detail = detail
            return SentinelEvent("control", status=status, detail=detail)

        elif kind == "CLARIFY":
            question   = parts[2] if len(parts) > 2 else ""
            assumption = parts[3] if len(parts) > 3 else ""
            question   = self._unescape(question)
            assumption = self._unescape(assumption)
            self.clarifications.append({
                "question": question,
                "assumption": assumption
            })
            return SentinelEvent("clarify",
                                  question=question,
                                  assumption=assumption)

        return None

    def _unescape(self, s: str) -> str:
        """Reverse the colon-escaping in sentinel field values."""
        return s.replace(r"\c", ":")

    def is_complete(self) -> bool:
        """True when a CONTROL sentinel has been received."""
        return self.control is not None


@dataclass
class SentinelEvent:
    kind:       str           # "progress"|"output_ready"|"control"|"clarify"
    message:    str | None = None
    port:       str | None = None
    content:    str | None = None
    status:     str | None = None
    detail:     str | None = None
    question:   str | None = None
    assumption: str | None = None
```

---

### Watchdog — detecting hung agents

```python
WATCHDOG_TIMEOUT = 300  # seconds between PROGRESS sentinels before considered hung

async def run_with_watchdog(proc: asyncio.subprocess.Process,
                             parser: SentinelParser,
                             node: Node,
                             store: StateStore,
                             dag_id: str) -> bool:
    """
    Reads agent stdout line by line.
    If no SENTINEL:PROGRESS received within WATCHDOG_TIMEOUT seconds,
    kills the agent and marks the node as failed.
    Returns True on success, False on failure or timeout.
    """
    last_progress = asyncio.get_event_loop().time()

    async def read_lines():
        nonlocal last_progress
        async for raw_line in proc.stdout:
            line  = raw_line.decode()
            event = parser.feed_line(line)
            if event and event.kind == "progress":
                last_progress = asyncio.get_event_loop().time()
            if parser.is_complete():
                return

    async def watchdog():
        while not parser.is_complete():
            await asyncio.sleep(10)
            elapsed = asyncio.get_event_loop().time() - last_progress
            if elapsed > WATCHDOG_TIMEOUT:
                proc.kill()
                await store.fail_node(dag_id, node.id,
                    f"watchdog_timeout: no progress for {elapsed:.0f}s")
                return

    await asyncio.gather(read_lines(), watchdog())
    await proc.wait()

    return parser.control == "success"
```

---

### Complete agent execution flow

```
Orchestrator                          Agent subprocess
────────────────────────────────────────────────────────────
1. build context string
2. create worktree
3. spawn subprocess with context
   ──────────────────────────────────► agent starts
4. watchdog timer starts              agent reads files, plans
                                      SENTINEL:PROGRESS:reading src/auth/jwt.py ──►
5. reset watchdog timer ◄─────────────────────────────────────
                                      agent implements
                                      SENTINEL:PROGRESS:implementing create_token ──►
6. reset watchdog timer ◄─────────────────────────────────────
                                      agent finishes implementation
                                      SENTINEL:OUTPUT_READY:stub_out:interface_stub:MULTILINE_BEGIN ──►
                                      def create_token(user_id: int) -> str: ... ──►
                                      SENTINEL:OUTPUT_END:stub_out ──►
7. capture stub payload ◄─────────────────────────────────────
                                      SENTINEL:OUTPUT_READY:diff_out:git_diff:MULTILINE_BEGIN ──►
                                      diff --git a/src/auth/jwt.py ... ──►
                                      SENTINEL:OUTPUT_END:diff_out ──►
8. capture diff payload ◄─────────────────────────────────────
                                      SENTINEL:CONTROL:success ──►
9. control received ◄─────────────────────────────────────────
10. verify write_set
11. commit worktree
12. merge to staging
13. extract stubs
14. refresh context cache
15. satisfy downstream ports
16. WAL: NODE_COMPLETED
17. cleanup worktree
────────────────────────────────────────────────────────────
```


## 7. Agentic Orchestration & Dynamic Proposal Protocol

### Motivation

A static DAG is fragile; if an agent discovers a new requirement mid-execution, a static system fails. Agentic Orchestration allows nodes to "negotiate" their constraints with the Orchestrator. This transforms the Orchestrator from a passive scheduler into an **Executive Authority** that can mutate the DAG and permissions in real-time.

we can use https://github.com/openai/openai-agents-python to implement the agent framework.

---

### 7.1 The Proposal Handshake

When an agent encounters a limitation (e.g., a file not in its `write_set`), it emits a `PROPOSE` sentinel. The Orchestrator must intercept this and decide whether to **Promote** (grant access) or **Replan** (call the Planner).

**Core Invariant:**

> **An agent may never act on a proposal until it receives an ACK from the Orchestrator.** The agent process is "thrashed" (paused) while the Orchestrator validates the proposal against the current global state in RocksDB.

---

### 7.2 Proposal Types & Resolution Logic

|**Proposal Type**|**Payload**|**Orchestrator Action**|
|---|---|---|
|`EDIT_EXPANSION`|`{file_path}`|Check `write_set` registry. If no parallel conflict, update WAL and grant.|
|`NODE_SPLIT`|`{sub_tasks}`|Pause node. Call Planner to decompose current node into $N$ new nodes.|
|`SCHEMA_CHANGE`|`{interface_stub}`|Call Planner to verify downstream impact. If safe, update `INTERFACE_STUB` payloads.|

---

### 7.3 Data Structures: The Executive State

Python

```
class ProposalStatus(str, Enum):
    PENDING   = "pending"   # Orchestrator is validating
    APPROVED  = "approved"  # WAL updated, agent may proceed
    REJECTED  = "rejected"  # Agent must find workaround or fail
    REPLANNING = "replanning" # DAG is being mutated by Planner

class Proposal(BaseModel):
    id:         str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    node_id:    str
    dag_id:     str
    kind:       str         # e.g., "EDIT_EXPANSION"
    payload:    dict
    status:     ProposalStatus = ProposalStatus.PENDING
    timestamp:  datetime = Field(default_factory=datetime.utcnow)
```

---

### 7.4 WAL Event Additions (RocksDB schema)

To maintain the **WAL-First** principle, every proposal must be logged before the Orchestrator evaluates it.

Python

```
# Add these to WalEvent Enum (Section 2)
PROPOSAL_RECEIVED = "proposal_received"
PROPOSAL_RESOLVED = "proposal_resolved"
DAG_PATCHED       = "dag_patched"

# Example WAL entry for a write_set expansion:
# event: "proposal_received"
# data:  {"node_id": "a3f2b1c9", "kind": "EDIT_EXPANSION", "file": "src/registry.py"}
```

---

### 7.5 Conflict Resolution Algorithm (The "Executive Lock")

Python

```
async def resolve_expansion_proposal(rdb: RocksDB, dag_id: str, proposal: Proposal) -> bool:
    """
    Checks if a proposed file expansion conflicts with any other active node.
    Returns True if approved, False if a Replan is required.
    """
    requested_file = proposal.payload["file"]
    
    # 1. Query RocksDB for all 'write_sets' of PENDING, READY, or RUNNING nodes
    active_write_sets = await rdb.get_active_write_sets(dag_id)
    
    # 2. Check for overlap
    for other_node_id, files in active_write_sets.items():
        if other_node_id == proposal.node_id:
            continue
        if requested_file in files:
            # CONFLICT: Another node owns this file or will own it.
            return False 
            
    # 3. No conflict: Commit to WAL and expand permissions
    await wal.append(WalEvent.PROPOSAL_RESOLVED, status="approved", ...)
    await rdb.expand_node_write_set(proposal.node_id, requested_file)
    return True
```

---

### 7.6 Dynamic DAG Patching

If a proposal is rejected due to conflict, the Orchestrator initiates a **DAG Patch**.

1. **Freeze:** The Orchestrator prevents any new nodes from being dispatched.
    
2. **Snapshot:** The current state of all `DONE` and `RUNNING` nodes is sent to the **Planner Agent**.
    
3. **Patch:** The Planner returns a "Delta-DAG" (a set of new nodes and edges to be added).
    
4. **Atomicity:** The Orchestrator writes the Delta-DAG to RocksDB in a single `WriteBatch`, ensuring the WAL remains the source of truth for the transition.
    

---

### 7.7 Sentinel Protocol Expansion (Section 6 Update)

Plaintext

```
# NEW SENTINEL FORMATS

# Agent -> Orchestrator
SENTINEL:PROPOSE:{KIND}:{PAYLOAD}
Example: SENTINEL:PROPOSE:EDIT_EXPANSION:src/utils.py

# Orchestrator -> Agent (via ACP/stdin)
# The orchestrator responds with a structured JSON-RPC message:
{
  "jsonrpc": "2.0",
  "method": "proposal/resolved",
  "params": { "id": "prop123", "decision": "approved" }
}
```

