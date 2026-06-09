"""One-off: migrate 计划 tab to frequency-aware 8-column schema.

Before: A基金名称 | B月扣款日 | C计划金额 | D起始日 | E状态 | F上次提醒日期 | G备注
After:  A基金名称 | B频率 | C扣款日 | D计划金额 | E起始日 | F状态 | G上次提醒日期 | H备注

频率 ∈ {monthly, weekly, irregular}. 扣款日:
  monthly  → 1-31 (day of month)
  weekly   → 1-7 ISO weekday (1=Mon, 4=Thu, 7=Sun)
  irregular → empty

The migration: clear all data rows, rewrite the header, re-insert the two
existing funds with corrected frequency/day values. Approach is "rewrite
from scratch" because the existing tab has only 2 rows — simpler than
gspread's column-insert dance and idempotent on re-run.

Dry-run by default; pass --apply to actually write.

Run:
    conda run -n assistant python scripts/setup_plan_tab.py
    conda run -n assistant python scripts/setup_plan_tab.py --apply
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

load_dotenv()

PLAN_TAB = "计划"
NEW_HEADERS = [
    "基金名称", "频率", "扣款日", "计划金额", "起始日",
    "状态", "上次提醒日期", "备注",
]

# Hard-coded migration: matches the funds the user already entered.
# Adjust here if the live sheet looks different at the moment of running.
NEW_ROWS = [
    # 基金名称, 频率, 扣款日, 计划金额, 起始日, 状态, 上次提醒日期, 备注
    ["思远定投全球好资产", "irregular", "", 2500, "2026-05-30", "active", "", "信号触发，金额固定"],
    ["富国全球科技互联网100055", "weekly", 4, 1000, "2026-06-04", "active", "", ""],
]


def main() -> None:
    apply = "--apply" in sys.argv

    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    ss = client.open_by_key(os.environ["INVESTMENTS_SHEET_ID"])
    tab = ss.worksheet(PLAN_TAB)

    print("=== current state ===")
    current = tab.get_all_values()
    for i, row in enumerate(current, start=1):
        print(f"  row {i}: {row}")

    print("\n=== target state ===")
    print(f"  row 1 (header): {NEW_HEADERS}")
    for i, row in enumerate(NEW_ROWS, start=2):
        print(f"  row {i}: {row}")

    if not apply:
        print("\n[DRY RUN] re-run with --apply to write")
        return

    print("\n=== applying ===")
    # Clear everything first (header + all rows)
    tab.clear()
    print("[ok] cleared 计划 tab")

    # Re-assert column formats. Necessary because columns retain whatever
    # numberFormat they had under the previous schema — without this, the
    # old D column (起始日, DATE-formatted) reinterprets 2500 → 1906-11-04
    # the moment we write a 计划金额 into it.
    #   C 扣款日 → NUMBER  (1-31 or 1-7)
    #   D 计划金额 → NUMBER
    #   E 起始日 / G 上次提醒日期 → DATE (yyyy-mm-dd)
    tab.format("C:C", {"numberFormat": {"type": "NUMBER", "pattern": "0"}})
    tab.format("D:D", {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}})
    tab.format("E:E", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    tab.format("G:G", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    print("[ok] re-asserted column number formats")

    # Write header + data rows in one batch
    payload = [NEW_HEADERS] + NEW_ROWS
    tab.update(values=payload, range_name="A1", value_input_option="USER_ENTERED")
    print(f"[ok] wrote {len(payload)} rows (header + {len(NEW_ROWS)} data rows)")

    print("\n=== final state ===")
    final = tab.get_all_values()
    for i, row in enumerate(final, start=1):
        print(f"  row {i}: {row}")


if __name__ == "__main__":
    main()
