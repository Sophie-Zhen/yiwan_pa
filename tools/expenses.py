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

import gspread
from dotenv import load_dotenv

from storage import sheets
from tools import inventory

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


def _ledger_tab() -> gspread.Worksheet:
    return sheets.open_sheet("EXPENSES_SHEET_ID").worksheet(LEDGER_TAB)


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
    start_row = sheets.first_empty_row(tab, COL_DATE)

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

    # Auto-restock: bump any watchlist item this trip replenished. Done at the
    # data layer (not left to the LLM) so "buying X also restocks X" can't be
    # forgotten — same reliability argument as record_investment's upsert.
    inventory_updates = inventory.apply_purchase(date, items)

    return {
        "rows": [start_row, end_row],
        "count": len(rows),
        "date": date,
        "store": store,
        "inventory_updates": inventory_updates,
    }


# --- 单笔 (transactions ledger) --------------------------------------------
# The COMPLETE one-row-per-transaction record. Card/bank rows are imported from
# statements monthly by scripts/consolidate.py (来源=对账单); this tool adds only
# the rows a statement can NEVER produce — manual CASH spending/income (来源=手记)
# — so they aren't lost. Columns mirror consolidate.py's SHEET_TAB exactly, plus
# 备注 (col H, hand-added in the live sheet), so a bot row and a script row match.
TXN_TAB = "单笔"
TXN_LAST_COL = "H"
_FLOW_SIGN = {"支出": -1, "收入": 1}


def _txn_tab() -> gspread.Worksheet:
    return sheets.open_sheet("EXPENSES_SHEET_ID").worksheet(TXN_TAB)


def record_transaction(
    date: str,
    description: str,
    amount: float,
    direction: str,
    account: str = "现金",
    category: str | None = None,
    notes: str | None = None,
) -> dict:
    """Append one manually-tracked transaction to the 单笔 ledger.

    For money the bank statement will NOT import: cash spending/income, a cash
    settle. Card/bank purchases must NOT go here — the monthly statement import
    brings those into 单笔, so logging them by hand would double-count.

    `direction` is '支出' or '收入'. `amount` is the positive magnitude; it is
    stored signed (支出 negative, 收入 positive) to match the statement rows.
    `account` defaults to 现金 (the usual manual case). `category` is the 支出
    bucket from the 单笔 taxonomy; left blank for 收入 (matches consolidate.py).
    来源 is always 手记, marking the row hand-entered vs a 对账单 import.
    """
    if direction not in _FLOW_SIGN:
        raise ValueError(f"direction must be one of {sorted(_FLOW_SIGN)}, got {direction!r}")
    signed = round(_FLOW_SIGN[direction] * abs(float(amount)), 2)
    cat = (category or "") if direction == "支出" else ""

    tab = _txn_tab()
    row_index = sheets.first_empty_row(tab, 1)
    row = [date, account, description, signed, direction, cat, "手记", notes or ""]
    tab.update(
        range_name=f"A{row_index}:{TXN_LAST_COL}{row_index}",
        values=[row],
        value_input_option="USER_ENTERED",
    )
    return {
        "row": row_index,
        "date": date,
        "account": account,
        "description": description,
        "amount": signed,
        "direction": direction,
        "category": cat,
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
        d = sheets.cell(row, COL_DATE)
        if not d:
            continue
        name = sheets.cell(row, COL_ITEM)
        st = sheets.cell(row, COL_STORE)
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
            "quantity": sheets.cell(row, COL_QUANTITY),
            "unit": sheets.cell(row, COL_UNIT),
            "unit_price": sheets.cell(row, COL_UNIT_PRICE),
            "subtotal": sheets.cell(row, COL_SUBTOTAL),
            "category": sheets.cell(row, COL_CATEGORY),
            "notes": sheets.cell(row, COL_NOTES),
        })
    return matches


