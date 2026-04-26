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
- `status` — one of `pending`, `done`, `cancelled`

### Optional fields

- `due` — deadline (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`)
- `tags` — space-separated `#category` or `#category/sub` strings
- `notes` — free-form context

## Lifecycle

- **Capture**: a new item is appended to `inbox.md` with `status: pending`
- **Complete / cancel**: the item is moved to `archive.md` with its terminal
  status, all fields preserved
- **Modify**: fields are edited in place; the item stays in `inbox.md` until
  it reaches a terminal status
