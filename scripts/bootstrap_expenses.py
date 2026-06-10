"""One-off bootstrap for 家庭花销 spreadsheet (Phase 1).

Verifies the service account can open EXPENSES_SHEET_ID, then creates the
'明细' tab with its header row and column number formats if it doesn't already
exist. Idempotent — safe to re-run.

Run:
    conda run -n assistant python scripts/bootstrap_expenses.py
"""

import os

import gspread
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound

load_dotenv()

LEDGER_TAB = "明细"
LEDGER_HEADERS = [
    "日期", "店铺", "商品", "数量", "单位", "单价", "小计", "类别", "备注",
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
        ws = ss.add_worksheet(title=name, rows=500, cols=max(len(headers), 9))
        ws.update(values=[headers], range_name="A1")
        # Number formats so quantities/prices render cleanly and dates as dates.
        ws.format("A:A", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
        # 数量 is mixed integer (1, 2) and fractional (0.62 kg). General renders
        # both cleanly (1 → "1", not "1."); a fixed pattern like "0.##" leaves a
        # trailing dot on integers.
        ws.format("D:D", {"numberFormat": {"type": "NUMBER", "pattern": "General"}})
        ws.format("F:G", {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}})
        print(f"[ok] created tab '{name}' with headers: {headers}")


def main() -> None:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    sheet_id = os.environ["EXPENSES_SHEET_ID"]
    ss = client.open_by_key(sheet_id)
    print(f"opened spreadsheet: {ss.title}")
    print(f"existing tabs: {[w.title for w in ss.worksheets()]}")

    ensure_tab(ss, LEDGER_TAB, LEDGER_HEADERS)

    print("\nfinal tabs:", [w.title for w in ss.worksheets()])


if __name__ == "__main__":
    main()
