"""One-off bootstrap for 定投记录 spreadsheet.

Verifies the service account can open INVESTMENTS_SHEET_ID, then creates the
'计划' and '流水' tabs with header rows if they don't already exist. Idempotent
— safe to re-run.

Run:
    conda run -n assistant python scripts/bootstrap_investments.py
"""

import os

import gspread
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound

load_dotenv()

PLAN_TAB = "计划"
PLAN_HEADERS = ["基金名称", "月扣款日", "计划金额", "起始日", "状态", "上次提醒日期", "备注"]

LEDGER_TAB = "流水"
LEDGER_HEADERS = [
    "扣款日期",
    "基金",
    "计划金额",
    "实际扣款金额",
    "确认日期",
    "确认份额",
    "状态",
    "备注",
]


def ensure_tab(ss: gspread.Spreadsheet, name: str, headers: list[str]) -> None:
    try:
        ws = ss.worksheet(name)
        existing = ws.row_values(1)
        if existing == headers:
            print(f"[skip] tab '{name}' already exists with matching headers")
            return
        print(f"[warn] tab '{name}' exists but headers differ: {existing}")
        return
    except WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=200, cols=max(len(headers), 8))
        ws.update(values=[headers], range_name="A1")
        print(f"[ok] created tab '{name}' with headers: {headers}")


def main() -> None:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    sheet_id = os.environ["INVESTMENTS_SHEET_ID"]
    ss = client.open_by_key(sheet_id)
    print(f"opened spreadsheet: {ss.title}")
    print(f"existing tabs: {[w.title for w in ss.worksheets()]}")

    ensure_tab(ss, PLAN_TAB, PLAN_HEADERS)
    ensure_tab(ss, LEDGER_TAB, LEDGER_HEADERS)

    print("\nfinal tabs:", [w.title for w in ss.worksheets()])


if __name__ == "__main__":
    main()