# --- Corrections -----------------------------------------------------------
# An append-only ledger still needs a way to fix mistakes. These edit or void
# rows located via find_purchase (which returns sheet row numbers). The prompt
# drives a propose-confirm flow: show what will change, then call one of these.

# Fields amend_purchase may set. Quantity is deliberately excluded — it changes
# inventory, so it has its own tool (set_purchase_quantity).
_AMENDABLE_COLS = {
    "store": COL_STORE,
    "date": COL_DATE,
    "category": COL_CATEGORY,
    "unit": COL_UNIT,
    "notes": COL_NOTES,
    "item": COL_ITEM,
    "unit_price": COL_UNIT_PRICE,
    "subtotal": COL_SUBTOTAL,
}


def amend_purchase(rows: list[int], field: str, value) -> dict:
    """Correct a non-quantity field on one or more 明细 rows (row numbers from
    find_purchase). No inventory effect (quantity is unchanged).

    Editing unit_price also rewrites 小计 to the formula =D*F (and editing
    subtotal rewrites 单价 to =G/D), keeping the qty/price/total trio consistent
    — the same convention record_purchase uses.
    """
    if field not in _AMENDABLE_COLS:
        raise ValueError(
            f"field must be one of {sorted(_AMENDABLE_COLS)} "
            "(use set_purchase_quantity to change quantity)"
        )
    if not rows:
        raise ValueError("rows must be a non-empty list")

    tab = _ledger_tab()
    col = _AMENDABLE_COLS[field]
    cells: list[gspread.cell.Cell] = []
    for r in rows:
        cells.append(gspread.cell.Cell(r, col, value))
        if field == "unit_price":
            cells.append(gspread.cell.Cell(r, COL_SUBTOTAL, f"=D{r}*F{r}"))
        elif field == "subtotal":
            cells.append(gspread.cell.Cell(r, COL_UNIT_PRICE, f"=G{r}/D{r}"))
    tab.update_cells(cells, value_input_option="USER_ENTERED")
    return {"rows": list(rows), "field": field, "value": value, "count": len(rows)}


def set_purchase_quantity(row: int, quantity: float) -> dict:
    """Correct the 数量 of a single 明细 row (row number from find_purchase). The
    tracked inventory items this line replenished are adjusted by (new - old).

    Only 数量 is written; the 单价/小计 trio recomputes via whichever was a formula:
      - unit_price known (小计 is =D*F): the LINE TOTAL follows the new quantity —
        the normal count case (qty was wrong, total should change).
      - line-total known (单价 is =G/D, e.g. loose produce): the TOTAL stays put
        and the per-unit price re-derives. If correcting a weight should ALSO
        change what was paid, amend the subtotal too via amend_purchase.
    If both were entered as literals, amend the price or subtotal as well.
    """
    tab = _ledger_tab()
    all_values = tab.get_all_values()
    idx = row - 1
    if row < 2 or idx >= len(all_values):
        raise ValueError(f"row {row} is out of range")
    existing = all_values[idx]
    item = sheets.cell(existing, COL_ITEM)
    if not item:
        raise ValueError(f"row {row} is not a purchase line (empty 商品)")

    old_qty = sheets.to_float(sheets.cell(existing, COL_QUANTITY))
    new_qty = float(quantity)
    tab.update_cell(row, COL_QUANTITY, new_qty)

    delta = new_qty - old_qty
    inv = inventory.apply_quantity_delta([{"item": item, "quantity": delta}]) if delta else []
    return {
        "row": row,
        "item": item,
        "old_quantity": round(old_qty, 3),
        "new_quantity": round(new_qty, 3),
        "inventory_updates": inv,
    }


def _most_recent_remaining(item_name: str) -> tuple[str | None, float | None]:
    """The (date, unit_price) of the most recent remaining ledger line whose 商品
    contains item_name (substring) — used to resync an inventory item's
    denormalized last-purchase facts after a void. (None, None) if none remain.
    """
    rows = find_purchase(item=item_name)
    if not rows:
        return None, None
    latest = max(rows, key=lambda r: r["date"])
    raw = latest.get("unit_price")
    try:
        price = float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    return latest["date"], price


