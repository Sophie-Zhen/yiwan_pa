"""Per-minute scheduler for T-N alerts.

For each pending inbox item with a `due` (HH:MM precision) and a declared
`alerts` list of offset minutes, the scan loop fires:

- **normal**: when the scan tick crosses T_offset within the tick tolerance
  window (1 minute). One message per offset.
- **late**: when T_offset is already past the tolerance window and the
  offset hasn't been recorded as fired (bot was offline / restarting).
  Multiple missed offsets for the same item are coalesced into one message
  that asks whether to skip remaining alerts.

State (alerts / alerted) lives on Item — see storage.markdown. The user
declares offsets at capture; the scheduler appends to `alerted` only after
the message successfully sends, so a network failure simply retries on the
next tick rather than silently dropping the alert.

Alert text is built in code (no LLM round-trip) — cheap, deterministic,
and survives an API outage. Strings are in English; if the user later
wants Chinese, swap the format helpers or move them to a template module.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from telegram.ext import Application, ContextTypes

from storage.markdown import (
    Item,
    _parse_offsets,
    mark_alerted,
    read_inbox,
)

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60
# A T_offset within this many seconds past `now` counts as "normal fire";
# anything older went through the late-fire path. Equal to scan interval
# so the boundary lines up with one tick of normal scheduler latency.
TICK_TOLERANCE_SECONDS = 60

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: Optional[int] = int(_chat_id_env) if _chat_id_env else None


def _parse_due(due: str) -> Optional[datetime]:
    """Parse `due` as YYYY-MM-DD HH:MM. Returns None for date-only values —
    those items can't have T-N alerts (no time of day to anchor on)."""
    try:
        return datetime.strptime(due, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _humanize(minutes: int) -> str:
    """180 -> '3h', 30 -> '30min', 90 -> '1h30min', 0 -> '0min' (callers
    that want a special phrasing for T-0 should branch before calling)."""
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h{m}min" if m else f"{h}h"
    return f"{minutes}min"


def _classify_alerts(
    item: Item, now: datetime
) -> tuple[list[int], list[int]]:
    """For one item, partition its un-fired declared offsets into
    (normal_now, late_already_past). Empty lists when item has no due,
    no HH:MM due, or no declared alerts."""
    if not item.due:
        return [], []
    due = _parse_due(item.due)
    if due is None:
        return [], []
    declared = _parse_offsets("alerts", item.alerts)
    fired = set(_parse_offsets("alerted", item.alerted))
    normal: list[int] = []
    late: list[int] = []
    for offset in declared:
        if offset in fired:
            continue
        t_offset = due - timedelta(minutes=offset)
        delta = (now - t_offset).total_seconds()
        if 0 <= delta <= TICK_TOLERANCE_SECONDS:
            normal.append(offset)
        elif delta > TICK_TOLERANCE_SECONDS:
            late.append(offset)
    return normal, late


def _format_normal(item: Item, offset: int) -> str:
    if offset == 0:
        return f"⏰ {item.title}\nDue now ({item.due})."
    return (
        f"⏰ {item.title}\n"
        f"T-{_humanize(offset)} reminder (due {item.due})."
    )


def _format_late(
    item: Item, late_offsets: list[int], now: datetime
) -> str:
    """Build a single coalesced late-alert message for one item. Lists
    every missed offset and what would remain if these are marked fired,
    so the user can decide whether to keep or skip the rest."""
    due = _parse_due(item.due)
    assert due is not None  # caller only invokes when late list is non-empty

    def _label(o: int) -> str:
        when = (due - timedelta(minutes=o)).strftime("%H:%M")
        if o == 0:
            return f"due time {when}"
        return f"T-{_humanize(o)} (was {when})"

    parts = [_label(o) for o in late_offsets]
    declared = _parse_offsets("alerts", item.alerts)
    already_fired = set(_parse_offsets("alerted", item.alerted))
    after = already_fired | set(late_offsets)
    remaining = [o for o in declared if o not in after]
    if remaining:
        rem_str = ", ".join(
            "due time" if o == 0 else f"T-{_humanize(o)}" for o in remaining
        )
        tail = (
            f"\nRemaining: {rem_str}. "
            f"Reply 'skip {item.title}' to cancel them."
        )
    else:
        tail = "\nNo remaining alerts."
    return (
        f"⚠️ Late alert: {item.title}\n"
        f"Missed: {', '.join(parts)}. Now {now.strftime('%H:%M')}.{tail}"
    )


async def scan_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — runs every SCAN_INTERVAL_SECONDS seconds.
    Source of truth is inbox.md; this function is stateless across ticks
    apart from what it writes back via mark_alerted.
    """
    if USER_CHAT_ID is None:
        return
    now = datetime.now()
    try:
        items = read_inbox()
    except Exception:
        logger.exception("scan_alerts: read_inbox failed")
        return

    for item in items:
        try:
            normal, late = _classify_alerts(item, now)
        except ValueError:
            logger.exception(
                "scan_alerts: classify failed on %r", item.title
            )
            continue
        if not normal and not late:
            continue

        for offset in normal:
            try:
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=_format_normal(item, offset),
                )
            except Exception:
                logger.exception(
                    "normal alert send failed: %r T-%d", item.title, offset
                )
                continue
            try:
                mark_alerted(item.title, offset)
            except Exception:
                # send succeeded, mark failed — next tick will re-fire this
                # offset (spam). Acceptable trade-off vs losing the record.
                logger.exception(
                    "mark_alerted failed after send: %r T-%d",
                    item.title, offset,
                )
            logger.info("normal alert fired: %r T-%d", item.title, offset)

        if late:
            try:
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=_format_late(item, late, now),
                )
            except Exception:
                logger.exception(
                    "late alert send failed: %r %s", item.title, late
                )
                continue
            for offset in late:
                try:
                    mark_alerted(item.title, offset)
                except Exception:
                    logger.exception(
                        "mark_alerted failed after late send: %r T-%d",
                        item.title, offset,
                    )
            logger.info("late alert fired: %r %s", item.title, late)


def register_jobs(app: Application) -> None:
    """Attach scan_alerts to the application's JobQueue. Called from bot
    main() after the existing daily digest registration."""
    if app.job_queue is None:
        logger.warning(
            "scheduler: JobQueue unavailable, scan_alerts not scheduled"
        )
        return
    app.job_queue.run_repeating(
        scan_alerts, interval=SCAN_INTERVAL_SECONDS, first=10
    )
    logger.info(
        "scheduler: scan_alerts every %ds", SCAN_INTERVAL_SECONDS
    )
