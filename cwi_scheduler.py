"""Evening reminder to enter the day's CWI sessions into Mountaineering Ireland's
DLOG (data/cwi_log.md).

Hourly JobQueue tick. At REMINDER_HOUR (local, default 19) it checks for any log
entries still in 'pending' status (logged here but not yet entered into the MI
DLOG) and sends ONE batched nudge. Sophie drafts each entry in chat right after
a session; this reminds her, once she's home from work, to paste them into the
DLOG while the day is still fresh.

Unlike contract / inventory reminders there is no per-entry last_reminded: a
pending entry is meant to nag each evening until she marks it recorded (reply
'录好了' → cwi_log.mark_recorded). Same JobQueue shape as the other schedulers.
"""
import logging
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes

import scheduler_base
from tools import cwi_log

# Load .env explicitly: USER_CHAT_ID is read at import, before bot.py has loaded
# .env. Same fix as contract_scheduler — don't rely on import order.
load_dotenv()

logger = logging.getLogger(__name__)

REMINDER_HOUR = scheduler_base.reminder_hour("CWI_REMINDER_HOUR", 19)
USER_CHAT_ID: Optional[int] = scheduler_base.load_chat_id()

_KIND_CN = {"instructed": "带组", "personal": "个人攀岩"}


def _format_line(e: dict) -> str:
    kind_cn = _KIND_CN.get(e.get("kind", ""), e.get("kind", ""))
    parts = [f"• {e.get('date', '')} {kind_cn}"]
    if e.get("venue"):
        parts.append(f" @ {e['venue']}")
    if e.get("detail"):
        parts.append(f"（{e['detail']}）")
    return "".join(parts)


async def _scan_once(
    now: datetime,
    send_fn: scheduler_base.SendFn,
    force_hour: bool = False,
) -> list[int]:
    """Core scan. Returns the ids of entries reminded about."""
    if not scheduler_base.should_scan(USER_CHAT_ID, now, REMINDER_HOUR, force_hour):
        return []

    try:
        pending = cwi_log.pending_entries()
    except Exception:
        logger.exception("cwi scheduler: pending_entries failed")
        return []
    if not pending:
        return []

    text = (
        "🧗 还没录进 MI DLOG 的 session，趁记忆新鲜录一下：\n"
        + "\n".join(_format_line(e) for e in pending)
        + "\n\n录好了回我一句就标记完成。"
    )
    try:
        await send_fn(USER_CHAT_ID, text)
    except Exception:
        logger.exception("cwi reminder send failed")
        return []

    ids = [e["id"] for e in pending]
    logger.info("cwi reminder fired: %s", ids)
    return ids


async def scan_cwi_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — runs every SCAN_INTERVAL_SECONDS seconds."""
    async def send(chat_id: int, text: str) -> None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    await _scan_once(datetime.now(), send)


def register_jobs(app: Application) -> None:
    # first=480: stagger from parcels(10)/investment(300)/inventory(360)/contract(420).
    scheduler_base.register_hourly(
        app, scan_cwi_reminders,
        first=480, log_label="cwi scheduler",
        reminder_hour=REMINDER_HOUR, logger=logger,
    )
