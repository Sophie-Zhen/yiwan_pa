"""Renewal reminder for annual contracts (data/contracts.md).

Hourly JobQueue tick. At REMINDER_HOUR (local, default 9) it scans contracts
and sends ONE batched message listing every contract whose remind_on has
arrived. The message carries the current price so Sophie has last year's number
to compare against when shopping around.

Idempotency: a contract's last_reminded is written only after a successful
send, and contracts_needing_reminder reminds once per cycle — re-arming only
when renew_contract rolls remind_on forward (and clears last_reminded). Same
shape as investment_scheduler / inventory_scheduler.
"""
import logging
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional

from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes

from tools import contracts as ct

# Load .env explicitly: USER_CHAT_ID is read at import, and this module is
# imported before any other module loads .env (tools.contracts has no env to
# load, unlike tools.expenses/investments). Don't rely on import order.
load_dotenv()

logger = logging.getLogger(__name__)

REMINDER_HOUR = int(os.getenv("CONTRACT_REMINDER_HOUR", "9"))
SCAN_INTERVAL_SECONDS = 3600

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: Optional[int] = int(_chat_id_env) if _chat_id_env else None

SendFn = Callable[[int, str], Awaitable[None]]

_TYPE_CN = {
    "energy": "能源",
    "broadband": "宽带",
    "home_insurance": "房屋险",
    "car_insurance": "车险",
    "other": "合同",
}


def _format_line(c: dict) -> str:
    """One bullet per due contract, with the renewal-decision context visible:
    expiry, the price you're paying now (the number to beat), and last year's
    for trend.
    """
    type_cn = _TYPE_CN.get(c.get("type", ""), c.get("type", ""))
    parts = [f"• {c['name']}（{type_cn}）{c.get('expiry', '')} 到期"]
    days = c.get("days_until_expiry")
    if days is not None:
        if days > 0:
            parts.append(f"，还有 {days} 天")
        elif days == 0:
            parts.append("，就是今天")
        else:
            parts.append(f"，已过期 {-days} 天")
    if c.get("current_price"):
        parts.append(f"。现价 {c['current_price']}")
    if c.get("prev_price"):
        parts.append(f"（去年 {c['prev_price']}）")
    return "".join(parts)


async def _scan_once(
    now: datetime,
    send_fn: SendFn,
    force_hour: bool = False,
) -> list[str]:
    """Core scan. Returns the names of contracts reminded."""
    if USER_CHAT_ID is None:
        return []
    if not force_hour and now.hour != REMINDER_HOUR:
        return []

    today = now.date()
    today_str = today.strftime("%Y-%m-%d")

    try:
        due = ct.contracts_needing_reminder(today)
    except Exception:
        logger.exception("contract scheduler: contracts_needing_reminder failed")
        return []
    if not due:
        return []

    text = "📄 合同续约提醒，记得比价/续约：\n" + "\n".join(_format_line(c) for c in due)
    try:
        await send_fn(USER_CHAT_ID, text)
    except Exception:
        logger.exception("contract reminder send failed")
        return []

    reminded: list[str] = []
    for c in due:
        try:
            ct.mark_contract_reminded(c["name"], today_str)
        except Exception:
            logger.exception("mark_contract_reminded failed after send: %r", c["name"])
        reminded.append(c["name"])

    logger.info("contract reminder fired: %s", reminded)
    return reminded


async def scan_contract_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — runs every SCAN_INTERVAL_SECONDS seconds."""
    async def send(chat_id: int, text: str) -> None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    await _scan_once(datetime.now(), send)


def register_jobs(app: Application) -> None:
    if app.job_queue is None:
        logger.warning(
            "contract scheduler: JobQueue unavailable, scan_contract_reminders not scheduled"
        )
        return
    # first=420: stagger from parcels (10), investment (300), inventory (360).
    app.job_queue.run_repeating(
        scan_contract_reminders,
        interval=SCAN_INTERVAL_SECONDS,
        first=420,
    )
    logger.info(
        "contract scheduler: scan every %ds, fires at hour=%d",
        SCAN_INTERVAL_SECONDS, REMINDER_HOUR,
    )
