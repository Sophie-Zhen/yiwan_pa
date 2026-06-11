"""Restock reminder for 库存 (inventory).

Hourly JobQueue tick. When the tick lands on REMINDER_HOUR (local time,
default 10), it scans the 库存 tab and sends ONE batched shopping-list message
listing every item that has gone low since it was last restocked.

Idempotency: an item's 上次提醒日期 is written only after a successful send.
Each low-stock episode is reminded once — items_needing_restock_reminder
re-arms an item only after a later purchase advances 上次购买日 past
上次提醒日期. The hourly tick + wall-clock-hour check recovers from a restart
window without double-firing (same approach as investment_scheduler).

Batched (one message, not one per item) because the natural use is a single
"what to buy" list, unlike the per-debit 定投 reminders.
"""
import logging
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional

from telegram.ext import Application, ContextTypes

from tools import expenses as exp

logger = logging.getLogger(__name__)

REMINDER_HOUR = int(os.getenv("INVENTORY_REMINDER_HOUR", "10"))
SCAN_INTERVAL_SECONDS = 3600

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: Optional[int] = int(_chat_id_env) if _chat_id_env else None

SendFn = Callable[[int, str], Awaitable[None]]


def _qty_str(q) -> str:
    """Drop the trailing .0 on whole numbers: 1.0 → '1', 0.5 → '0.5'."""
    try:
        f = float(q)
    except (TypeError, ValueError):
        return str(q)
    return str(int(f)) if f.is_integer() else str(f)


def _format_line(it: dict) -> str:
    """One bullet per low item, with the reason made visible.

    threshold → show remaining vs the minimum; cycle → show how long since the
    last purchase (the thing that tripped the cadence).
    """
    remaining = f"{_qty_str(it.get('quantity', ''))} {it.get('unit', '') or ''}".strip()
    if it.get("strategy") == exp.STRATEGY_THRESHOLD:
        return f"• {it['item']}：还剩 {remaining}（阈值 {_qty_str(it.get('threshold', ''))}）"
    days = it.get("days_since_purchase")
    days_txt = f"，已 {days} 天没买" if days is not None else ""
    return f"• {it['item']}：还剩 {remaining}{days_txt}"


async def _scan_once(
    now: datetime,
    send_fn: SendFn,
    force_hour: bool = False,
) -> list[str]:
    """Core scan. Returns the list of item names reminded.

    Args:
        now: wall clock — injected so tests can stub it.
        send_fn: async callable taking (chat_id, text).
        force_hour: if True, skip the REMINDER_HOUR check (manual triggers/tests).
    """
    if USER_CHAT_ID is None:
        return []
    if not force_hour and now.hour != REMINDER_HOUR:
        return []

    today_str = now.date().strftime("%Y-%m-%d")

    try:
        due = exp.items_needing_restock_reminder()
    except Exception:
        logger.exception("inventory scheduler: items_needing_restock_reminder failed")
        return []
    if not due:
        return []

    text = "🛒 补货提醒，以下该买了：\n" + "\n".join(_format_line(it) for it in due)
    try:
        await send_fn(USER_CHAT_ID, text)
    except Exception:
        logger.exception("inventory restock reminder send failed")
        return []

    reminded: list[str] = []
    for it in due:
        try:
            exp.mark_inventory_reminded(it["row"], today_str)
        except Exception:
            # Send succeeded, mark failed — next tick re-fires. One duplicate
            # reminder beats losing the alert.
            logger.exception("mark_inventory_reminded failed after send: %r", it["item"])
        reminded.append(it["item"])

    logger.info("inventory restock reminder fired: %s", reminded)
    return reminded


async def scan_restock_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — runs every SCAN_INTERVAL_SECONDS seconds."""
    async def send(chat_id: int, text: str) -> None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    await _scan_once(datetime.now(), send)


def register_jobs(app: Application) -> None:
    if app.job_queue is None:
        logger.warning(
            "inventory scheduler: JobQueue unavailable, scan_restock_reminders not scheduled"
        )
        return
    # first=360: stagger from the parcels (10) and investment (300) schedulers.
    app.job_queue.run_repeating(
        scan_restock_reminders,
        interval=SCAN_INTERVAL_SECONDS,
        first=360,
    )
    logger.info(
        "inventory scheduler: scan every %ds, fires at hour=%d",
        SCAN_INTERVAL_SECONDS, REMINDER_HOUR,
    )
