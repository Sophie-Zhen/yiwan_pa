"""my-assistant v0.1 — iteration 4: scheduled morning digest.

On top of iteration 3 (capture / query / complete via system prompt), this
adds:

- /id command — replies with the current chat's ID, so the user can copy it
  into TELEGRAM_USER_CHAT_ID in .env (needed for scheduled push).
- /digest command — triggers an immediate digest, mainly for testing the
  digest pipeline without waiting for the scheduled time.
- Daily JobQueue task — every day at DIGEST_TIME, builds and pushes the
  digest to TELEGRAM_USER_CHAT_ID in USER_LANGUAGE.
"""
import asyncio
import logging
import os
import re
from datetime import time

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from llm import get_backend
from prompts import render_morning_digest_request, render_personal_assistant
from storage.history import append_turn, read_history

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

backend = get_backend()
USER_NAME = os.getenv("USER_NAME", "the user")
USER_LANGUAGE = os.getenv("USER_LANGUAGE", "Chinese")


def _parse_digest_time(value: str) -> time:
    match = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if not match:
        raise ValueError(f"Invalid DIGEST_TIME {value!r}, expected HH:MM (e.g. 08:30)")
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


DIGEST_TIME = _parse_digest_time(os.getenv("DIGEST_TIME", "08:30"))

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: int | None = int(_chat_id_env) if _chat_id_env else None


async def _ask_llm(
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Run a single LLM round-trip with the personal-assistant system prompt."""
    system_prompt = render_personal_assistant(USER_NAME)
    return await asyncio.to_thread(backend.chat, user_message, system_prompt, history)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id
    logger.info("user (chat %s): %s", chat_id, text)

    # Read recent history (sliding window: 6 turns AND last 30 min, whichever
    # is shorter — see storage.history). Empty list on first message.
    history = read_history(chat_id)

    try:
        reply = await _ask_llm(text, history=history)
    except Exception as exc:
        logger.exception("backend.chat failed")
        reply = f"[error] {exc}"

    # Persist the turn so the next message sees this exchange in its history.
    # Stored as user + final assistant text only (no tool_use trajectory) —
    # see docs/decisions/0001-conversation-history.md.
    append_turn(chat_id, text, reply)

    logger.info("bot: %s", reply[:200])
    await update.message.reply_text(reply)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"chat_id: {chat_id}\n\n"
        f"Paste this into your .env as TELEGRAM_USER_CHAT_ID to enable scheduled pushes."
    )


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand digest — useful for testing without waiting for the scheduled time."""
    try:
        reply = await _ask_llm(render_morning_digest_request(USER_LANGUAGE))
    except Exception as exc:
        logger.exception("digest failed")
        reply = f"[error] {exc}"
    await update.message.reply_text(reply)


async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled task: build the morning digest and push to USER_CHAT_ID."""
    if USER_CHAT_ID is None:
        logger.warning("daily digest skipped: TELEGRAM_USER_CHAT_ID is not set")
        return
    try:
        reply = await _ask_llm(render_morning_digest_request(USER_LANGUAGE))
    except Exception as exc:
        logger.exception("daily digest failed")
        reply = f"[digest error] {exc}"
    await context.bot.send_message(chat_id=USER_CHAT_ID, text=reply)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue is None:
        logger.warning(
            "JobQueue is not available — install with `pip install \"python-telegram-bot[job-queue]\"`"
        )
    else:
        app.job_queue.run_daily(send_daily_digest, time=DIGEST_TIME)
        logger.info("daily digest scheduled at %s (system local time)", DIGEST_TIME)

    print("Bot starting... press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
