"""Exercise tools/parcels.py against a scratch tab.

Pass the name of an EMPTY tab as the argument:

    python scripts/test_parcels.py _test_scratch

Refuses to run unless that tab is header-only. Cleans up every row it touches
on the way out, success or failure. Do not point this at a tab with real data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.parcels import (
    _active_worksheet,
    apply_exchange_rate,
    find_parcel,
    record_parcel,
    set_active_tab,
    settle_shipping,
    update_parcel,
    update_parcels_by_tracking,
)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_parcels.py <empty-scratch-tab-name>")
        sys.exit(1)
    scratch_tab = sys.argv[1]
    print(f"setting active tab to {scratch_tab}")
    set_active_tab(scratch_tab)

    tab = _active_worksheet()
    rows_before = len(tab.get_all_values())
    assert rows_before == 1, (
        f"{scratch_tab} has {rows_before} rows but test expects only header (1). "
        "Point this script at a scratch tab, not a tab with real data."
    )

    rows_touched: set[int] = set()

    try:
        print("\n--- record_parcel: qty + unit_price (total via formula) ---")
        r1 = record_parcel(
            "2026-06-05", "测试火锅底料", "pdd", 4, unit_price=18.8
        )
        print(r1)
        rows_touched.add(r1["row"])
        assert r1["row"] == 2, f"first parcel should go to row 2, got {r1['row']}"

        print("\n--- record_parcel: qty + total_price (unit via formula) ---")
        r2 = record_parcel(
            "2026-06-05", "测试劳保手套", "tb", 60, total_price=224.09
        )
        print(r2)
        rows_touched.add(r2["row"])
        assert r2["row"] == 3

        print("\n--- record_parcel: qty + both (no formula) ---")
        r3 = record_parcel(
            "2026-06-05", "测试椅子保护套", "1688", 1, unit_price=24.33, total_price=24.33,
            weight_kg=0.5,
        )
        print(r3)
        rows_touched.add(r3["row"])
        assert r3["row"] == 4

        print("\n--- verify formulas/literals in cols E and F ---")
        ef = tab.get(
            f"E2:F4", value_render_option="FORMULA"
        )
        print(f"E2:F4 = {ef}")
        assert ef[0] == [18.8, "=E2*D2"], f"r1 EF wrong: {ef[0]}"
        assert ef[1] == ["=F3/D3", 224.09], f"r2 EF wrong: {ef[1]}"
        assert ef[2] == [24.33, 24.33], f"r3 EF wrong: {ef[2]}"

        print("\n--- find_parcel by item '火锅底料' ---")
        m = find_parcel("火锅底料")
        print(m)
        assert len(m) == 1 and m[0]["row"] == r1["row"]

        print("\n--- update_parcel: status + tracking on r1 ---")
        upd = update_parcel(r1["row"], status="在途", tracking_no="9812921819303")
        print(upd)

        print("\n--- find_parcel by tracking '9303' (suffix match) ---")
        m = find_parcel("9303")
        print(m)
        assert any(x["row"] == r1["row"] for x in m)

        print("\n--- update_parcel: invalid status raises ---")
        try:
            update_parcel(r1["row"], status="garbage")
            assert False, "expected ValueError"
        except ValueError as e:
            print(f"correctly rejected: {e}")

        print("\n--- update_parcel: weight + 已入库拍照 ---")
        upd = update_parcel(r2["row"], status="已入库拍照", weight_kg=3.82)
        print(upd)

        print("\n--- multi-SKU: assign same tracking to r3 and r4 (record r4 first) ---")
        r4 = record_parcel(
            "2026-06-05", "测试同包裹SKU-B", "pdd", 1, unit_price=10.0
        )
        rows_touched.add(r4["row"])
        shared_tracking = "SHARED9999"
        update_parcel(r3["row"], tracking_no=shared_tracking)
        update_parcel(r4["row"], tracking_no=shared_tracking)

        print(f"\n--- update_parcels_by_tracking('{shared_tracking}', status=已入库拍照, total_weight_kg=1.5) ---")
        result = update_parcels_by_tracking(
            tracking_no=shared_tracking,
            status="已入库拍照",
            total_weight_kg=1.5,
        )
        print(result)
        assert result["rows_updated"] == [r3["row"], r4["row"]]
        assert result["per_row_weight_kg"] == 0.75

        print("\n--- verify both rows got status + 0.75kg each ---")
        for row_num in [r3["row"], r4["row"]]:
            row_vals = tab.row_values(row_num)
            assert row_vals[COL_STATUS_IDX] == "已入库拍照", row_vals
            assert float(row_vals[COL_DOMESTIC_WEIGHT_IDX]) == 0.75, row_vals
        print("both rows ok")

        print("\n--- update_parcels_by_tracking with unmatched tracking returns ok=False ---")
        nores = update_parcels_by_tracking(
            tracking_no="NONEXISTENT", status="在途"
        )
        assert nores == {"ok": False, "reason": "no rows matched the tracking number"}
        print(nores)

        print("\n--- settle_shipping(20kg, 700) ---")
        s = settle_shipping(
            total_billed_weight_kg=20, total_shipping_rmb=700
        )
        print(s)
        rows_touched.add(s["summary_row"])
        assert s["summary_row"] == 6
        assert s["data_rows"] == [2, 3, 4, 5]

        print("\n--- verify summary row + formulas in data rows ---")
        all_formulas = tab.get(
            f"A1:R6", value_render_option="FORMULA"
        )
        for i, row in enumerate(all_formulas, 1):
            print(f"row {i}: {row}")
        # check summary marker on row 6
        assert all_formulas[5][COL_ITEM_IDX] == "summary"
        # check K/L/M/N in data row 2 are formulas referencing summary row 6
        assert all_formulas[1][10].startswith("=$M$6/$L$6"), f"K2: {all_formulas[1][10]}"
        assert all_formulas[1][11].startswith("=I2*$L$6/$I$6"), f"L2: {all_formulas[1][11]}"

        print("\n--- apply_exchange_rate(7.8) ---")
        ar = apply_exchange_rate(7.8)
        print(ar)
        assert ar["data_rows_updated"] == 4
        assert ar["summary_row_updated"] is True

        print("\n--- verify O/P/Q in data rows ---")
        opq = tab.get(
            f"O2:Q5", value_render_option="FORMULA"
        )
        print(f"O2:Q5 = {opq}")
        for i, row in enumerate(opq):
            row_num = i + 2
            assert row[0] == 7.8, f"O{row_num}: {row[0]}"
            assert row[1] == f"=N{row_num}/O{row_num}"
            assert row[2] == f"=P{row_num}/D{row_num}"

        print("\n--- verify O + P on summary row ---")
        op_summary = tab.get(f"O6:P6", value_render_option="FORMULA")
        print(f"O6:P6 = {op_summary}")
        assert op_summary == [[7.8, "=N6/O6"]], f"summary O/P wrong: {op_summary}"

        print("\nALL ASSERTIONS PASSED")
    finally:
        print(f"\ncleaning up rows: {sorted(rows_touched)}")
        if rows_touched:
            ranges = [f"A{r}:R{r}" for r in sorted(rows_touched)]
            tab.batch_clear(ranges)
        rows_after = len(tab.get_all_values())
        print(f"rows after cleanup: {rows_after}")
        assert rows_after == 1, f"cleanup failed: {rows_after} rows remain"


COL_ITEM_IDX = 1  # 0-based index for col B (商品名称) in returned lists
COL_STATUS_IDX = 7  # 0-based index for col H (快递状态)
COL_DOMESTIC_WEIGHT_IDX = 8  # 0-based index for col I (国内包裹重量)


if __name__ == "__main__":
    main()
