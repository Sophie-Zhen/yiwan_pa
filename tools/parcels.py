"""Tools for managing transhipment parcels in Google Sheets.

The "active tab" is the current shipment batch (e.g. "6月有易"). It is set via
the /active command in the bot and persisted to data/active_tab.txt so it
survives bot restarts.

Workflow has three stages, each handled by different tools:

Stage 1 — Capture (frequent, while shopping):
    record_parcel: append a new row with input columns 1-10.
    update_parcel: status / tracking / weight as info comes in.
    find_parcel: locate rows by 商品名称 substring or 国内快递单号 substring.

Stage 2 — Settle shipping (once, after carrier consolidates the batch):
    settle_shipping(total_billed_weight, total_shipping_rmb):
        Adds a summary row at the bottom (商品名称 = 'summary') containing
        the carrier-reported totals. Then writes apportioning formulas to
        columns K/L/M/N of every data row.

Stage 3 — Exchange rate (after Alipay rate is known):
    apply_exchange_rate(rate):
        Writes literal exchange rate to column O of every data row, plus
        EUR-conversion formulas to columns P and Q.

Sheet layout (column number is 1-based):
    1  A  购买日期         | input
    2  B  商品名称         | input (also 'summary' for summary row)
    3  C  购买平台         | input
    4  D  数量             | input (or formula on summary row)
    5  E  单价             | input or formula (depends on what user gave)
    6  F  采购总价         | input or formula (depends on what user gave)
    7  G  国内快递单号     | input (later, after shipment)
    8  H  快递状态         | input; one of VALID_STATUSES
    9  I  国内包裹重量     | input (later, after warehouse weighs)
    10 J  转运渠道         | inferred from tab name
    11 K  渠道单价/公斤    | formula written in stage 2
    12 L  总转运重量       | formula written in stage 2 (apportioned)
    13 M  实际支付运费     | formula written in stage 2
    14 N  合计成本 RMB     | formula written in stage 2
    15 O  汇率             | literal written in stage 3
    16 P  合计成本 EUR     | formula written in stage 3
    17 Q  欧元单价         | formula written in stage 3
    18 R  备注             | input
"""

import os
from pathlib import Path

import gspread
from dotenv import load_dotenv

load_dotenv()

ACTIVE_TAB_FILE = Path("data/active_tab.txt")

COL_DATE = 1
COL_ITEM = 2
COL_PLATFORM = 3
COL_QUANTITY = 4
COL_UNIT_PRICE = 5
COL_TOTAL_COST = 6
COL_TRACKING = 7
COL_STATUS = 8
COL_DOMESTIC_WEIGHT = 9
COL_CHANNEL = 10
COL_CHANNEL_RATE = 11
COL_TOTAL_WEIGHT = 12
COL_PAID_SHIPPING = 13
COL_GRAND_TOTAL_RMB = 14
COL_EXCHANGE_RATE = 15
COL_GRAND_TOTAL_EUR = 16
COL_EURO_UNIT_PRICE = 17
COL_NOTES = 18

NUM_COLS = 18
LAST_COL_LETTER = "R"

SUMMARY_MARKER = "summary"
VALID_STATUSES = {"未发货", "在途", "已签收", "已入库拍照"}


def _spreadsheet() -> gspread.Spreadsheet:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"])


def get_active_tab() -> str | None:
    if not ACTIVE_TAB_FILE.exists():
        return None
    name = ACTIVE_TAB_FILE.read_text().strip()
    return name or None


def set_active_tab(name: str) -> str:
    """Validate the tab exists, then persist it. Raises WorksheetNotFound on typo."""
    _spreadsheet().worksheet(name)
    ACTIVE_TAB_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_TAB_FILE.write_text(name)
    return name


def _active_worksheet() -> gspread.Worksheet:
    name = get_active_tab()
    if name is None:
        raise RuntimeError(
            "No active tab set. Use the /active command to set one first."
        )
    return _spreadsheet().worksheet(name)


def _channel_from_tab_name(tab_name: str) -> str:
    """6月有易 -> 有易卡航 (matching the sheet's convention)."""
    if "月" not in tab_name:
        return ""
    channel = tab_name.split("月", 1)[1]
    return f"{channel}卡航"


def _scan_rows(tab: gspread.Worksheet) -> tuple[list[int], int | None]:
    """Return (data_row_indices, summary_row_index_or_None).

    A data row is one whose 商品名称 != 'summary' and 数量 (col D) is non-empty.
    """
    all_values = tab.get_all_values()
    data_rows: list[int] = []
    summary_row: int | None = None
    for i, row in enumerate(all_values[1:], start=2):
        item = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        qty = row[COL_QUANTITY - 1] if len(row) >= COL_QUANTITY else ""
        if item == SUMMARY_MARKER:
            summary_row = i
            continue
        if qty:
            data_rows.append(i)
    return data_rows, summary_row


