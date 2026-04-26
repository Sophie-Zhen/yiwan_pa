# TODOS

_Last updated: 2026-04-26_

## Current Focus
Build scheduled daily digest (task #8) — JobQueue + /digest /id commands so the bot pushes a morning summary at DIGEST_TIME.

## Open Questions / Blockers
- (none currently)

## Todo
- [ ] Merge feat/capture into main and delete the branch (housekeeping before starting feat/digest)
- [ ] Verify task #9 actions (complete / modify / cancel) work via the existing system prompt — covered in design but not yet tested
- [ ] Implement scheduled daily digest with JobQueue, /id, /digest (task #8)
- [ ] Push to GitHub private repo (task #10)
- [ ] Add AnthropicBackend with self-written agent loop using anthropic SDK (task #11)
- [ ] Deploy to Raspberry Pi with systemd unit for 24/7 uptime (task #12)
- [ ] (pre-release) Register command menu in @BotFather via `/setcommands` so /id, /digest, etc. show up as autocomplete in Telegram
