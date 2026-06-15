"""Guard test for router.route_domains.

Pins (1) the shipped default is dark (ROUTING_ENABLED False with no env), (2) the
routing logic — input-type rules, trigger unions, ASCII false-positive guard,
and ALL-domains fallback. Run: conda run -n assistant python scripts/test_router.py
"""
import importlib
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import router
from prompts import ALL_DOMAINS


def check(desc, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'} {desc}: {sorted(got)}")
    if not ok:
        print(f"       expected {sorted(want)}")
    return ok


def main():
    passed = True

    # (1) Shipped default must be dark when ROUTING_ENABLED env is unset. Reload
    # the module with the var cleared so this reads the SOURCE default, not a
    # value the test set — catches a default accidentally flipped in source.
    saved_env = os.environ.pop("ROUTING_ENABLED", None)
    importlib.reload(router)
    ok = router.ROUTING_ENABLED is False
    print(f"  {'ok ' if ok else 'FAIL'} shipped default is dark (False): got {router.ROUTING_ENABLED}")
    passed &= ok
    if saved_env is not None:
        os.environ["ROUTING_ENABLED"] = saved_env
    importlib.reload(router)

    original = router.ROUTING_ENABLED
    try:
        # Dark mode returns ALL regardless of content.
        router.ROUTING_ENABLED = False
        passed &= check("disabled => ALL", router.route_domains("买了5个茶杯"), set(ALL_DOMAINS))

        # (2) Enable real routing for the logic checks.
        router.ROUTING_ENABLED = True
        web = {"web_search"}  # added to any non-fallback hit
        cases = [
            ("买了5个茶杯", {}, {"parcels"} | web),
            ("这个月定投基金扣款了", {}, {"investments"} | web),
            ("记一笔买菜花了30", {}, {"expenses"} | web),
            ("宽带续约了，新价格", {}, {"contracts"} | web),
            ("帮我把这份保单存档", {}, {"documents", "contracts", "expenses"} | web),
            ("提醒我明天打电话", {}, {"todos"} | web),
            ("今天在 Awesome Walls 上课带了两组 taster", {}, {"cwi"} | web),
        ]
        for text, kw, want in cases:
            passed &= check(repr(text), router.route_domains(text, **kw), want)

        # ASCII false-positive guard (#4): unrelated English must not substring-
        # match a domain and narrow-route — it falls through to ALL.
        passed &= check("'outback steakhouse' (not 'tb')",
                        router.route_domains("outback steakhouse"), set(ALL_DOMAINS))
        passed &= check("'a subpdd note' (not '\\bpdd\\b')",
                        router.route_domains("a subpdd note"), set(ALL_DOMAINS))

        # Input-type rules.
        passed &= check("has_document", router.route_domains("", has_document=True),
                        {"documents", "contracts", "expenses"} | web)
        passed &= check("has_image", router.route_domains("", has_image=True),
                        {"parcels", "expenses"} | web)

        # Multi-domain union.
        passed &= check("multi-domain", router.route_domains("买了显卡，提醒我交宽带续费"),
                        {"parcels", "todos", "contracts"} | web)

        # No signal => ALL fallback (zero regression).
        passed &= check("no-hit => ALL", router.route_domains("今天天气不错"), set(ALL_DOMAINS))
    finally:
        router.ROUTING_ENABLED = original  # don't leak the flag to other code

    print("PASS" if passed else "SOME CHECKS FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
