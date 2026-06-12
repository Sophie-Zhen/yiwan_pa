"""End-to-end (real LLM) test of the contract conversational flow.

Redirects CONTRACTS_PATH to a temp file, then runs a multi-turn text
conversation through the real system prompt + Anthropic API + tool loop, and
asserts the resulting contracts.md. Guards the subtle behavior: a renewal must
go through renew_contract (rotating price history), NOT update_contract.

Run:
    conda run -n assistant python scripts/e2e_contract_text.py
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from llm import get_backend
from prompts import render_personal_assistant
from tools import contracts as ct

load_dotenv()


def _now(msg: str) -> str:
    return f"[Now: {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{msg}"


def main() -> None:
    ct.CONTRACTS_PATH = Path(tempfile.mkdtemp()) / "contracts.md"

    backend = get_backend()
    system = render_personal_assistant(os.getenv("USER_NAME", "Sophie"))
    history: list[dict] = []

    def turn(msg: str) -> str:
        nonlocal history
        reply = backend.chat(_now(msg), system, history, None)
        history = history + [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": reply},
        ]
        print(f"\n>>> {msg}\n{reply}")
        return reply

    turn("记一下能源合同：Electric Ireland，2026-08-01 到期，现在 0.42/kWh，提前两周提醒我比价")
    c = next((x for x in ct.list_contracts() if "Electric" in x["name"]), None)
    assert c is not None, "add_contract not called / wrong name"
    assert c["type"] == "energy", c
    assert c["expiry"] == "2026-08-01", c
    assert c["remind_on"] == "2026-07-18", f"expected 2-week lead (2026-07-18), got {c['remind_on']}"
    print("  [check] added: energy, expiry 2026-08-01, remind 2 weeks before")

    reply = turn("我有哪些合同")
    assert "Electric" in reply or "能源" in reply, reply
    print("  [check] list surfaced the contract")

    turn("能源合同续约了，新到期 2027-08-01，今年 0.39/kWh")
    c = next((x for x in ct.list_contracts() if "Electric" in x["name"]), None)
    assert c["expiry"] == "2027-08-01", f"expiry not rolled: {c}"
    assert c["current_price"] and "0.39" in c["current_price"], c
    assert c["prev_price"] and "0.42" in c["prev_price"], \
        f"renewal must rotate old price into prev_price (got {c['prev_price']}) — did it use update instead of renew?"
    assert c["last_reminded"] is None, "renew must re-arm (clear last_reminded)"
    print("  [check] renewal: expiry→2027, price 0.42→prev / 0.39→current, re-armed")

    print("\n--- final contracts.md ---")
    print(ct.CONTRACTS_PATH.read_text())
    print("[contract e2e passed]")


if __name__ == "__main__":
    main()
