# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project (loosely) follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
