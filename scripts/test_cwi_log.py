"""Unit test for tools/cwi_log.py — runs against a temp file, no real data.

Run:
    conda run -n assistant python scripts/test_cwi_log.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import cwi_log


def main() -> None:
    # Redirect the store to a fresh temp file so we never touch real data.
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    tmp.unlink(missing_ok=True)
    cwi_log.CWI_LOG_PATH = tmp

    try:
        print("\n=== log two instructed sessions (today's taster + induction) ===")
        a = cwi_log.log_instructed_session(
            date="2026-06-12", venue="Awesome Walls Dublin",
            detail="top-rope taster", role="led",
            large_public_facility=True, notes="two groups of 2",
        )
        b = cwi_log.log_instructed_session(
            date="2026-06-12", venue="Awesome Walls Dublin",
            detail="bouldering induction", role="led",
            large_public_facility=True,
        )
        print(a, b)
        assert a["id"] == 1 and b["id"] == 2
        assert a["status"] == "pending"

        print("\n=== log a personal climb at a different wall ===")
        c = cwi_log.log_personal_climb(
            date="2026-06-11", venue="Gravity Dublin", climbs_led=5,
            detail="lead practice",
        )
        print(c)
        assert c["id"] == 3 and c["kind"] == "personal"

        print("\n=== progress: counts against official targets ===")
        p = cwi_log.progress()
        print(p)
        ins = p["instructed_sessions"]
        assert ins["done"] == 2 and ins["target"] == 15 and ins["remaining"] == 13
        assert ins["walls"] == 1 and ins["walls_target"] == 2  # both at same venue
        assert ins["has_large_public_facility"] is True
        assert ins["reflective"] == 2 and ins["reflective_target"] == 5
        per = p["personal_visits"]
        assert per["done"] == 1 and per["target"] == 30 and per["walls"] == 1
        assert p["lead_climbs"]["done"] == 5 and p["lead_climbs"]["target"] == 40

        print("\n=== distinct walls counts a second venue ===")
        cwi_log.log_instructed_session(
            date="2026-06-10", venue="The Wall Sandyford",
            detail="group session", role="assisted", large_public_facility=False,
        )
        assert cwi_log.progress()["instructed_sessions"]["walls"] == 2

        print("\n=== pending / mark_recorded ===")
        pending = cwi_log.pending_entries()
        assert len(pending) == 4, pending
        one = cwi_log.mark_recorded(ids=[1])
        assert one == {"recorded": [1], "count": 1}, one
        assert len(cwi_log.pending_entries()) == 3
        rest = cwi_log.mark_recorded()  # all remaining
        assert rest["count"] == 3, rest
        assert cwi_log.pending_entries() == []

        print("\n=== recorded entries STILL count toward progress ===")
        p2 = cwi_log.progress()
        assert p2["instructed_sessions"]["done"] == 3  # unchanged by recording
        assert p2["lead_climbs"]["done"] == 5

        print("\n=== validation: bad date raises ===")
        try:
            cwi_log.log_instructed_session(date="June 12", venue="x", detail="y")
            assert False, "bad date should raise"
        except ValueError as e:
            print(f"ok: {e}")

        print("\n=== round-trips through the markdown file ===")
        reparsed = cwi_log.list_entries()
        assert len(reparsed) == 4
        assert reparsed[0]["venue"] == "Awesome Walls Dublin"
        assert reparsed[0]["status"] == "recorded"

        print("\n[all assertions passed]")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
