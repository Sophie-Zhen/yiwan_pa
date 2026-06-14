"""Shared plumbing for the hourly domain reminder schedulers.

contract_/cwi_/inventory_/investment_scheduler.py each run an hourly JobQueue
tick that, at a per-domain REMINDER_HOUR, scans a store and sends reminders.
The *what to scan and how to phrase/mark it* differs per domain (batched vs
per-plan messages, mark-on-send vs nag-daily), so that logic stays in each
module's _scan_once. What is genuinely identical lives here: the scan interval,
the SendFn type, reading the chat id / reminder hour from the environment, the
"should this tick actually scan?" gate, and JobQueue registration.

This module does NOT call load_dotenv(): contract/cwi load it themselves before
reading USER_CHAT_ID at import (they have no tools-layer .env load to ride on).
should_scan takes chat_id/hour as arguments — read from the domain module's own
globals at call time — so tests that mutate a scheduler's USER_CHAT_ID or
REMINDER_HOUR still take effect.
"""

import logging
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional

from telegram.ext import Application

SCAN_INTERVAL_SECONDS = 3600
SendFn = Callable[[int, str], Awaitable[None]]


def reminder_hour(env_var: str, default: int) -> int:
    """The per-domain REMINDER_HOUR from the environment (local-time hour)."""
    return int(os.getenv(env_var, str(default)))


def load_chat_id() -> Optional[int]:
    """TELEGRAM_USER_CHAT_ID as an int, or None when unset. Each domain assigns
    the result to its own module global so tests can mutate it."""
    raw = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
    return int(raw) if raw else None


def should_scan(
    chat_id: Optional[int], now: datetime, hour: int, force_hour: bool
) -> bool:
    """The gate at the top of every domain _scan_once: scan only when there is a
    chat to send to and (unless forced) the tick lands on the reminder hour."""
    if chat_id is None:
        return False
    if not force_hour and now.hour != hour:
        return False
    return True


def register_hourly(
    app: Application,
    callback,
    *,
    first: int,
    log_label: str,
    reminder_hour: int,
    logger: logging.Logger,
) -> None:
    """Register a domain's hourly scan callback on the JobQueue (or warn if it is
    unavailable). `first` staggers startup so the schedulers' logs don't collide.
    """
    if app.job_queue is None:
        logger.warning(
            "%s: JobQueue unavailable, %s not scheduled", log_label, callback.__name__
        )
        return
    app.job_queue.run_repeating(callback, interval=SCAN_INTERVAL_SECONDS, first=first)
    logger.info(
        "%s: scan every %ds, fires at hour=%d",
        log_label, SCAN_INTERVAL_SECONDS, reminder_hour,
    )
