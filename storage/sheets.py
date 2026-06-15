"""Shared Google Sheets helpers used by the tools/ domain modules.

These are the pieces that were copy-pasted across tools/expenses.py,
tools/investments.py and tools/parcels.py: opening the service-account client,
the safe 1-based cell read, the "first empty row" banded-range workaround, the
tolerant numeric parse, the upsert merge-helper, and the USER_ENTERED row write.

Deliberately NO ``load_dotenv()`` at import: ``open_sheet`` reads ``os.environ``
at call time (exactly like the old per-module ``_spreadsheet`` did), so the
lazy KeyError-at-call-time behaviour callers rely on is preserved. This module
imports nothing from tools/ — the dependency edge is one-way (tools -> storage).
"""

import os

import gspread


def open_sheet(sheet_id_env: str) -> gspread.Spreadsheet:
    """Service-account client opened on the sheet id held in ``sheet_id_env``.

    Credentials always come from GOOGLE_SHEETS_CREDENTIALS; the spreadsheet id
    is read from the named env var (EXPENSES_SHEET_ID / INVESTMENTS_SHEET_ID /
    GOOGLE_SHEET_ID) at call time, raising KeyError if it is unset.
    """
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    return client.open_by_key(os.environ[sheet_id_env])


def cell(row: list, col: int) -> str:
    """Safe 1-based cell read: ``row[col-1]`` if present, else ``""``."""
    return row[col - 1] if len(row) >= col else ""


def first_empty_row(tab: gspread.Worksheet, key_col: int) -> int:
    """First row (>=2) whose key column is empty.

    Sequentially fills rows 2, 3, ... regardless of phantom structure. This is
    the banded-range workaround: gspread's append_row jumps rows when a banded
    range is present, so callers scan for the first empty key cell instead.
    """
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        if not cell(row, key_col):
            return i
    return len(all_values) + 1


def to_float(raw) -> float:
    """Parse a numeric cell to float; empty / non-numeric / None → 0.0."""
    if not raw:
        return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def keep(existing: list | None, col: int) -> str:
    """Upsert merge-helper: existing 1-based cell value, or ``""`` when there
    is no existing row / it is shorter than ``col``. Lets a partial update
    preserve the fields the caller omitted."""
    if existing is None:
        return ""
    return existing[col - 1] if len(existing) >= col else ""


def write_row(tab: gspread.Worksheet, row_index: int, values: list, last_col: str) -> None:
    """USER_ENTERED single-row write to A{row}:{last_col}{row}."""
    tab.update(
        range_name=f"A{row_index}:{last_col}{row_index}",
        values=[values],
        value_input_option="USER_ENTERED",
    )
