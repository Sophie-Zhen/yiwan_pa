"""Hermetic unit test for storage/sheets.py.

Pure-function coverage — no gspread, no network, no credentials. Covers the
helpers the tools/ modules share: cell (safe read), to_float (tolerant parse,
incl. the non-numeric -> 0.0 path that backs the investment_summary crash-fix),
keep (upsert merge), and first_empty_row against a fake worksheet.

Run:
    conda run -n assistant python scripts/test_sheets.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import sheets


class _FakeTab:
    """Minimal stand-in: get_all_values returns the rows it was given."""

    def __init__(self, rows: list[list[str]]):
        self._rows = rows

    def get_all_values(self) -> list[list[str]]:
        return self._rows


def main() -> None:
    print("\n=== cell (safe 1-based read) ===")
    assert sheets.cell(["a", "b", "c"], 1) == "a"
    assert sheets.cell(["a", "b", "c"], 3) == "c"
    assert sheets.cell(["a"], 3) == ""          # short row -> ""
    assert sheets.cell([], 1) == ""             # empty row -> ""
    print("  ok")

    print("\n=== to_float (tolerant parse) ===")
    assert sheets.to_float("12.5") == 12.5
    assert sheets.to_float("0") == 0.0
    assert sheets.to_float("") == 0.0
    assert sheets.to_float("abc") == 0.0        # non-numeric -> 0.0 (was a crash)
    assert sheets.to_float(None) == 0.0
    assert sheets.to_float(7) == 7.0
    print("  ok: non-numeric and None coerce to 0.0 instead of raising")

    print("\n=== keep (upsert merge) ===")
    assert sheets.keep(None, 2) == ""           # no existing row
    assert sheets.keep(["x", "y"], 2) == "y"
    assert sheets.keep(["x"], 2) == ""          # existing shorter than col
    print("  ok")

    print("\n=== first_empty_row ===")
    header = ["h1", "h2"]
    # rows 2,3 have key col (1) filled; row 4 is the first empty key cell
    tab = _FakeTab([header, ["2025-01-01", "x"], ["2025-01-02", "y"]])
    assert sheets.first_empty_row(tab, 1) == 4, sheets.first_empty_row(tab, 1)
    # a gap: row 3's key is blank -> that's the first empty
    tab2 = _FakeTab([header, ["2025-01-01", "x"], ["", "y"], ["2025-01-03", "z"]])
    assert sheets.first_empty_row(tab2, 1) == 3, sheets.first_empty_row(tab2, 1)
    # header only -> first data row is 2
    assert sheets.first_empty_row(_FakeTab([header]), 1) == 2
    print("  ok")

    print("\n[all sheets assertions passed]")


if __name__ == "__main__":
    main()
