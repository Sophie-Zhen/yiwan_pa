"""Spike: validate Google Sheets auth + read/write against '6月有易' tab.

Standalone validation, not bot code. Run on Mac:

    pip install gspread
    python scripts/spike_sheets.py

What it does:
    Appends a clearly-marked test row, updates one cell, then deletes the row
    so the sheet is left untouched. Verifies the full auth + API plumbing.
"""

import os

import gspread
from dotenv import load_dotenv

load_dotenv()

CREDS_PATH = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
TAB_NAME = "6月有易"
TEST_MARKER = "SPIKE_TEST_DELETE_ME"


def main() -> None:
    print(f"loading credentials from {CREDS_PATH}")
    client = gspread.service_account(filename=CREDS_PATH)

    print(f"opening spreadsheet {SHEET_ID}")
    spreadsheet = client.open_by_key(SHEET_ID)

    print(f"selecting tab '{TAB_NAME}'")
    tab = spreadsheet.worksheet(TAB_NAME)

    headers = tab.row_values(1)
    print(f"\nheaders ({len(headers)} cols): {headers}")

    rows_before = tab.get_all_values()
    print(f"rows before: {len(rows_before)}")

    test_row = ["1900-01-01", TEST_MARKER, "spike", 1, 0]
    print(f"\nappending test row: {test_row}")
    tab.append_row(test_row, value_input_option="USER_ENTERED")

    rows_after_append = tab.get_all_values()
    new_row_idx = len(rows_after_append)
    print(f"new row at index {new_row_idx}: {rows_after_append[-1]}")
    assert TEST_MARKER in rows_after_append[-1], "test row marker missing after append"

    status_col = 8
    print(f"\nupdating row {new_row_idx} col {status_col} (快递状态) to '在途'")
    tab.update_cell(new_row_idx, status_col, "在途")

    updated_row = tab.row_values(new_row_idx)
    print(f"row after update: {updated_row}")
    assert updated_row[status_col - 1] == "在途", "status update did not stick"

    print(f"\ndeleting test row {new_row_idx}")
    tab.delete_rows(new_row_idx)

    rows_after_delete = tab.get_all_values()
    assert len(rows_after_delete) == len(rows_before), "row count did not return to original"

    print(f"\nspike succeeded. rows unchanged: {len(rows_after_delete)}")


if __name__ == "__main__":
    main()
