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

INVENTORY_TAB = "库存"
INVENTORY_HEADERS = [
    "商品", "当前数量", "单位", "补货策略", "阈值",
    "上次购买日", "上次单价", "状态", "上次提醒日期", "备注",
]


# Number formats per tab. General renders mixed integer/fractional quantities
# cleanly (1 → "1", not "1."); a fixed pattern like "0.##" leaves a trailing
# dot on integers. Prices use 0.00; dates use the ISO date pattern.
NUMBER = lambda pat: {"numberFormat": {"type": "NUMBER", "pattern": pat}}  # noqa: E731
DATE = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}

LEDGER_FORMATS = [("A:A", DATE), ("D:D", NUMBER("General")), ("F:G", NUMBER("0.00"))]
INVENTORY_FORMATS = [
    ("B:B", NUMBER("General")),  # 当前数量 (mixed int/fraction)
    ("E:E", NUMBER("General")),  # 阈值 (qty or interval-days)
    ("F:F", DATE),               # 上次购买日
    ("G:G", NUMBER("0.00")),     # 上次单价
    ("I:I", DATE),               # 上次提醒日期
]


def ensure_tab(
    ss: gspread.Spreadsheet,
    name: str,
    headers: list[str],
    formats: list[tuple[str, dict]],
) -> None:
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
        for rng, fmt in formats:
            ws.format(rng, fmt)
        print(f"[ok] created tab '{name}' with headers: {headers}")


def main() -> None:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    sheet_id = os.environ["EXPENSES_SHEET_ID"]
    ss = client.open_by_key(sheet_id)
    print(f"opened spreadsheet: {ss.title}")
    print(f"existing tabs: {[w.title for w in ss.worksheets()]}")

    ensure_tab(ss, LEDGER_TAB, LEDGER_HEADERS, LEDGER_FORMATS)
    ensure_tab(ss, INVENTORY_TAB, INVENTORY_HEADERS, INVENTORY_FORMATS)

    print("\nfinal tabs:", [w.title for w in ss.worksheets()])


if __name__ == "__main__":
    main()