def _find_first_empty_data_row(tab: gspread.Worksheet) -> int:
    """First row where D is empty and B is not 'summary'.

    Sequentially fills rows 2, 3, ... regardless of phantom structure.
    """
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        item = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        qty = row[COL_QUANTITY - 1] if len(row) >= COL_QUANTITY else ""
        if item == SUMMARY_MARKER:
            continue
        if not qty:
            return i
    return len(all_values) + 1


def record_parcel(
    date: str,
    item: str,
    platform: str,
    quantity: int,
    unit_price: float | None = None,
    total_price: float | None = None,
    tracking_no: str | None = None,
    weight_kg: float | None = None,
    notes: str | None = None,
) -> dict:
    """Append a new parcel. Must provide unit_price or total_price (or both).

    If only one of unit_price/total_price is given, the other is written as a
    formula so the trio (qty, unit_price, total) stays consistent.
    """
    if unit_price is None and total_price is None:
        raise ValueError("must provide unit_price, total_price, or both")

    tab = _active_worksheet()
    _, summary_row = _scan_rows(tab)
    if summary_row is not None:
        raise RuntimeError(
            f"Summary row exists at row {summary_row}; batch is settled. "
            "Delete the summary row first if you really want to add more parcels."
        )

    target_row = _find_first_empty_data_row(tab)

    row = [""] * NUM_COLS
    row[COL_DATE - 1] = date
    row[COL_ITEM - 1] = item
    row[COL_PLATFORM - 1] = platform
    row[COL_QUANTITY - 1] = quantity

    if unit_price is not None and total_price is not None:
        row[COL_UNIT_PRICE - 1] = unit_price
        row[COL_TOTAL_COST - 1] = total_price
    elif unit_price is not None:
        row[COL_UNIT_PRICE - 1] = unit_price
        row[COL_TOTAL_COST - 1] = f"=E{target_row}*D{target_row}"
    else:
        row[COL_UNIT_PRICE - 1] = f"=F{target_row}/D{target_row}"
        row[COL_TOTAL_COST - 1] = total_price

    if tracking_no is not None:
        row[COL_TRACKING - 1] = tracking_no
    row[COL_STATUS - 1] = "未发货"
    if weight_kg is not None:
        row[COL_DOMESTIC_WEIGHT - 1] = weight_kg
    row[COL_CHANNEL - 1] = _channel_from_tab_name(tab.title)
    if notes is not None:
        row[COL_NOTES - 1] = notes

    tab.update(
        range_name=f"A{target_row}:{LAST_COL_LETTER}{target_row}",
        values=[row],
        value_input_option="USER_ENTERED",
    )
    return {"row": target_row, "item": item, "tab": tab.title}


def find_parcel(query: str) -> list[dict]:
    """Find data rows whose 商品名称 or 国内快递单号 contains the query string.

    Skips the summary row.
    """
    tab = _active_worksheet()
    all_values = tab.get_all_values()
    if len(all_values) < 2:
        return []

    query_lower = query.lower()
    matches: list[dict] = []
    for row_idx, row in enumerate(all_values[1:], start=2):
        item = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        if item == SUMMARY_MARKER:
            continue
        tracking = row[COL_TRACKING - 1] if len(row) >= COL_TRACKING else ""
        status = row[COL_STATUS - 1] if len(row) >= COL_STATUS else ""
        qty = row[COL_QUANTITY - 1] if len(row) >= COL_QUANTITY else ""
        if not qty:
            continue
        if query_lower in item.lower() or (query and query in tracking):
            matches.append({
                "row": row_idx,
                "item": item,
                "tracking_no": tracking,
                "status": status,
            })
    return matches


