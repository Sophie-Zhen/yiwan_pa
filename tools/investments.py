"""Tools for tracking 基金定投 (recurring fund investments) in Google Sheets.

Two tabs in the INVESTMENTS_SHEET_ID spreadsheet:

「计划」(plan) — small, ~5-10 rows. One row per active 定投 contract.
    A 基金名称       | input
    B 频率           | one of VALID_FREQUENCIES (monthly / weekly / irregular)
    C 扣款日         | input — depends on frequency:
                        monthly  → 1-31 (day of month)
                        weekly   → 1-7 (ISO weekday, 1=Mon, 4=Thu, 7=Sun)
                        irregular → empty
    D 计划金额       | input (RMB per debit)
    E 起始日         | input (YYYY-MM-DD)
    F 状态           | one of VALID_PLAN_STATUSES
    G 上次提醒日期   | written by scheduler at T-1
    H 备注           | input

「流水」(ledger) — append-only. One row per debit event.
    A 扣款日期       | input (YYYY-MM-DD)
    B 基金           | input (matches a plan name)
    C 计划金额       | input (copied from plan at record time)
    D 实际扣款金额   | input (from bank notification)
    E 确认日期       | input later, when fund confirms shares
    F 确认份额       | input later
    G 状态           | one of VALID_LEDGER_STATUSES
    H 备注           | input

Why two-stage record: in China 基金 commonly debits on day T but confirms
shares on T+1..T+3. The user forwards bank texts when they arrive, which may
be a debit-only text (record_investment with only A-D) or a consolidated text
(record_investment with A-H), or a separate confirmation text (find_investment
then update_investment_confirmation).
"""

import gspread
from dotenv import load_dotenv

from storage import sheets

load_dotenv()

PLAN_TAB = "计划"
PLAN_COL_FUND = 1
PLAN_COL_FREQUENCY = 2
PLAN_COL_DAY = 3
PLAN_COL_AMOUNT = 4
PLAN_COL_START = 5
PLAN_COL_STATUS = 6
PLAN_COL_LAST_REMINDED = 7
PLAN_COL_NOTES = 8
PLAN_NUM_COLS = 8
PLAN_LAST_COL = "H"

FREQUENCY_MONTHLY = "monthly"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_IRREGULAR = "irregular"
VALID_FREQUENCIES = {FREQUENCY_MONTHLY, FREQUENCY_WEEKLY, FREQUENCY_IRREGULAR}

LEDGER_TAB = "流水"
LEDGER_COL_DEBIT_DATE = 1
LEDGER_COL_FUND = 2
LEDGER_COL_PLANNED = 3
LEDGER_COL_ACTUAL = 4
LEDGER_COL_CONFIRM_DATE = 5
LEDGER_COL_SHARES = 6
LEDGER_COL_STATUS = 7
LEDGER_COL_NOTES = 8
LEDGER_NUM_COLS = 8
LEDGER_LAST_COL = "H"

VALID_PLAN_STATUSES = {"active", "paused", "ended"}
VALID_LEDGER_STATUSES = {"已扣款", "已确认", "已跳过", "失败"}


def _plan_tab() -> gspread.Worksheet:
    return sheets.open_sheet("INVESTMENTS_SHEET_ID").worksheet(PLAN_TAB)


def _ledger_tab() -> gspread.Worksheet:
    return sheets.open_sheet("INVESTMENTS_SHEET_ID").worksheet(LEDGER_TAB)


def add_investment_plan(
    fund: str,
    frequency: str,
    planned_amount: float,
    start_date: str,
    day_of_month: int | None = None,
    day_of_week: int | None = None,
    notes: str | None = None,
) -> dict:
    """Add a new 定投 plan. status defaults to 'active'.

    frequency selects which schedule field is required:
      - 'monthly'   → day_of_month required (1-31), day_of_week ignored
      - 'weekly'    → day_of_week required (1-7, ISO: 1=Mon, 7=Sun)
      - 'irregular' → neither used; no auto-reminder will fire
    """
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(
            f"Invalid frequency {frequency!r}. Must be one of: {sorted(VALID_FREQUENCIES)}"
        )

    schedule_day: int | str = ""
    if frequency == FREQUENCY_MONTHLY:
        if day_of_month is None or not 1 <= day_of_month <= 31:
            raise ValueError(
                f"frequency='monthly' requires day_of_month 1-31, got {day_of_month}"
            )
        schedule_day = day_of_month
    elif frequency == FREQUENCY_WEEKLY:
        if day_of_week is None or not 1 <= day_of_week <= 7:
            raise ValueError(
                f"frequency='weekly' requires day_of_week 1-7 (ISO), got {day_of_week}"
            )
        schedule_day = day_of_week

    tab = _plan_tab()
    target_row = sheets.first_empty_row(tab, PLAN_COL_FUND)

    row = [""] * PLAN_NUM_COLS
    row[PLAN_COL_FUND - 1] = fund
    row[PLAN_COL_FREQUENCY - 1] = frequency
    row[PLAN_COL_DAY - 1] = schedule_day
    row[PLAN_COL_AMOUNT - 1] = planned_amount
    row[PLAN_COL_START - 1] = start_date
    row[PLAN_COL_STATUS - 1] = "active"
    if notes is not None:
        row[PLAN_COL_NOTES - 1] = notes

    sheets.write_row(tab, target_row, row, PLAN_LAST_COL)
    return {"row": target_row, "fund": fund, "frequency": frequency}


