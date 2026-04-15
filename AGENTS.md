# codara Agent Guide

This repository was generated from a Copier template. Keep changes small,
reviewable, and aligned with the project layout.

## Project Structure

- `src/`: Python package source.
- `tests/`: Python tests.
- `pyproject.toml`: Python project metadata.
- `docs/`: Human documentation, decisions, onboarding, and reference notes.
- `scripts/`: Repeatable local automation.
- `.agents/shared.md`: Shared instructions used across agent tools.
- `.agents/rules/`: Core rule set for the compound engineering loop.
- `.agents/plans/`: Implementation plans and triage notes for non-trivial work.
- `.agents/learnings/`: Reusable lessons, pitfalls, and follow-up notes that future agents must consult.

## Rule Loading Order

Read these before starting non-trivial work:

1. `.agents/shared.md`
2. `.agents/rules/avoid-these.md`
3. `.agents/rules/compound-engineering.mdc`
4. The rule files most relevant to the task:
   - `.agents/rules/patterns.md`
   - `.agents/rules/architecture.md`
   - `.agents/rules/tools.md`

When two rules seem to conflict, follow the more specific file for the task at hand.
If repository code or docs establish a stronger local constraint, follow the repository.

## Working Rules

- Read `README.md` before making broad structural changes.
- Prefer updating existing files over introducing new top-level layout.
- Run the narrowest relevant checks for the area you touched.
- Document new workflows and design decisions in `docs/`.
- Treat `.agents/plans/` and `.agents/learnings/` as required project memory, not optional extras.
- Before implementing non-trivial work, read the active plan in `.agents/plans/` and any relevant entries in `.agents/learnings/`.
- While implementing, follow the active plan; if reality changes, update the plan before continuing.

## The Enhanced Compound Engineering Loop

The canonical process lives in `.agents/rules/compound-engineering.mdc`.
Use it as the default workflow for features, bug fixes, refactors, and migrations.

### Step 0 — Triage when needed

> Skip this step if you already have a precise problem statement.

```
Use triage to diagnose <symptom>.
```


Use triage when the task starts from a symptom, failing test, vague report,
or incomplete problem statement.

Typical output:
- `.agents/plans/triage-<topic>.md`

Triage should focus on:
- relevant code paths
- recent changes and history
- failing logs, tests, or reproduction steps
- likely root cause and next investigation step

### Step 1 — Plan

**Never write code without a plan.**

#### Research First
1. The triage report (if one exists)
2. **Examine the codebase** - structure, patterns, conventions
3. **Review commit history** - how were similar features built?
4. **Check learnings** - read `.agents/learnings/*.md` for past insights
5. .agents/rules/compound-engineering.mdc, .agents/rules/patterns.md, .agents/rules/architecture.md
6. **Research best practices** - external docs, examples, patterns

#### Plan Document Template

Save to: `.agents/plans/YYYY-MM-DD-<feature>-plan.md`

It writes a plan containing: objective, constraints, relevant past
learnings, proposed architecture, files that will change, and
success criteria. 
```markdown
# [Feature] Implementation Plan

## Objective
[What are we building and why?]

## Research Summary
- [Codebase analysis findings]
- [Commit history patterns]
- [External best practices]
- [Applicable learnings]

## Proposed Architecture
[How this fits into the existing system]

## Implementation Approach
[Technical approach with code examples]

## Success Criteria
- [ ] [Criterion 1]
- [ ] [Criterion 2]
- [ ] [Required tests]

## Potential Issues
[Risks and mitigations]

## Sources
[Links used for research]
```

It does NOT write any code.

---

### Step 2 — Challenge

```
Use challenger to stress-test the plan in .agents/plans/YYYY-MM-DD-<feature>-plan.md.
```

The challenger reads the plan adversarially: finds hidden assumptions,
identifies failure modes, checks for conflicts with .agents/rules/architecture.md,
and verifies that success criteria actually validate the objective.

The planner should address any challenges before work begins. You
decide which challenges are worth incorporating.

---

### Step 3 — Work

Execute the plan in small, testable increments.

- For bug fixes and behavior changes, prefer TDD:
  1. write or update a failing test that captures the expected behavior
  2. implement the smallest change that makes it pass
  3. run the relevant tests
  4. refactor only after the behavior is protected
- If a change is hard to cover with automated tests, document the alternative validation approach explicitly.
- Follow the plan unless new evidence requires revision.
- If the plan becomes wrong, update the plan before continuing.
- Prefer targeted validation while iterating, then run broader checks when needed.
- Keep changes coherent and reviewable.

### Step 4 — Assess

**Multi-perspective review.**

Review the result from the angles that matter for the task.

