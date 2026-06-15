"""CWI (Climbing Wall Instructor) logbook tracking, stored in data/cwi_log.md.

Sophie is working toward the Mountaineering Ireland Climbing Wall Instructor
(CWI) certificate. Before assessment she must accumulate, in Mountain
Training's DLOG, a logbook of instructing and personal-climbing sessions. This
module is NOT a second system of record — the DLOG on MI's site is. It's a
lightweight staging + progress tracker:

- the bot drafts each DLOG reflective entry in chat (NOT stored here — she
  pastes it into MI's site);
- here we keep only BRIEF structured metadata per session, which powers (a) the
  evening "go enter today's sessions into DLOG" reminder and (b) a progress
  readout against the official requirements.

The assessment targets below are the OFFICIAL Mountaineering Ireland / Mountain
Training requirements (verified 2026-06, both sites agree). They are the
authoritative numbers — the bot only counts logged sessions against them, it
never infers the standard itself.

File format mirrors contracts.md — one entry per `## ` heading, `- key: value`
lines. Entries are append-mostly; status flips pending → recorded once she's
entered the session into the DLOG.
"""
import pathlib
from dataclasses import dataclass
from typing import Optional

from storage import md_entities

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CWI_LOG_PATH = DATA_DIR / "cwi_log.md"

# Official CWI pre-assessment logbook requirements (Mountaineering Ireland /
# Mountain Training). Verified against both sites 2026-06; they are the
# authoritative targets — progress() only counts against them.
TARGET_INSTRUCTED_SESSIONS = 15   # assisted-supervision instructed sessions
TARGET_INSTRUCTED_WALLS = 2       # across >= 2 walls (incl. a large public facility)
TARGET_REFLECTIVE = 5             # >= 5 of those with reflective comments on DLOG
TARGET_PERSONAL_VISITS = 30       # personal climbing visits
TARGET_PERSONAL_WALLS = 3         # across >= 3 different walls
TARGET_LEAD_CLIMBS = 40           # climbs led

CWI_LOG_HEADER = (
    "# CWI logbook\n\nBrief staging records for Mountaineering Ireland CWI DLOG "
    "entries. The full reflective text lives in the DLOG, not here. "
    "Hand-editable.\n\n---\n"
)

VALID_KINDS = {"instructed", "personal"}
VALID_STATUSES = {"pending", "recorded"}


@dataclass
class Entry:
    id: int
    date: str                      # YYYY-MM-DD
    kind: str = "instructed"       # instructed | personal
    venue: str = ""
    detail: str = ""               # instructed: session kind ("top-rope taster"); personal: free
    role: str = ""                 # instructed: led | assisted | supervised
    climbs_led: int = 0
    reflective: bool = False       # instructed: a reflective comment was written for DLOG
    large_public: bool = False     # instructed: venue is a large public facility
    status: str = "pending"        # pending | recorded (into MI DLOG)
    notes: Optional[str] = None

    def to_markdown(self) -> str:
        # Heading is a human-readable label derived from the fields; the fields
        # below are the source of truth (parsing reads them, not the heading).
        label = f"{self.date} · {self.kind}" + (f" · {self.venue}" if self.venue else "")
        lines = [f"## {label}"]
        lines.append(f"- id: {self.id}")
        lines.append(f"- date: {self.date}")
        lines.append(f"- kind: {self.kind}")
        lines.append(f"- venue: {self.venue}")
        lines.append(f"- detail: {self.detail}")
        lines.append(f"- role: {self.role}")
        lines.append(f"- climbs_led: {self.climbs_led}")
        lines.append(f"- reflective: {'yes' if self.reflective else 'no'}")
        lines.append(f"- large_public: {'yes' if self.large_public else 'no'}")
        lines.append(f"- status: {self.status}")
        if self.notes:
            lines.append(f"- notes: {self.notes}")
        return "\n".join(lines)


def _ensure_file() -> None:
    md_entities.ensure_file(CWI_LOG_PATH, CWI_LOG_HEADER)


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse(content: str) -> list[Entry]:
    entries: list[Entry] = []
    for _heading, fields in md_entities.parse_sections(content):
        if "id" not in fields:
            continue
        entries.append(
            Entry(
                id=_to_int(fields.get("id", "0")),
                date=fields.get("date", "") or "",
                kind=fields.get("kind", "instructed") or "instructed",
                venue=fields.get("venue", "") or "",
                detail=fields.get("detail", "") or "",
                role=fields.get("role", "") or "",
                climbs_led=_to_int(fields.get("climbs_led", "0")),
                reflective=(fields.get("reflective", "no") == "yes"),
                large_public=(fields.get("large_public", "no") == "yes"),
                status=fields.get("status", "pending") or "pending",
                notes=fields.get("notes") or None,
            )
        )
    return entries


def _read() -> list[Entry]:
    _ensure_file()
    return _parse(CWI_LOG_PATH.read_text(encoding="utf-8"))


