"""Tools for tracking 家庭花销 (household spending) in Google Sheets.

Phase 1 scope: the 明细 (line-item) tab only — an append-only ledger where one
shopping trip becomes N rows, one per item. This granularity is the point: it
is what lets us answer "how has the price of coffee beans moved?" and "what do
we buy the most?" later. The 库存 (inventory) tab is added in a later phase.

「明细」(line-item ledger) — append-only. One row per item per trip.
    A 日期    | input (YYYY-MM-DD)
    B 店铺    | input (store name; receipts here are English, so values are too)
    C 商品    | input (item name as printed on the receipt)
    D 数量    | input
    E 单位    | input, optional (each / kg / pack ... free text)
    F 单价    | input or formula (=G/D when only the line total is known)
    G 小计    | input or formula (=D*F when only the unit price is known)
    H 类别    | input, optional (coarse bucket: 食品 / 日用 / 装修 ...)
    I 备注    | input, optional

Why batch (record_purchase takes a list): a receipt is naturally many lines
sharing one date and store. Writing them in one call keeps the LLM from firing
N separate tool calls per receipt, and keeps the rows visually grouped.
"""

import os

import gspread
from dotenv import load_dotenv

load_dotenv()

LEDGER_TAB = "明细"
COL_DATE = 1
COL_STORE = 2
COL_ITEM = 3
COL_QUANTITY = 4
COL_UNIT = 5
COL_UNIT_PRICE = 6
COL_SUBTOTAL = 7
COL_CATEGORY = 8
COL_NOTES = 9
NUM_COLS = 9
LAST_COL = "I"


def _spreadsheet() -> gspread.Spreadsheet:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    return client.open_by_key(os.environ["EXPENSES_SHEET_ID"])


def _ledger_tab() -> gspread.Worksheet:
    return _spreadsheet().worksheet(LEDGER_TAB)


def _find_first_empty_row(tab: gspread.Worksheet) -> int:
    """First row (>=2) whose 日期 column is empty.

    Sequentially fills rows 2, 3, ... regardless of phantom structure. Mirrors
    the parcels.py / investments.py workaround for banded ranges making
    append_row jump rows.
    """
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        cell = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
        if not cell:
            return i
    return len(all_values) + 1


def _to_float(raw: str) -> float:
    """Parse a numeric cell to float; empty / non-numeric → 0.0."""
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def record_purchase(
    date: str,
    store: str,
    items: list[dict],
    notes: str | None = None,
) -> dict:
    """Append a shopping trip as N line-item rows sharing one date and store.

    Each item dict:
        item        (str, required)  — name as printed on the receipt
        quantity    (number, required)
        unit_price  (number, optional)
        subtotal    (number, optional)  — the line total
        unit        (str, optional)
        category    (str, optional)
        notes       (str, optional)

    Must provide unit_price or subtotal (or both) per item. When only one is
    given, the other is written as a formula so the trio (qty, unit_price,
    subtotal) stays consistent — same approach as parcels.record_parcel.

    The top-level `notes` (e.g. "周末囤货") is written to any item row that did
    not carry its own notes.
    """
    if not items:
        raise ValueError("items must be a non-empty list")

    tab = _ledger_tab()
    start_row = _find_first_empty_row(tab)

    rows: list[list] = []
    for offset, it in enumerate(items):
        r = start_row + offset
        name = it.get("item")
        if not name:
            raise ValueError(f"item #{offset} missing 'item' name")
        qty = it.get("quantity")
        if qty is None:
            raise ValueError(f"item {name!r} missing 'quantity'")
        unit_price = it.get("unit_price")
        subtotal = it.get("subtotal")
        if unit_price is None and subtotal is None:
            raise ValueError(f"item {name!r} needs unit_price or subtotal")

        row = [""] * NUM_COLS
        row[COL_DATE - 1] = date
        row[COL_STORE - 1] = store
        row[COL_ITEM - 1] = name
        row[COL_QUANTITY - 1] = qty
        row[COL_UNIT - 1] = it.get("unit", "")

        if unit_price is not None and subtotal is not None:
            row[COL_UNIT_PRICE - 1] = unit_price
            row[COL_SUBTOTAL - 1] = subtotal
        elif unit_price is not None:
            row[COL_UNIT_PRICE - 1] = unit_price
            row[COL_SUBTOTAL - 1] = f"=D{r}*F{r}"
        else:
            row[COL_UNIT_PRICE - 1] = f"=G{r}/D{r}"
            row[COL_SUBTOTAL - 1] = subtotal

        row[COL_CATEGORY - 1] = it.get("category", "")
        row[COL_NOTES - 1] = it.get("notes") or (notes or "")
        rows.append(row)

    end_row = start_row + len(rows) - 1
    tab.update(
        range_name=f"A{start_row}:{LAST_COL}{end_row}",
        values=rows,
        value_input_option="USER_ENTERED",
    )
    return {
        "rows": [start_row, end_row],
        "count": len(rows),
        "date": date,
        "store": store,
    }


