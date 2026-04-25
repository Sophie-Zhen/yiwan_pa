# my-assistant

A personal AI assistant exposed as a Telegram bot. Captures todos via natural
language, sends scheduled daily digests, and tracks deadlines across projects
and personal life.

## v0.1 roadmap

Iterative — each step ends with a runnable artifact.

1. **Echo bot** (current) — verify the Telegram pipeline
2. **LLM abstraction + Claude Code backend** — wire up Claude
3. **Markdown storage + capture flow** — record todos
4. **On-demand and scheduled digests** — query and get reminded
5. **Complete / modify / cancel** — update item status
6. **Push to GitHub private repo**
7. **Anthropic SDK backend** — second pluggable backend
8. **Deploy to Raspberry Pi** — 24/7 uptime

## Local development

```bash
conda create -n assistant python=3.12 -y
conda activate assistant
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your TELEGRAM_BOT_TOKEN
python bot.py
```
