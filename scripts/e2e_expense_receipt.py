"""End-to-end test: real receipt/checkout photo → vision → record_purchase.

Exercises the FULL production path (real system prompt + real Anthropic vision
API + real tool loop + real sheet), reproducing the two-turn propose-confirm
flow exactly as the Telegram bot runs it:

  Turn 1: image + caption. The model should PROPOSE the parsed lines and ask to
          confirm — and must NOT write to the sheet yet.
  Turn 2: plain-text "对" (image is gone, as in production where history stores
          only a "[图片]" marker). The model should now call record_purchase.

Verifies row counts before/after each turn, prints both replies and the rows
written, then deletes the rows it created.

Requires a JPEG (vision API rejects HEIC). Convert first:
    sips -s format jpeg ~/Downloads/IMG_4121.HEIC --out /tmp/expense_test/receipt.jpg

Run:
    conda run -n assistant python scripts/e2e_expense_receipt.py [path-to.jpg]
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from llm import get_backend
from prompts import render_personal_assistant
from tools import expenses as exp

load_dotenv()

IMAGE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/expense_test/receipt.jpg")
CAPTION = "超市结账屏幕，记一下花销"
CONFIRM = "对，记吧"


def _data_row_count() -> int:
    return len(exp.find_purchase())


def _now_prefix(msg: str) -> str:
    return f"[Now: {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{msg}"


def main() -> None:
    if not IMAGE_PATH.exists():
        print(f"[fatal] image not found: {IMAGE_PATH}")
        print("Convert the HEIC first (see module docstring).")
        sys.exit(1)

    backend = get_backend()
    system_prompt = render_personal_assistant(os.getenv("USER_NAME", "Sophie"))
    image_bytes = IMAGE_PATH.read_bytes()

    before = _data_row_count()
    print(f"明细 data rows before: {before}")

    # --- Turn 1: image + caption. Expect a proposal, NO write. ---
    print("\n=== Turn 1: send photo (expect propose-confirm, no write) ===")
    reply1 = backend.chat(_now_prefix(CAPTION), system_prompt, None, [image_bytes])
    print(reply1)
    after_t1 = _data_row_count()
    print(f"\n明细 data rows after turn 1: {after_t1}")
    if after_t1 != before:
        print("[WARN] model wrote on turn 1 — propose-confirm guard not respected!")

    # --- Turn 2: plain-text confirm, image GONE (as in production). ---
    # History mirrors what the bot stores: the user turn is just a text marker
    # ("[图片] <caption>"), NOT the image bytes — the model must rely on its own
    # turn-1 proposal text, which is the assistant message below.
    history = [
        {"role": "user", "content": f"[图片] {CAPTION}"},
        {"role": "assistant", "content": reply1},
    ]
    print("\n=== Turn 2: confirm in text (expect record_purchase) ===")
    reply2 = backend.chat(_now_prefix(CONFIRM), system_prompt, history, None)
    print(reply2)
    after_t2 = _data_row_count()
    print(f"\n明细 data rows after turn 2: {after_t2}")

    new_count = after_t2 - after_t1
    print(f"\nrows written on confirm: {new_count}")

    # Show what landed, and clean up.
    created_rows: list[int] = []
    if new_count > 0:
        all_rows = exp.find_purchase()
        # newest rows are the ones with the highest row index
        all_rows.sort(key=lambda r: r["row"])
        written = all_rows[-new_count:]
        print("\n--- rows written ---")
        total = 0.0
        for r in written:
            created_rows.append(r["row"])
            print(f"  {r['store']:8} | {r['item']:24} | qty {r['quantity']:>5} {r['unit']:4} "
                  f"| @{r['unit_price']:>6} | = {r['subtotal']:>6} | {r['category']}")
            try:
                total += float(r["subtotal"])
            except ValueError:
                pass
        print(f"  sum of subtotals: {total:.2f}")

    print("\ncleaning up rows:", created_rows)
    if created_rows:
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["EXPENSES_SHEET_ID"]
        )
        tab = ss.worksheet(exp.LEDGER_TAB)
        for r in sorted(created_rows, reverse=True):
            tab.delete_rows(r)
    print("done")


if __name__ == "__main__":
    main()
