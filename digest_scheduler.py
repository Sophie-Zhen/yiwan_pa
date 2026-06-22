"""Morning and evening digest pushes (scheduled JobQueue tasks).

Split out of bot.py so the timed digest jobs sit alongside the other
*_scheduler.py modules instead of being special-cased in bot.main(). Unlike the
event-driven reminder schedulers, these run_daily at a wall-clock time, gather
todo state, ask the LLM to compose a digest, and push it to the user — so this
is the one scheduler that makes an LLM round-trip.

Self-contained on purpose: it does NOT import bot (that would be a cycle), so it
keeps its own backend / _ask_llm / chat-id, copied verbatim from bot.py — the
mark-on-send-success and empty-reply-suppression invariants are load-bearing and
untested end-to-end, so they must stay byte-for-byte. load_dotenv() runs at
import because USER_CHAT_ID / DIGEST_TIME are read here, before bot.py loads .env.
"""
import asyncio
import logging
import os
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes

from llm import get_backend
from prompts import (
    render_evening_digest_request,
    render_morning_digest_request,
    render_personal_assistant,
)
from storage.markdown import get_stale_items, mark_stale_alerted

load_dotenv()

logger = logging.getLogger(__name__)

backend = get_backend()
USER_NAME = os.getenv("USER_NAME", "the user")
USER_LANGUAGE = os.getenv("USER_LANGUAGE", "Chinese")

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: int | None = int(_chat_id_env) if _chat_id_env else None

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


async def _ask_llm(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    images: list[bytes] | None = None,
    documents: list[bytes] | None = None,
) -> str:
    """Run a single LLM round-trip with the personal-assistant system prompt."""
    system_prompt = render_personal_assistant(USER_NAME)
    # cache=False: digests fire on a cron, hours apart, so the 5-min prompt
    # cache always expires before the next call — caching would only pay the
    # 1.25x write premium for a write that is never read. Bill at 1.0x instead.
    return await asyncio.to_thread(
        backend.chat, user_message, system_prompt, history, images, documents,
        cache=False,
    )


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


def register_jobs(app: Application) -> None:
    """Schedule the daily morning + evening digest pushes at their wall-clock
    times (run_daily, unlike the hourly reminder schedulers)."""
    if app.job_queue is None:
        logger.warning("digest scheduler: JobQueue unavailable, digests not scheduled")
        return
    app.job_queue.run_daily(send_daily_digest, time=DIGEST_TIME)
    logger.info("daily digest scheduled at %s (system local time)", DIGEST_TIME)
    app.job_queue.run_daily(send_evening_digest, time=EVENING_DIGEST_TIME)
    logger.info("evening digest scheduled at %s (system local time)", EVENING_DIGEST_TIME)
