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
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import scheduler
from llm import get_backend
from prompts import (
    render_evening_digest_request,
    render_morning_digest_request,
    render_personal_assistant,
    render_todos_request,
)
from storage.history import append_turn, read_history
from storage.markdown import get_stale_items, mark_stale_alerted

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

backend = get_backend()
USER_NAME = os.getenv("USER_NAME", "the user")
USER_LANGUAGE = os.getenv("USER_LANGUAGE", "Chinese")


# Docker containers default to UTC. We read TZ from the environment so the
# digest fires at the user's wall-clock time. PTB's JobQueue treats a naive
# datetime.time as UTC by default; attaching tzinfo makes it use local time.
LOCAL_TZ = ZoneInfo(os.getenv("TZ", "UTC"))


def _parse_digest_time(value: str) -> time:
    match = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if not match:
        raise ValueError(f"Invalid DIGEST_TIME {value!r}, expected HH:MM (e.g. 08:30)")
    return time(
        hour=int(match.group(1)),
        minute=int(match.group(2)),
        tzinfo=LOCAL_TZ,
    )


DIGEST_TIME = _parse_digest_time(os.getenv("DIGEST_TIME", "08:30"))
EVENING_DIGEST_TIME = _parse_digest_time(os.getenv("EVENING_DIGEST_TIME", "21:00"))

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: int | None = int(_chat_id_env) if _chat_id_env else None


def _is_authorized(update: Update) -> bool:
    """Fail closed: only the configured chat_id may invoke the LLM / tools.

    When TELEGRAM_USER_CHAT_ID is unset, everything except /id is blocked —
    the legit user bootstraps by calling /id, copying the value into .env,
    and restarting the bot.
    """
    if USER_CHAT_ID is None:
        return False
    return update.effective_chat.id == USER_CHAT_ID


async def _ask_llm(
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Run a single LLM round-trip with the personal-assistant system prompt."""
    system_prompt = render_personal_assistant(USER_NAME)
    return await asyncio.to_thread(backend.chat, user_message, system_prompt, history)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (message)", chat_id)
        return
    text = update.message.text
    logger.info("user (chat %s): %s", chat_id, text)

    # Read recent history (sliding window: 6 turns AND last 30 min, whichever
    # is shorter — see storage.history). Empty list on first message.
    history = read_history(chat_id)

    # Inject current wall-clock time so the LLM can resolve relative
    # references ("10 分钟后", "半小时后") and fill `created` accurately.
    # The system prompt deliberately omits HH:MM for prompt-cache stability;
    # putting time on the per-message side keeps the cached prefix intact.
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prefixed = f"[Now: {now_str}]\n{text}"

    try:
        reply = await _ask_llm(prefixed, history=history)
    except Exception as exc:
        logger.exception("backend.chat failed")
        reply = f"[error] {exc}"

    # Persist the ORIGINAL message (not prefixed) so history files stay
    # human-readable and prior turns don't accumulate stale [Now: ...] tags.
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
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (/digest)", update.effective_chat.id)
        return
    try:
        reply = await _ask_llm(render_morning_digest_request(USER_LANGUAGE))
    except Exception as exc:
        logger.exception("digest failed")
        reply = f"[error] {exc}"
    await update.message.reply_text(reply)


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand full pending list, including items with no due date.
    Distinct from /digest, which uses the morning-digest format and only
    surfaces no-due items via the stale filter."""
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (/todos)", update.effective_chat.id)
        return
    try:
        reply = await _ask_llm(render_todos_request(USER_LANGUAGE))
    except Exception as exc:
        logger.exception("todos failed")
        reply = f"[error] {exc}"
    await update.message.reply_text(reply)


async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled task: build the morning digest and push to USER_CHAT_ID.

    Includes a "Stale" section for pending items with no due date that
    haven't been surfaced in the last 7 days. Stale state is mark-on-send
    success: if the LLM call or Telegram push fails, the items are NOT
    marked, so they re-surface tomorrow.
    """
    if USER_CHAT_ID is None:
        logger.warning("daily digest skipped: TELEGRAM_USER_CHAT_ID is not set")
        return

    now = datetime.now()
    stale = get_stale_items(days=7, now=now)
    stale_titles = [it.title for it in stale] or None

    llm_ok = False
    try:
        reply = await _ask_llm(
            render_morning_digest_request(USER_LANGUAGE, stale_titles=stale_titles)
        )
        llm_ok = True
    except Exception as exc:
        logger.exception("daily digest failed")
        reply = f"[digest error] {exc}"

    send_ok = False
    try:
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=reply)
        send_ok = True
    except Exception:
        logger.exception("daily digest send failed")

    if llm_ok and send_ok and stale:
        now_str = now.strftime("%Y-%m-%d %H:%M")
        for it in stale:
            try:
                mark_stale_alerted(it.title, now_str)
            except Exception:
                logger.exception("mark_stale_alerted failed for %r", it.title)


async def send_evening_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled task: evening check-in. If nothing is pending today and
    nothing is overdue, the LLM returns an empty string and the push is
    suppressed (silence is the desired state when there's nothing to nag
    about — see EVENING_DIGEST_TEMPLATE)."""
    if USER_CHAT_ID is None:
        logger.warning("evening digest skipped: TELEGRAM_USER_CHAT_ID is not set")
        return
    try:
        reply = await _ask_llm(render_evening_digest_request(USER_LANGUAGE))
    except Exception as exc:
        logger.exception("evening digest failed")
        reply = f"[evening digest error] {exc}"
    if not reply.strip():
        logger.info("evening digest suppressed: nothing pending today")
        return
    await context.bot.send_message(chat_id=USER_CHAT_ID, text=reply)


async def _set_commands(application: Application) -> None:
    """Register the slash-command menu Telegram shows when the user types '/'.
    Runs once on startup via post_init; idempotent. Keep in sync with the
    CommandHandler list in main()."""
    await application.bot.set_my_commands([
        BotCommand("id", "Show current chat_id"),
        BotCommand("digest", "Trigger today's morning digest"),
        BotCommand("todos", "List all pending items"),
    ])


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_set_commands).build()

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue is None:
        logger.warning(
            "JobQueue is not available — install with `pip install \"python-telegram-bot[job-queue]\"`"
        )
    else:
        app.job_queue.run_daily(send_daily_digest, time=DIGEST_TIME)
        logger.info("daily digest scheduled at %s (system local time)", DIGEST_TIME)
        app.job_queue.run_daily(send_evening_digest, time=EVENING_DIGEST_TIME)
        logger.info("evening digest scheduled at %s (system local time)", EVENING_DIGEST_TIME)
        scheduler.register_jobs(app)

    print("Bot starting... press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
