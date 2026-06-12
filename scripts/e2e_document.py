"""End-to-end test: PDF → Opus extract → propose-confirm → save → Q&A.

Runs the full production path (real system prompt + real Anthropic API with a
PDF document block + real tool loop + real fact-sheet store), reproducing the
two-turn ingest flow the bot's handle_document drives, then asks a question and
checks it's answered from the saved fact-sheet.

DOCS_DIR is redirected to a temp dir, so nothing touches real data.

Needs a PDF (default the synthetic policy under /tmp/doc_test/policy.pdf):
    cupsfilter policy.txt > /tmp/doc_test/policy.pdf

Run:
    conda run -n assistant python scripts/e2e_document.py [path-to.pdf]
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
from tools import documents as docs

load_dotenv()

PDF_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/doc_test/policy.pdf")
SAVED_NAME = "policy.pdf"


def _now(msg: str) -> str:
    return f"[Now: {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{msg}"


def main() -> None:
    if not PDF_PATH.exists():
        print(f"[fatal] PDF not found: {PDF_PATH} (see module docstring)")
        sys.exit(1)

    docs.DOCS_DIR = Path(tempfile.mkdtemp()) / "documents"
    docs.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_bytes = PDF_PATH.read_bytes()
    (docs.DOCS_DIR / SAVED_NAME).write_bytes(pdf_bytes)  # mimic handle_document persisting the original

    backend = get_backend()
    system = render_personal_assistant(os.getenv("USER_NAME", "Sophie"))

    # --- Turn 1: PDF in, expect a proposed fact-sheet, NO save yet ---
    print("=== Turn 1: send PDF (expect propose-confirm, no save) ===")
    tag = f"[文档 PDF: AXA car insurance.pdf，原件已存为 {SAVED_NAME}] 存一下这个车险保单"
    reply1 = backend.chat(_now(tag), system, None, None, [pdf_bytes])
    print(reply1)
    after_t1 = len(docs.list_documents())
    print(f"\ndocuments after turn 1: {after_t1}")
    if after_t1 != 0:
        print("[WARN] model saved on turn 1 without confirmation")

    # --- Turn 2: confirm in text, PDF gone (as in production) ---
    history = [
        {"role": "user", "content": f"[文档 PDF: AXA car insurance.pdf] 存一下这个车险保单"},
        {"role": "assistant", "content": reply1},
    ]
    print("\n=== Turn 2: confirm (expect save_document) ===")
    reply2 = backend.chat(_now("对，存吧"), system, history, None, None)
    print(reply2)
    saved = docs.list_documents()
    print(f"\ndocuments after turn 2: {[d['name'] for d in saved]}")
    assert len(saved) >= 1, "save_document was not called on confirm"
    doc = saved[0]
    assert doc["type"] in ("car_insurance", "insurance"), doc
    fact = docs.read_document(doc["name"])["fact_sheet"]
    print("\n--- saved fact-sheet ---")
    print(fact)
    assert "250" in fact, "excess €250 should be captured in the fact-sheet"
    assert "560" in fact, "premium €560 should be captured"

    history += [
        {"role": "user", "content": "对，存吧"},
        {"role": "assistant", "content": reply2},
    ]

    # --- Turn 3: Q&A answered from the fact-sheet (no PDF reload) ---
    print("\n=== Turn 3: ask a question (answer from fact-sheet) ===")
    reply3 = backend.chat(_now("我的车险免赔额是多少？"), system, history, None, None)
    print(reply3)
    assert "250" in reply3, f"expected the €250 excess in the answer: {reply3}"
    print("\n[document e2e passed]")


if __name__ == "__main__":
    main()