def find_purchase(
    item: str | None = None,
    store: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Find line-item rows. All filters AND together.

    - item:  substring match on 商品 (case-insensitive)
    - store: substring match on 店铺 (case-insensitive)
    - since/until: inclusive YYYY-MM-DD bounds on 日期 (string compare is safe
      for zero-padded ISO dates)
    """
    tab = _ledger_tab()
    all_values = tab.get_all_values()
    item_lower = item.lower() if item else None
    store_lower = store.lower() if store else None

    matches: list[dict] = []
    for i, row in enumerate(all_values[1:], start=2):
        d = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
        if not d:
            continue
        name = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        st = row[COL_STORE - 1] if len(row) >= COL_STORE else ""
        if item_lower is not None and item_lower not in name.lower():
            continue
        if store_lower is not None and store_lower not in st.lower():
            continue
        if since is not None and d < since:
            continue
        if until is not None and d > until:
            continue
        matches.append({
            "row": i,
            "date": d,
            "store": st,
            "item": name,
            "quantity": row[COL_QUANTITY - 1] if len(row) >= COL_QUANTITY else "",
            "unit": row[COL_UNIT - 1] if len(row) >= COL_UNIT else "",
            "unit_price": row[COL_UNIT_PRICE - 1] if len(row) >= COL_UNIT_PRICE else "",
            "subtotal": row[COL_SUBTOTAL - 1] if len(row) >= COL_SUBTOTAL else "",
            "category": row[COL_CATEGORY - 1] if len(row) >= COL_CATEGORY else "",
            "notes": row[COL_NOTES - 1] if len(row) >= COL_NOTES else "",
        })
    return matches


def price_history(item: str) -> list[dict]:
    """Every purchase of an item (substring match), sorted by date ascending.

    Returns the fields needed to eyeball a price trend: date, store, unit,
    quantity, unit_price. This is the raw series; the caller (LLM) narrates
    whether the price rose or fell.
    """
    rows = find_purchase(item=item)
    rows.sort(key=lambda r: r["date"])
    return [
        {
            "date": r["date"],
            "store": r["store"],
            "item": r["item"],
            "unit": r["unit"],
            "quantity": r["quantity"],
            "unit_price": r["unit_price"],
        }
        for r in rows
    ]


def top_items(
    since: str | None = None,
    until: str | None = None,
    by: str = "spend",
    limit: int = 15,
) -> list[dict]:
    """Aggregate the ledger by item to answer "what do we buy the most?".

    Groups rows by 商品 (lowercased for the key, original casing kept for
    display). For each item reports times (number of purchase rows), total
    quantity, and total spend. Sorted by `by` ∈ {'spend', 'count', 'quantity'}
    descending; top `limit` returned.

    Date range via since/until (inclusive YYYY-MM-DD), same semantics as
    find_purchase.
    """
    valid_by = {"spend", "count", "quantity"}
    if by not in valid_by:
        raise ValueError(f"by must be one of {sorted(valid_by)}, got {by!r}")

    rows = find_purchase(since=since, until=until)
    agg: dict[str, dict] = {}
    for r in rows:
        key = r["item"].lower()
        bucket = agg.setdefault(
            key, {"item": r["item"], "times": 0, "quantity": 0.0, "spend": 0.0}
        )
        bucket["times"] += 1
        bucket["quantity"] += _to_float(str(r["quantity"]))
        bucket["spend"] += _to_float(str(r["subtotal"]))

    sort_key = {"spend": "spend", "count": "times", "quantity": "quantity"}[by]
    ranked = sorted(agg.values(), key=lambda b: b[sort_key], reverse=True)
    return [
        {
            "item": b["item"],
            "times": b["times"],
            "quantity": round(b["quantity"], 3),
            "spend": round(b["spend"], 2),
        }
        for b in ranked[:limit]
    ]