def void_purchase(rows: list[int]) -> dict:
    """Delete one or more 明细 rows (row numbers from find_purchase) and reverse
    their effect on inventory: subtract each line's purchased quantity from the
    tracked items it replenished, then resync every affected item's 上次购买日/
    上次单价 to the most recent REMAINING ledger line (cleared if nothing remains),
    so the denormalized inventory facts match the ledger after the void.

    Only real purchase lines (non-empty 商品) are deleted — a stale or blank row
    number is returned in `skipped_rows`, never silently removed.

    NOT atomic: Google Sheets has no transactions, so a transient API failure
    between the row delete and the inventory reversal can briefly desync the two.
    Acceptable at single-user volume; if it happens, re-locate with find_purchase
    (row numbers will have shifted) and fix inventory by hand.
    """
    if not rows:
        raise ValueError("rows must be a non-empty list")

    tab = _ledger_tab()
    all_values = tab.get_all_values()

    valid_rows: list[int] = []
    skipped: list[int] = []
    item_names: list[str] = []        # every item touched (for resync)
    reverse_lines: list[dict] = []    # only positives were applied to inventory
    for r in rows:
        idx = r - 1
        if r < 2 or idx >= len(all_values):
            raise ValueError(f"row {r} is out of range")
        item = sheets.cell(all_values[idx], COL_ITEM)
        if not item:
            skipped.append(r)         # blank/stale — do NOT delete it
            continue
        valid_rows.append(r)
        item_names.append(item)
        qty = sheets.to_float(sheets.cell(all_values[idx], COL_QUANTITY))
        if qty > 0:                   # apply_purchase only applied positives
            reverse_lines.append({"item": item, "quantity": -qty})

    # Delete only the validated rows, high→low so indices don't shift mid-loop.
    for r in sorted(valid_rows, reverse=True):
        tab.delete_rows(r)

    # Reverse the quantity, then resync date/price for EVERY affected watchlist
    # item — including ones whose net delta is zero — from the now-current ledger.
    inv = inventory.apply_quantity_delta(reverse_lines) if reverse_lines else []
    resynced: list[dict] = []
    for name in inventory.matching_items(item_names):
        d, p = _most_recent_remaining(name)
        res = inventory.resync_last_purchase(name, d, p)
        if res:
            resynced.append(res)

    return {
        "deleted_rows": sorted(valid_rows),
        "skipped_rows": sorted(skipped),
        "count": len(valid_rows),
        "inventory_reversed": inv,
        "inventory_resynced": resynced,
    }


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
        bucket["quantity"] += sheets.to_float(str(r["quantity"]))
        bucket["spend"] += sheets.to_float(str(r["subtotal"]))

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


def spend_summary(since: str | None = None, until: str | None = None) -> dict:
    """Total spend over a date range, broken down by 类别 (category).

    This is the "where did my money go" view — the categorised total makes
    big fixed/annual costs (保险 / 能源 / 固定支出) visible alongside everyday
    spending, so a monthly tally doesn't have an unexplained gap. Computed in
    code (summed from the ledger rows), not estimated by the model.

    Rows with no category bucket under '未分类'. since/until are inclusive
    YYYY-MM-DD bounds, same as find_purchase.
    """
    rows = find_purchase(since=since, until=until)
    by_category: dict[str, float] = {}
    total = 0.0
    for r in rows:
        amount = sheets.to_float(str(r["subtotal"]))
        total += amount
        category = r["category"] or "未分类"
        by_category[category] = by_category.get(category, 0.0) + amount

    return {
        "since": since,
        "until": until,
        "total": round(total, 2),
        "rows_count": len(rows),
        "by_category": {
            k: round(v, 2)
            for k, v in sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
        },
    }