def list_investment_plans(status_filter: str | None = "active") -> list[dict]:
    """List plans. status_filter=None means all; default 'active'.

    The schedule_day cell holds either a day-of-month (1-31), a day-of-week
    (1-7 ISO), or empty (irregular). This function splits it into
    day_of_month and day_of_week fields based on the row's frequency so
    callers don't have to interpret the polymorphic cell themselves.
    """
    tab = _plan_tab()
    all_values = tab.get_all_values()
    plans: list[dict] = []
    for i, row in enumerate(all_values[1:], start=2):
        fund = sheets.cell(row, PLAN_COL_FUND)
        if not fund:
            continue
        status = sheets.cell(row, PLAN_COL_STATUS)
        if status_filter is not None and status != status_filter:
            continue
        frequency = sheets.cell(row, PLAN_COL_FREQUENCY)
        schedule_day_raw = sheets.cell(row, PLAN_COL_DAY)
        day_of_month: int | None = None
        day_of_week: int | None = None
        if frequency == FREQUENCY_MONTHLY and schedule_day_raw:
            day_of_month = int(schedule_day_raw)
        elif frequency == FREQUENCY_WEEKLY and schedule_day_raw:
            day_of_week = int(schedule_day_raw)

        plans.append({
            "row": i,
            "fund": fund,
            "frequency": frequency,
            "day_of_month": day_of_month,
            "day_of_week": day_of_week,
            "planned_amount": sheets.cell(row, PLAN_COL_AMOUNT),
            "start_date": sheets.cell(row, PLAN_COL_START),
            "status": status,
            "last_reminded": sheets.cell(row, PLAN_COL_LAST_REMINDED),
            "notes": sheets.cell(row, PLAN_COL_NOTES),
        })
    return plans


def mark_plan_reminded(row: int, date_str: str) -> dict:
    """Set 上次提醒日期 on a plan row. Internal helper used by the T-1 scheduler;
    NOT exposed as an LLM tool. Idempotency lives here: the scheduler only
    sends a reminder when last_reminded != today, and updates it after a
    successful Telegram send.
    """
    tab = _plan_tab()
    tab.update_cell(row, PLAN_COL_LAST_REMINDED, date_str)
    return {"row": row, "last_reminded": date_str}


def update_plan_status(fund: str, status: str) -> dict:
    """Change a plan's status. Matches fund by exact name."""
    if status not in VALID_PLAN_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(VALID_PLAN_STATUSES)}"
        )
    tab = _plan_tab()
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        name = sheets.cell(row, PLAN_COL_FUND)
        if name == fund:
            tab.update_cell(i, PLAN_COL_STATUS, status)
            return {"row": i, "fund": fund, "status": status}
    raise ValueError(f"No plan found with fund={fund!r}")


def record_investment(
    debit_date: str,
    fund: str,
    planned_amount: float,
    actual_amount: float,
    confirm_date: str | None = None,
    shares: float | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> dict:
    """Upsert a debit event to 流水, keyed by (debit_date, fund).

    If a row with the same debit_date AND fund already exists, it is updated
    in place: non-None fields overwrite; None preserves existing values.
    Otherwise a new row is inserted.

    This dedup at the data layer is intentional: the LLM cannot reliably
    decide "is this a new debit or a follow-up confirmation?" from the text
    alone (consolidated bank texts contain both events; a confirmation-only
    text and an order-only text look similar in structure). Making the
    primary tool idempotent removes the failure mode entirely.

    Status defaulting (applied AFTER merging existing + new):
      - If status is passed explicitly, use it.
      - Else if final confirm_date and shares are both filled, '已确认'.
      - Else '已扣款'.

    Returns operation='inserted' or 'updated' so callers can phrase replies.
    """
    tab = _ledger_tab()
    all_values = tab.get_all_values()

    existing_row: int | None = None
    existing: list[str] | None = None
    for i, row in enumerate(all_values[1:], start=2):
        d = sheets.cell(row, LEDGER_COL_DEBIT_DATE)
        f = sheets.cell(row, LEDGER_COL_FUND)
        if d == debit_date and f == fund:
            existing_row = i
            existing = row
            break

    final_confirm_date = confirm_date if confirm_date is not None else sheets.keep(existing, LEDGER_COL_CONFIRM_DATE)
    final_shares = shares if shares is not None else sheets.keep(existing, LEDGER_COL_SHARES)
    final_notes = notes if notes is not None else sheets.keep(existing, LEDGER_COL_NOTES)

    if status is None:
        status = "已确认" if (final_confirm_date and final_shares not in ("", None)) else "已扣款"
    if status not in VALID_LEDGER_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(VALID_LEDGER_STATUSES)}"
        )

    row = [""] * LEDGER_NUM_COLS
    row[LEDGER_COL_DEBIT_DATE - 1] = debit_date
    row[LEDGER_COL_FUND - 1] = fund
    row[LEDGER_COL_PLANNED - 1] = planned_amount
    row[LEDGER_COL_ACTUAL - 1] = actual_amount
    if final_confirm_date:
        row[LEDGER_COL_CONFIRM_DATE - 1] = final_confirm_date
    if final_shares not in ("", None):
        row[LEDGER_COL_SHARES - 1] = final_shares
    row[LEDGER_COL_STATUS - 1] = status
    if final_notes:
        row[LEDGER_COL_NOTES - 1] = final_notes

    target_row = existing_row if existing_row is not None else sheets.first_empty_row(tab, LEDGER_COL_DEBIT_DATE)
    sheets.write_row(tab, target_row, row, LEDGER_LAST_COL)
    return {
        "row": target_row,
        "fund": fund,
        "debit_date": debit_date,
        "status": status,
        "operation": "updated" if existing_row is not None else "inserted",
    }


