# 0006. Per-item scheduler state schema

- **Status**: Decided
- **Date**: 2026-05-20
- **Tags**: #push-strategy #storage

## Problem

The Phase 1 push redesign (per-item T-N alerts, evening digest, morning-digest stale section) needs durable per-item state, otherwise the 60s scan loop re-fires on every tick or loses progress across restarts.

Three pieces of state per item:

1. **Declaration** — which T-N offsets the user wants (e.g. "T-3h, T-2h" → `180,120`).
2. **Fire record** — which declared offsets have already fired, so the scanner can subtract and not duplicate.
3. **Stale-section throttle** — timestamp of the last time an item appeared in the morning digest's stale section, so it isn't surfaced every morning forever.

The decision: where does this state live?

## Context

- Existing storage is `data/inbox.md` — flat list of `## title` blocks with `- key: value` lines. Parsed via `storage/markdown.py` into typed `Item` dataclass instances.
- Scheduler runs in-process via PTB JobQueue `run_repeating`, scanning every 60s. Stateless across ticks except what it persists back to disk.
- Bot restarts often during dev (`docker compose up -d --build`). State must survive restart.
- Single writer — one Pi, one bot process.
- An adjacent constraint: the LLM already manipulates `Item` fields via tools (`append_to_inbox`, `update_inbox_item`, etc.). Any scheduler state on `Item` is visible to the LLM unless explicitly filtered.

## Options considered

| | State location | Atomic with item | Restart-safe | Op-debug ergonomics | New deps |
|---|---|---|---|---|---|
| **A. Fields on `Item`** | new keys in inbox.md | yes | yes | `grep alerts data/inbox.md` | none |
| **B. Sidecar JSON** | `data/scheduler-state.json` keyed by title | no — two writes | yes | open separate file | none |
| **C. SQLite** | `data/scheduler.db` | no — two writes | yes | needs sqlite3 CLI | stdlib `sqlite3` |

B and C were not implemented or prototyped — they were considered conceptually against A's properties and rejected on the points below. The table values for B/C reflect their design shape, not measured behavior.

## Decision

**Option A — three new fields on `Item`, persisted in `data/inbox.md` alongside existing fields:**

- `alerts: Optional[str]` — declared offsets, comma-separated minute integers (e.g. `"180,120"`). `0` means "fire at the due time itself".
- `alerted: Optional[str]` — subset of `alerts` that has fired. `__post_init__` validates `alerted ⊆ alerts`.
- `alerted_stale: Optional[str]` — `"YYYY-MM-DD HH:MM"` timestamp of the most recent appearance in the morning digest stale section.

All three are filtered out of the LLM payload by `llm/anthropic_api.py:_item_payload`. The LLM neither sees them nor is asked to manage them. Scheduler internals stay scheduler internals.

## Rationale

1. **Atomic with the item.** When `move_to_archive` runs, the scheduler state travels with the item — no orphan rows in a sidecar to clean up. When `update_inbox_item` rewrites inbox.md, scheduler state isn't lost.
2. **No two-file drift.** Sidecar approaches need both files in sync. A crash between writing inbox.md and writing the sidecar produces inconsistency; with A the state *is* the item, so a single write is atomic.
3. **Discoverable.** When debugging a misfire on the Pi: `grep -A6 alerts data/inbox.md` shows declared + fired offsets next to the item, in one screen.
4. **Existing pattern, zero conceptual cost.** `storage/markdown.py` already encodes typed fields as `- key: value` lines. Three more is straight-line extension, not a new mechanism.
5. **LLM contamination is contained at one boundary.** `_item_payload` is the single filter. Sidecar-based state still needs filtering somewhere if any tool ever returns it, so option A doesn't worsen that surface.

Trade-off accepted: when an item is archived, `alerts/alerted/alerted_stale` carry into `archive.md`. They're dead weight there (scheduler doesn't read archive) but cause no functional harm. Stripping on archive is deferrable.

## Consequences

**Easier**

- Single source of truth per item; restart safety with no reconciliation step.
- Manual ops: `vim data/inbox.md` can correct an `alerted` value if a misfire happens.
- `mark_alerted` writes one file.

**Harder / new concerns**

- `_item_payload` filter is load-bearing — forget to filter and the LLM starts seeing scheduler internals. No unit test for this currently; protection is "we ship-tested it doesn't leak".
- Archive accumulates dead scheduler fields. Visual noise only.

## Open questions

- **`alerts` value format**: chose comma-separated string (`"180,120"`) to mirror the existing markdown key:value convention. If a future need requires richer per-offset state (which channel fired, retry counts), the field could become structured — but that's a known migration, not blocking.
- **Cleanup on archive**: currently `move_to_archive` carries the scheduler fields through. Strip them if archive.md visually bloats from heavy alert usage.
- **No unit test on `_item_payload` filter**: relying on dogfeed to catch any future leak. Add a regression test the first time a field leaks.

## References

- `storage/markdown.py` — `Item` dataclass; `__post_init__` validates `alerted ⊆ alerts`; helpers `mark_alerted`, `mark_stale_alerted`, `skip_remaining_alerts`, `get_stale_items`.
- `scheduler.py` — 60s scan loop, normal-fire vs late-fire classification, coalesced late-alert messages.
- `llm/anthropic_api.py` — `_item_payload` filter; `skip_remaining_alerts` tool surface.
- TODOS.md — "ADR 0006" entry, closed by this doc.
- FIELDNOTES.md 2026-05-20 — design-call rationale captured fresh.
