"""T-1 reminder for fund 定投.

Hourly JobQueue tick. When the tick happens to be at REMINDER_HOUR
(local time, default 9), it scans the 计划 tab and fires a reminder for any
plan whose next scheduled debit is *tomorrow*.

Idempotency: a plan's 上次提醒日期 cell is updated only after a successful
Telegram send. If the same hour ticks twice (process restart) the date guard
prevents a duplicate reminder. If the send fails the date is not written, so
the next tick retries.

Why not run once daily at 9? `run_repeating` survives restarts mid-day (a
daily job that fired before restart wouldn't re-arm until tomorrow). Hourly
+ wall-clock-hour check costs nothing and recovers from any restart window.

Month-end handling: a plan with day_of_month=31 against a 30-day month
fires its reminder one day before the actual last-day debit (the bank
typically rolls 31 → 30 for short months).
"""
import calendar
import logging
import os
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Optional

from telegram.ext import Application, ContextTypes

from tools import investments as inv
from tools.investments import (
    FREQUENCY_IRREGULAR,
    FREQUENCY_MONTHLY,
    FREQUENCY_WEEKLY,
)

logger = logging.getLogger(__name__)

REMINDER_HOUR = int(os.getenv("INVESTMENT_REMINDER_HOUR", "9"))
SCAN_INTERVAL_SECONDS = 3600

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: Optional[int] = int(_chat_id_env) if _chat_id_env else None


def _should_remind(plan: dict, today: date) -> bool:
    """True iff tomorrow is the effective debit day for this plan.

    monthly:  tomorrow.day == min(plan['day_of_month'], days_in_tomorrow_month)
              — handles month-end roll (plan day=31 in Feb → fires on the 28th/29th).
    weekly:   tomorrow.isoweekday() == plan['day_of_week']
    irregular: never (no schedule to anchor on).
    """
    freq = plan.get("frequency", "")
    tomorrow = today + timedelta(days=1)
    if freq == FREQUENCY_MONTHLY:
        day_of_month = plan.get("day_of_month")
        if not day_of_month:
            return False
        days_in_tomorrow_month = calendar.monthrange(tomorrow.year, tomorrow.month)[1]
        effective_debit_day = min(int(day_of_month), days_in_tomorrow_month)
        return tomorrow.day == effective_debit_day
    if freq == FREQUENCY_WEEKLY:
        day_of_week = plan.get("day_of_week")
        if not day_of_week:
            return False
        return tomorrow.isoweekday() == int(day_of_week)
    # FREQUENCY_IRREGULAR or unknown — never auto-fire
    return False


_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _format_reminder(plan: dict, today: date) -> str:
    tomorrow = today + timedelta(days=1)
    weekday_cn = _WEEKDAY_CN[tomorrow.weekday()]
    return (
        f"💰 定投提醒：明天（{tomorrow.strftime('%Y-%m-%d')} {weekday_cn}）"
        f"应扣 {plan['fund']} {plan['planned_amount']} 元，记得查余额。"
    )


SendFn = Callable[[int, str], Awaitable[None]]


async def _scan_once(
    now: datetime,
    send_fn: SendFn,
    force_hour: bool = False,
) -> list[str]:
    """Core scan logic. Returns list of fund names that were reminded.

    Args:
        now: wall clock — injected so tests can stub it.
        send_fn: async callable taking (chat_id, text).
        force_hour: if True, skip the REMINDER_HOUR check (manual triggers).
    """
    if USER_CHAT_ID is None:
        return []
    if not force_hour and now.hour != REMINDER_HOUR:
        return []

    today = now.date()
    today_str = today.strftime("%Y-%m-%d")
    fired: list[str] = []

    try:
        plans = inv.list_investment_plans(status_filter="active")
    except Exception:
        logger.exception("investment scheduler: list_investment_plans failed")
        return fired

    for plan in plans:
        try:
            if not _should_remind(plan, today):
                continue
        except (ValueError, TypeError):
            logger.warning(
                "plan %r: malformed schedule fields, skipping",
                plan.get("fund"),
            )
            continue
        if plan["last_reminded"] == today_str:
            continue

        text = _format_reminder(plan, today)
        try:
            await send_fn(USER_CHAT_ID, text)
        except Exception:
            logger.exception("investment reminder send failed: %r", plan["fund"])
            continue
        try:
            inv.mark_plan_reminded(plan["row"], today_str)
        except Exception:
            # Send succeeded, mark failed — next tick (next hour) will spot
            # it again and re-fire. Acceptable in this domain: one duplicate
            # reminder beats losing the alert.
            logger.exception(
                "mark_plan_reminded failed after send: %r", plan["fund"]
            )
        fired.append(plan["fund"])
        logger.info("investment reminder fired: %r", plan["fund"])

    return fired


async def scan_investment_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — runs every SCAN_INTERVAL_SECONDS seconds."""
    async def send(chat_id: int, text: str) -> None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    await _scan_once(datetime.now(), send)


def register_jobs(app: Application) -> None:
    if app.job_queue is None:
        logger.warning(
            "investment scheduler: JobQueue unavailable, scan_investment_reminders not scheduled"
        )
        return
    # first=300: stagger from the parcels scheduler's first=10 and the digest
    # daily jobs. No race risk — they're separate handlers — but uneven first
    # offsets keep the startup log readable.
    app.job_queue.run_repeating(
        scan_investment_reminders,
        interval=SCAN_INTERVAL_SECONDS,
        first=300,
    )
    logger.info(
        "investment scheduler: scan every %ds, fires at hour=%d",
        SCAN_INTERVAL_SECONDS, REMINDER_HOUR,
    )
