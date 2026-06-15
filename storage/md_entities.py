"""Shared skeleton for the `## heading` + `- key: value` markdown entity stores.

tools/contracts.py and tools/cwi_log.py are two near-identical hand-editable
markdown stores: a header, then one entity per `## ` section with `- key: value`
field lines. This module owns the parse/write/validate machinery they share;
each domain keeps only its own dataclass, to_markdown, and field mapping.

Note the field regex allows EMPTY values (`.*`), so a bare `- prev_price:` line
round-trips to "" → None — this is load-bearing for those stores and differs
from storage/markdown.py (the todo store), which requires non-empty values and
has its own parser. Imports nothing from tools/ — one-way tools -> storage edge.
"""

import pathlib
import re
from datetime import date

_FIELD_LINE = re.compile(r"^- (\w+):\s*(.*)$")


def parse_sections(content: str) -> list[tuple[str, dict[str, str]]]:
    """Split an entity-store file into (heading, fields) per `## ` section.

    heading is the text after `## ` on the section's first line; fields is the
    `- key: value` lines below it. Returns every section — domain-specific
    guards (e.g. "skip sections with no id") stay in the caller.
    """
    out: list[tuple[str, dict[str, str]]] = []
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    for sec in sections:
        lines = sec.strip().split("\n")
        if not lines:
            continue
        heading = lines[0].strip()
        fields: dict[str, str] = {}
        for line in lines[1:]:
            m = _FIELD_LINE.match(line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        out.append((heading, fields))
    return out


def ensure_file(path: pathlib.Path, header: str) -> None:
    """Create the store file with its header if it does not yet exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + "\n", encoding="utf-8")


def write_entities(path: pathlib.Path, header: str, entities: list) -> None:
    """Write the whole store: header + each entity's to_markdown(), separated by
    blank lines. An empty list writes just the header (still hand-editable)."""
    body = "\n\n".join(e.to_markdown() for e in entities)
    sep = "\n\n" if body else ""
    path.write_text(header + sep + body + "\n", encoding="utf-8")


def validate_date(label: str, value: str) -> None:
    """Raise ValueError unless value is an ISO YYYY-MM-DD date."""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc
