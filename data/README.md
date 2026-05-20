# data/

Holds the bot's todo state. The actual `inbox.md` and `archive.md` are
gitignored — your todos are private. Only this README is tracked, so anyone
forking the repo knows the file format.

## Files

- `inbox.md` — active todos (status: pending)
- `archive.md` — completed and cancelled items, kept for history

## Item format

Each todo is a level-2 markdown heading followed by a list of fields:

    ## Ryanair check-in for London flight
    - created: 2026-04-26 21:14
    - due: 2026-04-28 09:00
    - status: pending
    - tags: #travel/london
    - notes: Day before flying out on the 29th

### Required fields

- `created` — ISO timestamp when the item was captured (`YYYY-MM-DD HH:MM`)
- `status` — one of `pending`, `in_progress`, `done`, `cancelled`

### Optional fields

- `due` — deadline (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`)
- `tags` — space-separated `#category` or `#category/sub` strings
- `notes` — free-form context
- `type` — `project` only. Marks a multi-step project record. Omit on standalone todos and on steps.
- `mode` — required when `type: project`. One of `sequential` (only one step in_progress at a time, ordered) or `parallel` (any order, multiple in_progress allowed).
- `project` — set on each step belonging to a project. The value is the parent project's title. Don't set on standalone items or on project records themselves.

### Scheduler-internal fields (don't hand-edit)

These three are managed by the bot's push scheduler. They round-trip through the parser but the bot will rewrite them; manual edits are likely to be overwritten or to break the in-progress invariants.

- `alerts` — comma-separated minutes-before-due offsets the user requested as push reminders (e.g. `180,120` = T-3h and T-2h). Set at capture time by the LLM when the user explicitly asks for advance reminders. Only meaningful for items with HH:MM-precision `due`.
- `alerted` — subset of `alerts` whose push has already been delivered. Updated by the scheduler after each successful send. Always a subset of `alerts`.
- `alerted_stale` — timestamp (`YYYY-MM-DD HH:MM`) of the last time this item appeared in the morning digest's stale section. Used to throttle re-surfacing of no-due todos (re-surface every 7 days max).

## Multi-step projects

Some commitments span several ordered or related steps. Store the parent as a project record and each step as its own item that references the project:

    ## 刷漆
    - created: 2026-05-18 10:00
    - type: project
    - mode: sequential
    - status: in_progress
    - due: 2026-06-01
    - notes: 客厅 + 卧室

    ## sand
    - created: 2026-05-18 10:01
    - project: 刷漆
    - status: in_progress
    - due: 2026-05-20

    ## prime
    - created: 2026-05-18 10:01
    - project: 刷漆
    - status: pending
    - due: 2026-05-22

In a `sequential` project, only one step may have `status: in_progress` at any time. The bot enforces this via the `set_status` tool — direct hand edits to `inbox.md` are not validated, so be careful.

## Lifecycle

- **Capture**: a new item is appended to `inbox.md` with `status: pending`
- **Status change**: handled by the `set_status` tool. Non-terminal statuses (`pending`, `in_progress`) update the field in place. Terminal statuses (`done`, `cancelled`) update the field *and* move the item to `archive.md`, all fields preserved.
- **Modify other fields** (title, due, tags, notes): edited in place; the item stays in `inbox.md` until it reaches a terminal status.
- **Project completion**: when all steps of a project are done, the bot asks before archiving the project itself.
- **Project cancellation**: when the user cancels a project, the bot asks before cascading `cancelled` to its pending/in_progress steps.
