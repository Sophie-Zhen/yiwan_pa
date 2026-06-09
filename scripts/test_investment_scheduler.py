"""Test investment_scheduler.

Two parts:
  1. Pure unit tests for _should_remind() — covers normal months, Feb,
     month-end roll-over, and a sample of edge cases.
  2. End-to-end via _scan_once with a mock send_fn — creates a synthetic
     plan whose day_of_month matches tomorrow, runs the scan with
     force_hour=True, verifies the mock got called and the plan's
     last_reminded was written. Then it runs scan again and verifies the
     guard prevents a duplicate. Cleans up the plan.

Optional Telegram mode: pass --telegram to actually send the reminder via
your TELEGRAM_BOT_TOKEN to TELEGRAM_USER_CHAT_ID (real e2e). Default is the
mock mode (no Telegram).

Run:
    conda run -n assistant python scripts/test_investment_scheduler.py
    conda run -n assistant python scripts/test_investment_scheduler.py --telegram
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import investment_scheduler as sched
from tools import investments as inv

load_dotenv()


def test_should_remind() -> None:
    print("\n=== _should_remind ===")

    def monthly(day: int) -> dict:
        return {"frequency": "monthly", "day_of_month": day, "day_of_week": None}

    def weekly(day: int) -> dict:
        return {"frequency": "weekly", "day_of_month": None, "day_of_week": day}

    def irregular() -> dict:
        return {"frequency": "irregular", "day_of_month": None, "day_of_week": None}

    # --- monthly ---
    assert sched._should_remind(monthly(10), date(2026, 6, 9))
    assert not sched._should_remind(monthly(10), date(2026, 6, 8))
    # Day 1 plan, today is last day of month → fire
    assert sched._should_remind(monthly(1), date(2026, 5, 31))
    assert sched._should_remind(monthly(1), date(2026, 6, 30))
    # Feb roll non-leap: plan day=31, today=Feb 27, tomorrow=Feb 28=eff(31,28)=28 → fire
    assert sched._should_remind(monthly(31), date(2027, 2, 27))
    # Feb roll leap: plan day=31, today=Feb 28, tomorrow=Feb 29 → fire
    assert sched._should_remind(monthly(31), date(2028, 2, 28))
    # Feb edge: plan day=31, today=Feb 28 non-leap, tomorrow=Mar 1 → no
    assert not sched._should_remind(monthly(31), date(2027, 2, 28))
    # Plan day=30 in Feb non-leap: today=Feb 27, tomorrow=Feb 28=eff(30,28)=28 → fire
    assert sched._should_remind(monthly(30), date(2027, 2, 27))

    # --- weekly ---
    # 2026-06-10 is a Wednesday (isoweekday=3). Tomorrow's day_of_week needs to match.
    # Take a known date pair: 2026-06-09 (Tue) → tomorrow 2026-06-10 (Wed, ISO=3)
    assert sched._should_remind(weekly(3), date(2026, 6, 9))
    # Same date, plan day_of_week=4 → no
    assert not sched._should_remind(weekly(4), date(2026, 6, 9))
    # 2026-06-10 (Wed) → tomorrow 2026-06-11 (Thu, ISO=4) → weekly(4) fires
    assert sched._should_remind(weekly(4), date(2026, 6, 10))
    # 2026-06-13 (Sat) → tomorrow 2026-06-14 (Sun, ISO=7) → weekly(7) fires
    assert sched._should_remind(weekly(7), date(2026, 6, 13))
    # 2026-06-14 (Sun) → tomorrow 2026-06-15 (Mon, ISO=1) → weekly(1) fires
    assert sched._should_remind(weekly(1), date(2026, 6, 14))

    # --- irregular ---
    # Never fires regardless of date
    assert not sched._should_remind(irregular(), date(2026, 6, 9))
    assert not sched._should_remind(irregular(), date(2026, 6, 10))

    print("ok: _should_remind passed all cases (monthly + weekly + irregular)")


async def test_scan_once_mock() -> None:
    """Exercises all 3 frequencies in one scan: a monthly + a weekly plan
    that both come due tomorrow should fire; an irregular plan should not.
    """
    print("\n=== _scan_once with mock send (monthly + weekly + irregular) ===")
    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set; can't run scan path")
        return

    today = date.today()
    tomorrow = today + timedelta(days=1)

    p_monthly = inv.add_investment_plan(
        fund="TEST_SCHED_monthly",
        frequency="monthly",
        day_of_month=tomorrow.day,
        planned_amount=300,
        start_date=today.strftime("%Y-%m-%d"),
        notes="scheduler test monthly",
    )
    p_weekly = inv.add_investment_plan(
        fund="TEST_SCHED_weekly",
        frequency="weekly",
        day_of_week=tomorrow.isoweekday(),
        planned_amount=400,
        start_date=today.strftime("%Y-%m-%d"),
        notes="scheduler test weekly",
    )
    p_irregular = inv.add_investment_plan(
        fund="TEST_SCHED_irregular",
        frequency="irregular",
        planned_amount=500,
        start_date=today.strftime("%Y-%m-%d"),
        notes="scheduler test irregular",
    )
    plan_rows = [p_monthly["row"], p_weekly["row"], p_irregular["row"]]

    sent: list[tuple[int, str]] = []

    async def mock_send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    try:
        fake_now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
        fired = await sched._scan_once(fake_now, mock_send, force_hour=True)
        print(f"first scan fired: {fired}")
        assert "TEST_SCHED_monthly" in fired
        assert "TEST_SCHED_weekly" in fired
        assert "TEST_SCHED_irregular" not in fired, "irregular plans must never auto-fire"
        assert len(sent) == 2
        for _, text in sent:
            assert "明天" in text
            # New format also includes weekday in Chinese
            print(f"sent: {text}")

        fired2 = await sched._scan_once(fake_now, mock_send, force_hour=True)
        print(f"second scan fired: {fired2}")
        assert fired2 == []
        assert len(sent) == 2
        print("ok: idempotency guard suppressed both")
    finally:
        import gspread
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["INVESTMENTS_SHEET_ID"]
        )
        plan_tab = ss.worksheet(inv.PLAN_TAB)
        for r in sorted(plan_rows, reverse=True):
            plan_tab.delete_rows(r)
        print(f"cleaned up plan rows {plan_rows}")


async def test_scan_once_telegram() -> None:
    """Real Telegram round-trip — sends a reminder to TELEGRAM_USER_CHAT_ID."""
    print("\n=== _scan_once with REAL Telegram send ===")
    from telegram import Bot

    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set")
        return

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    today = date.today()
    tomorrow = today + timedelta(days=1)

    plan = inv.add_investment_plan(
        fund="TEST_SCHED_基金（请忽略）",
        frequency="monthly",
        day_of_month=tomorrow.day,
        planned_amount=300,
        start_date=today.strftime("%Y-%m-%d"),
    )

    async def real_send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    try:
        fake_now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
        fired = await sched._scan_once(fake_now, real_send, force_hour=True)
        print(f"fired: {fired}")
        print("→ check Telegram for the reminder message")
    finally:
        import gspread
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["INVESTMENTS_SHEET_ID"]
        )
        ss.worksheet(inv.PLAN_TAB).delete_rows(plan["row"])
        print(f"cleaned up plan row {plan['row']}")


async def main() -> None:
    test_should_remind()
    await test_scan_once_mock()
    if "--telegram" in sys.argv:
        await test_scan_once_telegram()
    print("\n[all scheduler tests passed]")


if __name__ == "__main__":
    asyncio.run(main())