def update_parcel(
    row: int,
    status: str | None = None,
    tracking_no: str | None = None,
    weight_kg: float | None = None,
    notes: str | None = None,
) -> dict:
    """Update one or more fields on a data row."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(VALID_STATUSES)}"
        )

    tab = _active_worksheet()
    cells = []
    if status is not None:
        cells.append(gspread.cell.Cell(row, COL_STATUS, status))
    if tracking_no is not None:
        cells.append(gspread.cell.Cell(row, COL_TRACKING, tracking_no))
    if weight_kg is not None:
        cells.append(gspread.cell.Cell(row, COL_DOMESTIC_WEIGHT, weight_kg))
    if notes is not None:
        cells.append(gspread.cell.Cell(row, COL_NOTES, notes))

    if cells:
        tab.update_cells(cells, value_input_option="USER_ENTERED")

    return {"row": row, "values": tab.row_values(row)}


def update_parcels_by_tracking(
    tracking_no: str,
    status: str | None = None,
    total_weight_kg: float | None = None,
    notes: str | None = None,
) -> dict:
    """Update all rows whose 国内快递单号 contains the given tracking_no (substring match).

    Used when one physical parcel carries multiple SKUs (multiple rows share a
    tracking number). Status and notes are applied uniformly to all matched
    rows. Weight is split EQUALLY across matched rows (total_weight_kg / N).

    For non-equal weight splits, the caller (LLM) should compute per-row
    weights from the user's stated ratio/literal values and call update_parcel
    once per row instead.
    """
    if status is None and total_weight_kg is None and notes is None:
        raise ValueError("nothing to update; provide status, total_weight_kg, or notes")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(VALID_STATUSES)}"
        )
    if not tracking_no:
        raise ValueError("tracking_no must be non-empty")

    tab = _active_worksheet()
    all_values = tab.get_all_values()
    matched_rows: list[int] = []
    for i, row in enumerate(all_values[1:], start=2):
        item = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        if item == SUMMARY_MARKER:
            continue
        tr = str(row[COL_TRACKING - 1]) if len(row) >= COL_TRACKING else ""
        if tracking_no in tr:
            matched_rows.append(i)

    if not matched_rows:
        return {"ok": False, "reason": "no rows matched the tracking number"}

    per_row_weight = (
        total_weight_kg / len(matched_rows) if total_weight_kg is not None else None
    )

    cells = []
    for r in matched_rows:
        if status is not None:
            cells.append(gspread.cell.Cell(r, COL_STATUS, status))
        if per_row_weight is not None:
            cells.append(gspread.cell.Cell(r, COL_DOMESTIC_WEIGHT, per_row_weight))
        if notes is not None:
            cells.append(gspread.cell.Cell(r, COL_NOTES, notes))

    if cells:
        tab.update_cells(cells, value_input_option="USER_ENTERED")

    return {
        "rows_updated": matched_rows,
        "per_row_weight_kg": per_row_weight,
    }


def settle_shipping(
    total_billed_weight_kg: float,
    total_shipping_rmb: float,
) -> dict:
    """Stage 2: add summary row + apportioning formulas to all data rows.

    The carrier has reported the consolidated billing weight and shipping cost.
    This creates a summary row holding those literals, then writes formulas in
    columns K (channel rate), L (apportioned weight), M (per-item shipping),
    N (per-item RMB total) for every data row, referencing the summary row.
    """
    tab = _active_worksheet()
    data_rows, summary_row = _scan_rows(tab)
    if summary_row is not None:
        raise RuntimeError(f"Summary row already exists at row {summary_row}")
    if not data_rows:
        raise RuntimeError("No data rows to settle")

    first = min(data_rows)
    last = max(data_rows)
    s = last + 1  # summary row index

    summary = [""] * NUM_COLS
    summary[COL_ITEM - 1] = SUMMARY_MARKER
    summary[COL_QUANTITY - 1] = f"=COUNT(D{first}:D{last})"
    summary[COL_TOTAL_COST - 1] = f"=SUM(F{first}:F{last})"
    summary[COL_DOMESTIC_WEIGHT - 1] = f"=SUM(I{first}:I{last})"
    summary[COL_CHANNEL_RATE - 1] = f"=M{s}/L{s}"
    summary[COL_TOTAL_WEIGHT - 1] = total_billed_weight_kg
    summary[COL_PAID_SHIPPING - 1] = total_shipping_rmb
    summary[COL_GRAND_TOTAL_RMB - 1] = f"=F{s}+M{s}"

    tab.update(
        range_name=f"A{s}:{LAST_COL_LETTER}{s}",
        values=[summary],
        value_input_option="USER_ENTERED",
    )

    cells = []
    for r in data_rows:
        cells.append(gspread.cell.Cell(r, COL_CHANNEL_RATE, f"=$M${s}/$L${s}"))
        cells.append(gspread.cell.Cell(r, COL_TOTAL_WEIGHT, f"=I{r}*$L${s}/$I${s}"))
        cells.append(gspread.cell.Cell(r, COL_PAID_SHIPPING, f"=K{r}*L{r}"))
        cells.append(gspread.cell.Cell(r, COL_GRAND_TOTAL_RMB, f"=F{r}+M{r}"))
    tab.update_cells(cells, value_input_option="USER_ENTERED")

    return {"summary_row": s, "data_rows": data_rows}


def apply_exchange_rate(rate: float) -> dict:
    """Stage 3: write exchange rate + EUR-conversion formulas to all data rows."""
    tab = _active_worksheet()
    data_rows, _ = _scan_rows(tab)
    if not data_rows:
        raise RuntimeError("No data rows to apply exchange rate")

    cells = []
    for r in data_rows:
        cells.append(gspread.cell.Cell(r, COL_EXCHANGE_RATE, rate))
        cells.append(gspread.cell.Cell(r, COL_GRAND_TOTAL_EUR, f"=N{r}/O{r}"))
        cells.append(gspread.cell.Cell(r, COL_EURO_UNIT_PRICE, f"=P{r}/D{r}"))
    tab.update_cells(cells, value_input_option="USER_ENTERED")

    return {"rows_updated": len(data_rows)}