Common review lenses:
- correctness
- consistency with existing patterns
- error handling
- test coverage
- security for sensitive inputs or auth flows
- performance for hot paths or expensive data access
- documentation for behavior or workflow changes
- real world user

For substantial changes, use the assessment approach from `.agents/rules/compound-engineering.mdc` rather than relying on a single pass.

Note: challenger-style review is not the same as TDD.
- challenger checks whether the plan is sound before implementation
- TDD checks whether the code satisfies the intended behavior during implementation

### Step 5 — Compound

Capture durable learnings so the next task is easier.

Save reusable notes under:
- `.agents/learnings/YYYY-MM-DD-<topic>.md`

#### What to Capture
- **Bugs** - What caused it? How to prevent?
- **Performance issues** - What was slow? The fix?
- **Architecture decisions** - Why this approach?
- **Patterns that work** - Reusable solutions
- **Mistakes** - What went wrong and why?
- **Tool insights** - Gotchas, configurations

#### Learning Document Template

Save to: `.agents/learnings/YYYY-MM-DD-<topic>.md`

```markdown
# [Topic]: [Brief Description]

**Date:** YYYY-MM-DD
**Context:** [What were you building?]

## The Problem
[What issue did you encounter?]

## The Solution
[How did you solve it? Code examples if helpful]

## Why This Works
[Reasoning for future agents]

## How to Apply
[When should future work use this?]

## Tags
#tag1 #tag2 #tag3
```

#### Update Project Rules
When learnings are broadly applicable:
- **Patterns** → `.agents/rules/patterns.md`
- **Architecture** → `.agents/rules/architecture.md`
- **Mistakes** → `.agents/rules/avoid-these.md`
- **Tools** → `.agents/rules/tools.md`


## Quick reference — which agent does what

| Agent          | Step        | Reads                          | Writes                          | Sandbox     |
|----------------|-------------|--------------------------------|---------------------------------|-------------|
| triage         | 0           | code, logs, learnings          | .agents/plans/triage-*.md       | read-only   |
| planner        | 1           | triage, learnings, rules       | .agents/plans/<feature>.md      | read-only   |
| challenger     | 2           | plan, .agents/rules/architecture.md    | review report (inline)          | read-only   |
| worker         | 3           | plan                           | .agents/plans/<feature>-draft.md | write      |
| reviewer       | 4           | changed code, rules            | findings (inline)               | read-only   |
| compounder     | 5           | review findings, learnings     | .agents/learnings/*.md          | write       |


## Rules All Agents Must Follow
1. Read .agents/rules/avoid-these.md before doing any work.
2. Never touch files outside your defined scope.
3. Read the active plan in `.agents/plans/` before implementing and follow it unless it is updated.
4. Read relevant entries in `.agents/learnings/` before planning or implementing. If a past learning is directly relevant, cite it
   explicitly in your output.
5. If you discover something that should become a learning but you are
   not the compounder, flag it clearly so the compounder picks it up.
6. Do not skip the draft/apply split to save time. The review gate exists
   for a reason.

## Shortcut — when you need to move fast

If the change is truly trivial (typo fix, config value, single-line
patch with no architectural implications), you may collapse the loop:

Do not use this shortcut for anything that touches business logic,
APIs, database schemas, or shared utilities.

---

## .agents/learnings/ conventions

Each file in .agents/learnings/ follows this format:

```markdown
# <Topic>
Date: <YYYY-MM-DD>
Source: <feature or PR this came from>
Tags: [technology, task-type, e.g. postgres, migration, auth]
Status: active | superseded

## What we learned
<1–3 sentences>

## Why it matters
<impact on future work>

## Rule to follow next time
<concrete instruction an agent can act on>

---
<!-- If superseded: -->
## Superseded by
<filename> on <date> — <reason>
```
Tag every entry. Tags are how agents find relevant learnings without
reading all files every time.

## Checklists

### Before Starting Any Feature
- [ ] Read `.agents/learnings/` for relevant insights
- [ ] Check for similar existing features
- [ ] Review recent commits for patterns
- [ ] Create plan in `.agents/plans/`
- [ ] Get plan approved before coding

### After Completing Any Feature
- [ ] Run full 8-perspective assessment
- [ ] Document at least ONE learning
- [ ] Update rules if broadly applicable
- [ ] Commit learnings with feature


## Key Principles

1. **Never code without a plan** - Quality is determined in planning
2. **Always capture learnings** - Compounding only works if you document
3. **Read before you write** - Check learnings before building
4. **Small, frequent compounds** - Don't wait for big insights
5. **Make learnings searchable** - Tags, clear titles, organization
6. **Update rules when patterns emerge** - Seen twice = pattern
