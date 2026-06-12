"""Unit test for tools/documents.py. Redirects DOCS_DIR to a temp dir.

Run:
    conda run -n assistant python scripts/test_documents.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import documents as docs


def main() -> None:
    docs.DOCS_DIR = Path(tempfile.mkdtemp()) / "documents"

    print("\n=== slugify ===")
    assert docs.slugify("AXA 车险保单 2026") == "axa-车险保单-2026", docs.slugify("AXA 车险保单 2026")
    assert docs.slugify("Home Insurance!!!") == "home-insurance"
    print("ok")

    print("\n=== save_document ===")
    fact_sheet = """## 关键信息
- 保单号: POL-12345
- 保费: €560/year
- 免赔额 (excess): €250
- 保额上限: €30,000
- 主要除外: 故意损坏、赛道驾驶

## 摘要
AXA 全年综合车险，含第三方责任与碰撞。免赔额 250 欧。"""
    r = docs.save_document(
        name="AXA 车险保单 2026", doc_type="car_insurance", fact_sheet=fact_sheet,
        file="axa-车险保单-2026.pdf", source_date="2026-06-12", expiry="2027-06-15",
    )
    print(r)
    assert r["slug"] == "axa-车险保单-2026"

    print("\n=== validation ===")
    try:
        docs.save_document(name="bad", doc_type="spaceship", fact_sheet="x")
        assert False
    except ValueError as e:
        print(f"ok: bad type → {e}")
    try:
        docs.save_document(name="bad", doc_type="other", fact_sheet="x", expiry="2027/06/15")
        assert False
    except ValueError as e:
        print(f"ok: bad date → {e}")

    print("\n=== list_documents (header only) ===")
    lst = docs.list_documents()
    print(lst)
    assert len(lst) == 1
    d = lst[0]
    assert d["name"] == "AXA 车险保单 2026"
    assert d["type"] == "car_insurance"
    assert d["expiry"] == "2027-06-15"
    assert d["file"] == "axa-车险保单-2026.pdf"

    print("\n=== read_document (full fact sheet) ===")
    full = docs.read_document("车险")
    assert "保单号: POL-12345" in full["fact_sheet"]
    assert "免赔额 (excess): €250" in full["fact_sheet"]
    assert full["expiry"] == "2027-06-15"
    print("ok: body round-trips, header parsed")

    print("\n=== upsert (re-save same name overwrites) ===")
    docs.save_document(name="AXA 车险保单 2026", doc_type="car_insurance",
                       fact_sheet="## 关键信息\n- 保费: €600/year (renewed)")
    assert len(docs.list_documents()) == 1, "re-save should overwrite, not duplicate"
    full2 = docs.read_document("车险")
    assert "€600/year" in full2["fact_sheet"]
    print("ok: overwrote in place")

    print("\n--- file on disk ---")
    print((docs.DOCS_DIR / "axa-车险保单-2026.md").read_text())
    print("[all document assertions passed]")


if __name__ == "__main__":
    main()