def _write(entries: list[Entry]) -> None:
    md_entities.write_entities(CWI_LOG_PATH, CWI_LOG_HEADER, entries)


def _next_id(entries: list[Entry]) -> int:
    return max((e.id for e in entries), default=0) + 1


def _entry_payload(e: Entry) -> dict:
    return {
        "id": e.id,
        "date": e.date,
        "kind": e.kind,
        "venue": e.venue,
        "detail": e.detail,
        "role": e.role,
        "climbs_led": e.climbs_led,
        "reflective": e.reflective,
        "large_public": e.large_public,
        "status": e.status,
        "notes": e.notes,
    }


def log_instructed_session(
    date: str,
    venue: str,
    detail: str,
    role: str = "assisted",
    climbs_led: int = 0,
    reflective: bool = True,
    large_public_facility: bool = False,
    notes: Optional[str] = None,
) -> dict:
    """Record a brief metadata row for an instructing/assisting group session.
    The full reflective DLOG text is NOT stored here — it's drafted in chat and
    pasted into MI's DLOG. status starts 'pending' (not yet entered into DLOG).
    reflective defaults True because each session's DLOG entry IS a reflective
    comment.
    """
    md_entities.validate_date("date", date)
    entries = _read()
    entry = Entry(
        id=_next_id(entries),
        date=date,
        kind="instructed",
        venue=venue,
        detail=detail,
        role=role,
        climbs_led=climbs_led,
        reflective=reflective,
        large_public=large_public_facility,
        status="pending",
        notes=notes,
    )
    entries.append(entry)
    _write(entries)
    return {"id": entry.id, "date": date, "kind": "instructed",
            "venue": venue, "status": "pending"}


def log_personal_climb(
    date: str,
    venue: str,
    climbs_led: int = 0,
    detail: str = "",
    notes: Optional[str] = None,
) -> dict:
    """Record a brief metadata row for a personal climbing visit (Sophie's own
    training) — counts toward the 30-visits / 3-walls / 40-leads personal
    experience requirement. status starts 'pending'.
    """
    md_entities.validate_date("date", date)
    entries = _read()
    entry = Entry(
        id=_next_id(entries),
        date=date,
        kind="personal",
        venue=venue,
        detail=detail,
        climbs_led=climbs_led,
        status="pending",
        notes=notes,
    )
    entries.append(entry)
    _write(entries)
    return {"id": entry.id, "date": date, "kind": "personal",
            "venue": venue, "climbs_led": climbs_led, "status": "pending"}


def list_entries(
    status_filter: Optional[str] = None, kind: Optional[str] = None
) -> list[dict]:
    out: list[dict] = []
    for e in _read():
        if status_filter is not None and e.status != status_filter:
            continue
        if kind is not None and e.kind != kind:
            continue
        out.append(_entry_payload(e))
    return out


def pending_entries() -> list[dict]:
    """Entries not yet marked as entered into the MI DLOG. Drives the evening
    reminder and answers 'what haven't I recorded yet'."""
    return list_entries(status_filter="pending")


def mark_recorded(ids: Optional[list[int]] = None) -> dict:
    """Mark entries as entered into the MI DLOG (stops the evening reminder for
    them). ids=None marks ALL pending entries (the common 'all done' case);
    otherwise marks just the given ids.
    """
    entries = _read()
    target = set(ids) if ids is not None else None
    changed: list[int] = []
    for e in entries:
        if e.status != "pending":
            continue
        if target is None or e.id in target:
            e.status = "recorded"
            changed.append(e.id)
    if changed:
        _write(entries)
    return {"recorded": changed, "count": len(changed)}


def progress() -> dict:
    """Count logged sessions against the official CWI pre-assessment targets.
    Counts ALL logged entries regardless of recorded status (recorded just means
    'already entered into DLOG' — it still counts toward the requirement). The
    targets are the authoritative MI numbers; this only counts and compares.
    """
    entries = _read()
    instructed = [e for e in entries if e.kind == "instructed"]
    personal = [e for e in entries if e.kind == "personal"]

    def _walls(es: list[Entry]) -> int:
        return len({e.venue.strip().lower() for e in es if e.venue.strip()})

    reflective_count = sum(1 for e in instructed if e.reflective)
    leads = sum(e.climbs_led for e in entries)

    def _block(done: int, target: int) -> dict:
        return {"done": done, "target": target, "remaining": max(0, target - done)}

    return {
        "instructed_sessions": {
            **_block(len(instructed), TARGET_INSTRUCTED_SESSIONS),
            "walls": _walls(instructed),
            "walls_target": TARGET_INSTRUCTED_WALLS,
            "has_large_public_facility": any(e.large_public for e in instructed),
            "reflective": reflective_count,
            "reflective_target": TARGET_REFLECTIVE,
        },
        "personal_visits": {
            **_block(len(personal), TARGET_PERSONAL_VISITS),
            "walls": _walls(personal),
            "walls_target": TARGET_PERSONAL_WALLS,
        },
        "lead_climbs": _block(leads, TARGET_LEAD_CLIMBS),
    }
