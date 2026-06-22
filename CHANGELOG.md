# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project (loosely) follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multi-step projects** (commit `b190f9e`) — items with `type: project` and a `mode` (`sequential` or `parallel`), plus their steps linked via `project: <parent title>`. `sequential` projects enforce "only one step `in_progress` at a time" via the `set_status` tool. The bot asks before cascading on project completion or cancellation. Format spec in [data/README.md](data/README.md).
- **Per-item T-N push alerts** (commit `44a0ed3`) — at capture the user can declare offsets ("提前 3h 2h 提醒" → `alerts: 180,120`); a per-minute scan loop in `scheduler.py` fires reminders as each offset's window arrives. Missed offsets (bot offline / restart) coalesce into a single late-alert message that asks whether to skip the remaining pushes.
- **Evening digest** (commit `44a0ed3`) — daily check-in at `EVENING_DIGEST_TIME` (default 21:00) listing today's still-pending + overdue items. Empty list → push suppressed (silence is the desired output).
- **Morning digest "Stale" section** (commit `44a0ed3`) — items with no due date that haven't been surfaced in >7 days appear as a fourth section. Mark-on-send-success: a failed push re-surfaces the items the next morning rather than silently disappearing.
- **`find_item` tool** (commit `f70f485`) — searches both `inbox.md` and `archive.md`. Closes two trust bugs from v0.1.1 dogfeed where the bot saying "no such item" based on inbox alone overlooked the archived copy.
- **`append_to_notes` tool** (commit `0507fbe`) — appends to existing notes without overwriting. `update_inbox_item(field=notes, ...)` keeps overwrite semantics and its description now warns to use `append_to_notes` for additive intent.
- **`skip_remaining_alerts` tool** (commit `44a0ed3`) — cancels the remaining T-N pushes for an item (e.g. when the user replies "skip flight" to a late alert) without losing the declared `alerts` configuration.
- **Transhipment parcel tracking** — Google Sheet workflow for international forwarding: capture orders, settle carrier-consolidated shipping by weight, apply exchange rate; OCRs e-commerce + warehouse-arrival screenshots. Multi-SKU-per-parcel aware.
- **Fund 定投 (recurring investments)** — Google Sheet plans + debit ledger; records debits / share-confirmations from forwarded bank texts (upsert by date+fund); cumulative-total queries.
- **家庭花销 (household spending)** (PRs #5, #6, #10) — line-item ledger with receipt-photo OCR, price-trend / top-items queries, an inventory watchlist with auto-restock + daily low-stock reminders, and `spend_summary` (records big annual costs as line items so monthly totals have no unexplained gap).
- **`web_search` server tool** (PR #7) — Anthropic-hosted web search for current public facts (phone numbers, opening hours), with `pause_turn` resume handling in the agent loop.
- **Annual contract renewal reminders** (PR #8) — track energy / broadband / insurance expiry in `data/contracts.md`; daily reminder before renewal; rotates price year-over-year for comparison.
- **Document fact-sheets + Q&A** (PR #9) — forward an insurance PDF; Opus extracts a fact-sheet once at ingest (`data/documents/`), then later questions are answered from the distillate without re-reading the file.
- **CWI instructor logbook** (PR #12) — drafts Mountaineering Ireland DLOG entries after a climbing-instruction session, stores brief metadata in `data/cwi_log.md`, tracks progress vs the certificate's logbook targets, and reminds each evening to file pending sessions.
- **Google Drive backup of `data/`** (PR #11) — `scripts/backup_data.sh` rclone-syncs the Pi's runtime data (gitignored, SD-card-only) to Drive with a versioned archive, using the `drive.file` least-privilege scope; cron at 03:00.

### Changed

- **`set_status` is now the only tool that may change status** (commit `b190f9e`). `update_inbox_item.field` no longer accepts `"status"`; status transitions to `done` / `cancelled` also move the item to archive in one step, removing the prior separate `move_to_archive` tool.
- **Telegram whitelist hardened** (commit `c4e1f9b`) — bot only responds to messages from `TELEGRAM_USER_CHAT_ID`; other chats are silently dropped at the handler entry point.
- **Photo albums read together** — when one screenshot can't capture the whole order, send several at once: a multi-photo album (shared `media_group_id`) is now buffered and sent to the vision model as ONE call instead of N unrelated single-image calls, replying once. A lone photo is still processed immediately. The single-photo path is byte-identical to before; the album flush is debounced (`_GROUP_DEBOUNCE_SECONDS`, 3s) to wait out the slow-Pi gap between consecutive photos.

### Fixed

- **Self-contradiction bug** (FIELDNOTES 4/30): bot confirmed an item archived, then five minutes later denied it existed because it re-read inbox during a later capture and read "not in inbox" as "never existed". Closed by `find_item` + a "State checks" section in the system prompt that says: prior confirmations are evidence the item exists; `find_item` before retracting.
- **Silent notes overwrite** (FIELDNOTES 4/30): "再加一项 X" caused `update_inbox_item(field=notes, ...)` to clobber prior notes. Closed by `append_to_notes` (read-then-write, joins with `; `).
- **Archive trust** (FIELDNOTES 4/29): bot claimed an item was archived when in fact it wasn't. Same root-cause family as the self-contradiction bug; `find_item` + State checks close it.
- **`investment_summary` crash on a non-numeric cell** — a stray or hand-typed value in 实际扣款金额 / 确认份额 used to raise and abort the whole summary; it now coerces to `0.0` like the other money parses (via `storage/sheets.py:to_float`).

### For contributors

- **Shared infrastructure extracted** — the Google Sheets boilerplate (auth, first-empty-row workaround, tolerant numeric parse, cell read, row writer) now lives in `storage/sheets.py`; the `## heading` + `- key: value` markdown stores share `storage/md_entities.py`; the four domain reminder schedulers share `scheduler_base.py`. Internal restructuring, no behavior change.
- **Module cohesion** — the 库存 inventory watchlist split out of `tools/expenses.py` into `tools/inventory.py`; the morning/evening digest jobs split out of `bot.py` into `digest_scheduler.py`. Pure moves, behavior preserved.
- **Per-message domain routing** (`router.py`) — ships only the active domain's prompt sections + tools per message, cutting the per-call prefix from ~18k to ~4-7.5k tokens (the dominant API cost was cache-writing the always-on prefix). Behind `ROUTING_ENABLED`, **off by default** — byte-identical to before when off.

## [0.1.1] — 2026-04-29

### Added

- **Conversation history** with sliding window (6 turns OR 30 minutes, whichever is shorter), stored per-chat as JSONL at `data/history/<chat_id>.jsonl`. Bot now resolves short follow-ups like "29号" or "那条改成下周五" against recent context. Design recorded in [ADR 0001](docs/decisions/0001-conversation-history.md).
- **Engineering decisions directory** (`docs/decisions/`) with format/index in `README.md` and the conversation-history record as `0001-conversation-history.md`.

### Fixed

- **Daily digest now fires at the user's wall-clock time.** The Docker container defaulted to UTC and python-telegram-bot's `JobQueue` treats naive `datetime.time` as UTC by default, so `DIGEST_TIME=09:00` fired at 10:00 Europe/Dublin (IST). Added `TZ` env var (consumed by libc + Python) and constructed the digest time with `zoneinfo.ZoneInfo` so PTB schedules in local time.

## [0.1.0] — 2026-04-28

Initial shipped version. End-to-end personal-assistant Telegram bot running 24/7 on a Raspberry Pi.

### Added

- **Telegram bot** with long-polling via `python-telegram-bot` 21+, command handlers (`/id`, `/digest`), and a text message handler for natural-language interaction.
- **Pluggable LLM backend** abstraction (`LLMBackend`) with two implementations selected via the `LLM_BACKEND` env var:
  - `ClaudeCodeBackend` — shells out to the local `claude` CLI, reusing the user's Claude Code subscription auth. Used for development.
  - `AnthropicBackend` — direct `anthropic` SDK with a self-written agent loop, five tools (`read_inbox`, `read_archive`, `append_to_inbox`, `update_inbox_item`, `move_to_archive`), top-level prompt caching, and adaptive thinking. Used in production.
- **Markdown-based storage** at `data/inbox.md` (active todos) and `data/archive.md` (done / cancelled). Python parser in `storage/markdown.py` exposing typed `Item` records and read/write helpers.
- **Personal-assistant system prompt** in `prompts.py` defining four actions: capture, query, complete/cancel, modify.
- **Daily morning digest** scheduled via `JobQueue.run_daily` (default 08:30), pushed to `TELEGRAM_USER_CHAT_ID`.
- **BotFather command menu** registered via `/setcommands` for `/id` and `/digest` autocomplete.
- **Docker deployment**: `Dockerfile` (Python 3.12-slim, multi-arch), `docker-compose.yml` (`restart: unless-stopped`, data volume mount), `.dockerignore`. Production runs as a single container on a Raspberry Pi.
- Project scaffolding: `README.md`, `TODOS.md`, `requirements.txt`, `.env.example`, `.gitignore`.