def find_investment(
    debit_date: str | None = None,
    fund: str | None = None,
    pending_only: bool = False,
) -> list[dict]:
    """Find ledger rows.

    - debit_date: exact match on column A
    - fund: substring match on column B (case-insensitive)
    - pending_only: only rows whose status == '已扣款' (awaiting share confirmation)

    All filters AND together. If all None/False, returns every row.
    """
    tab = _ledger_tab()
    all_values = tab.get_all_values()
    matches: list[dict] = []
    fund_lower = fund.lower() if fund else None
    for i, row in enumerate(all_values[1:], start=2):
        d = sheets.cell(row, LEDGER_COL_DEBIT_DATE)
        f = sheets.cell(row, LEDGER_COL_FUND)
        s = sheets.cell(row, LEDGER_COL_STATUS)
        if not d:
            continue
        if debit_date is not None and d != debit_date:
            continue
        if fund_lower is not None and fund_lower not in f.lower():
            continue
        if pending_only and s != "已扣款":
            continue
        matches.append({
            "row": i,
            "debit_date": d,
            "fund": f,
            "planned_amount": sheets.cell(row, LEDGER_COL_PLANNED),
            "actual_amount": sheets.cell(row, LEDGER_COL_ACTUAL),
            "confirm_date": sheets.cell(row, LEDGER_COL_CONFIRM_DATE),
            "shares": sheets.cell(row, LEDGER_COL_SHARES),
            "status": s,
            "notes": sheets.cell(row, LEDGER_COL_NOTES),
        })
    return matches


def update_investment_confirmation(
    row: int,
    confirm_date: str,
    shares: float,
) -> dict:
    """Fill in 确认日期/确认份额 on an existing 流水 row and set status='已确认'.

    Use find_investment(pending_only=True) first to locate the row.
    """
    tab = _ledger_tab()
    cells = [
        gspread.cell.Cell(row, LEDGER_COL_CONFIRM_DATE, confirm_date),
        gspread.cell.Cell(row, LEDGER_COL_SHARES, shares),
        gspread.cell.Cell(row, LEDGER_COL_STATUS, "已确认"),
    ]
    tab.update_cells(cells, value_input_option="USER_ENTERED")
    return {"row": row, "confirm_date": confirm_date, "shares": shares, "status": "已确认"}


def investment_summary(
    fund: str | None = None,
    year: int | None = None,
) -> dict:
    """Aggregate totals from 流水. Optional filters: fund (exact), year (扣款日期 prefix)."""
    tab = _ledger_tab()
    all_values = tab.get_all_values()

    total_debited = 0.0
    total_shares = 0.0
    rows_count = 0
    pending_count = 0
    by_fund: dict[str, dict] = {}

    year_prefix = f"{year}-" if year is not None else None

    for row in all_values[1:]:
        d = sheets.cell(row, LEDGER_COL_DEBIT_DATE)
        f = sheets.cell(row, LEDGER_COL_FUND)
        s = sheets.cell(row, LEDGER_COL_STATUS)
        if not d:
            continue
        if fund is not None and f != fund:
            continue
        if year_prefix is not None and not d.startswith(year_prefix):
            continue
        if s == "已跳过" or s == "失败":
            continue

        actual_raw = sheets.cell(row, LEDGER_COL_ACTUAL)
        shares_raw = sheets.cell(row, LEDGER_COL_SHARES)
        actual = sheets.to_float(actual_raw)
        sh = sheets.to_float(shares_raw)

        total_debited += actual
        total_shares += sh
        rows_count += 1
        if s == "已扣款":
            pending_count += 1

        bucket = by_fund.setdefault(f, {"debited": 0.0, "shares": 0.0, "rows": 0})
        bucket["debited"] += actual
        bucket["shares"] += sh
        bucket["rows"] += 1

    return {
        "filter": {"fund": fund, "year": year},
        "total_debited_rmb": round(total_debited, 2),
        "total_shares_confirmed": round(total_shares, 4),
        "rows_count": rows_count,
        "pending_confirmations_count": pending_count,
        "by_fund": {
            k: {"debited": round(v["debited"], 2), "shares": round(v["shares"], 4), "rows": v["rows"]}
            for k, v in by_fund.items()
        },
    }
