# amesh

@.agents/shared.md
@.agents/rules/avoid-these.md
@.agents/rules/compound-engineering.mdc

## Gemini CLI Notes

- Use `AGENTS.md` as the repository entrypoint for workflow and layout guidance.
- Treat `.agents/rules/compound-engineering.mdc` as the canonical definition of the compound engineering loop.
- Review `.agents/rules/patterns.md`, `.agents/rules/architecture.md`, and `.agents/rules/tools.md` when they are relevant to the task.
- For non-trivial work, check `.agents/learnings/` before planning, write plans under `.agents/plans/`, and follow the active plan while implementing.
- For bug fixes and behavior changes, prefer test-driven development: establish the failing test first, then implement the smallest passing change.
