# Explorer Page Rebuild Plan

**Date:** 2026-04-26
**Status:** Draft
**Target:** Table-based traces view with side panel (Users page pattern)

---

## Current Issue

The current Observability page shows traces as a vertical list. When clicking a trace, the detail panel appears inline below the list in a two-column grid. This pattern is inconsistent with other pages like Users/Sessions which use:
- **Table** for list view
- **Side panel** (slides from right) for detail view

## Proposal

```
┌─────────────────────────────────────────────────────────────┐
│ Explorer                                          │
├─────────────────────────────────────────────────────────────┤
│ [Traces] [Logs] [CLI Runs]                       │
├─────────────────────────────────────────────────────────────┤
│ Filters: [Search...] [Status ▼] [Since] [Until]   │
├─────────────────────────────────────────────────────────────┤
│ ID          Name          Status  Started      Dur    │
│ ──────────────────────────────────────────────────────│
│ trc_abc     chat          ok      10:30:00   150ms │
│ trc_def     chat          error   10:29:00   50ms  │
│ trc_ghi     session.new   ok      10:28:00   200ms │
│ ...click row → side panel opens                    │
└─────────────────────────────────────────────────────┘
                    ┌──────────────────────────┐
                    │ Trace Detail       [X] │
                    ├──────────────────────────┤
                    │ Timeline        │
                    │ ●──────────○──●────●──○       │
                    │ 10:28:00 start         │
                    │ 10:28:01 tool_use    │
                    │ 10:28:02 text      │
                    │ 10:28:03 done     │
                    │                  │
                    │ Metadata        │
                    │ request_id: ...  │
                    │ component: ...  │
                    │ attributes: {} │
                    └──────────────────────────┘
```

## Implementation

### Step 1: Table Layout
- Replace vertical list with `<table>` or CSS grid
- Columns: ID, Name, Status, Started, Duration, Component
- Sticky header, scrollable body
- Click row → set selectedTraceId, open side panel

### Step 2: Side Panel Pattern
- Use existing Users.tsx side panel pattern (slides from right)
- Panel shows:
  - Timeline visualization (vertical line with events)
  - Metadata cards
  - Close on ESC key or X button
- Close panel when click outside or press ESC

### Step 3: Rename
- Rename Observability.tsx → Explorer.tsx
- Route: `/observability` → `/explorer`
- Menu: "Explorer" (no section header)

### Future Tabs (Not in Phase 1)
- **Logs:** Similar table + side panel pattern
- **CLI Runs:** Add later with existing backend endpoint

---

## Files to Modify

| File | Change |
|------|-------|
| `ui/src/pages/Observability.tsx` | Rename to Explorer.tsx, refactor to table + side panel |
| `ui/src/App.tsx` | Update route `/explorer`, menu label |

---

## Success Criteria
- [ ] Traces shown in table layout (not vertical list)
- [ ] Click row opens side panel
- [ ] Side panel shows timeline + metadata
- [ ] Panel closes on ESC/X/click outside
- [ ] Consistent with Users.tsx pattern
- [ ] Rename page to Explorer