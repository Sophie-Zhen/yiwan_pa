"""Document fact-sheets: distil a PDF once at ingest, answer from the distillate.

The cost model is inverted on purpose. A document (insurance policy, warranty)
arrives ~once a year and is then read by the model exactly once — an Opus pass
extracts the key facts into a fact-sheet. Afterwards every question is answered
from that small fact-sheet, never the full PDF. Pay once at write time; read
cheaply forever. (Full-PDF fallback for the rare uncovered question is a later
phase — over-extract at ingest to keep that rare.)

One self-contained markdown file per document under data/documents/:

    data/documents/<slug>.md   — the fact-sheet (header + key facts + summary)
    data/documents/<slug>.pdf  — the original (source of truth / future fallback)

Fact-sheet file shape:

    # AXA 车险保单 2026
    - type: car_insurance
    - file: axa-车险保单-2026.pdf
    - source_date: 2026-06-12
    - expiry: 2027-06-15

    <the extracted fact-sheet body: key facts + summary, rich markdown>

The header lines (single-line `- key: value`) are the cheap "index" — listing
the directory and parsing headers tells the model which document a question is
about, without loading any fact-sheet body.
"""
import pathlib
import re
from datetime import date
from typing import Optional

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DOCS_DIR = DATA_DIR / "documents"

VALID_TYPES = {
    "insurance", "car_insurance", "home_insurance", "warranty", "manual",
    "contract", "statement", "other",
}

_HEADER_LINE = re.compile(r"^- (\w+):\s*(.*)$")
_SLUG_STRIP = re.compile(r"[^\w一-鿿-]+")  # keep word chars, CJK, hyphen


def slugify(name: str) -> str:
    """Filesystem-safe slug from a document name. Keeps CJK so a Chinese name
    stays readable (the FS handles UTF-8); collapses everything else to '-'.
    """
    s = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return s or "document"


def _ensure_dir() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_header(text: str) -> tuple[str, dict[str, str], str]:
    """Split a fact-sheet file into (title, header_fields, body).

    Title is the first `# ` line. Header fields are the `- key: value` lines
    immediately after it; the body is everything from the first blank line /
    non-header line onward.
    """
    lines = text.splitlines()
    title = ""
    fields: dict[str, str] = {}
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        m = _HEADER_LINE.match(stripped)
        if m and title and not fields.get("_body_started"):
            fields[m.group(1)] = m.group(2).strip()
            continue
        if stripped == "" and title and i <= len(fields) + 1:
            # blank line right after the header block — body starts next
            body_start = i + 1
            continue
        # first real content line → body starts here
        body_start = i
        break
    body = "\n".join(lines[body_start:]).strip()
    return title, fields, body


def _doc_files() -> list[pathlib.Path]:
    if not DOCS_DIR.exists():
        return []
    return sorted(DOCS_DIR.glob("*.md"))


def _find_file(name: str) -> Optional[pathlib.Path]:
    """Match a fact-sheet file by document title or slug (case-insensitive
    substring). Returns the first match by sorted filename, or None.
    """
    needle = name.lower()
    for path in _doc_files():
        title, _, _ = _parse_header(path.read_text(encoding="utf-8"))
        if needle in title.lower() or needle in path.stem.lower():
            return path
    return None


def save_document(
    name: str,
    doc_type: str,
    fact_sheet: str,
    file: Optional[str] = None,
    source_date: Optional[str] = None,
    expiry: Optional[str] = None,
) -> dict:
    """Write a document's fact-sheet to data/documents/<slug>.md.

    `fact_sheet` is the extracted body (rich markdown: key facts + summary).
    `file` is the stored original's filename (in data/documents/), recorded so
    a later full-read fallback can find it. Upserts by slug — re-saving the
    same name overwrites.
    """
    if doc_type not in VALID_TYPES:
        raise ValueError(f"doc_type must be one of {sorted(VALID_TYPES)}, got {doc_type!r}")
    if source_date is None:
        source_date = date.today().isoformat()
    for label, value in (("source_date", source_date), ("expiry", expiry)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc

    _ensure_dir()
    slug = slugify(name)
    path = DOCS_DIR / f"{slug}.md"

    header = [f"# {name}", f"- type: {doc_type}"]
    if file:
        header.append(f"- file: {file}")
    header.append(f"- source_date: {source_date}")
    if expiry:
        header.append(f"- expiry: {expiry}")
    content = "\n".join(header) + "\n\n" + fact_sheet.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return {"name": name, "slug": slug, "type": doc_type, "saved_to": path.name}


def list_documents() -> list[dict]:
    """List stored documents from their fact-sheet headers (cheap — does not
    load the bodies). Use to route a question to the right document and to
    answer 'what documents do I have'.
    """
    out: list[dict] = []
    for path in _doc_files():
        title, fields, _ = _parse_header(path.read_text(encoding="utf-8"))
        out.append({
            "name": title or path.stem,
            "slug": path.stem,
            "type": fields.get("type", ""),
            "file": fields.get("file", ""),
            "source_date": fields.get("source_date", ""),
            "expiry": fields.get("expiry", ""),
        })
    return out


def read_document(name: str) -> dict:
    """Return a document's full fact-sheet (header + body) for Q&A. Match by
    title or slug substring. Raises if nothing matches. This is the normal
    answer path — small, cheap, no PDF reload.
    """
    path = _find_file(name)
    if path is None:
        raise ValueError(f"no document matches {name!r}")
    text = path.read_text(encoding="utf-8")
    title, fields, body = _parse_header(text)
    return {
        "name": title or path.stem,
        "type": fields.get("type", ""),
        "file": fields.get("file", ""),
        "expiry": fields.get("expiry", ""),
        "fact_sheet": body,
    }
