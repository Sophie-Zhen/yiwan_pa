"""Markdown parser/writer for the inbox/archive todo store.

The bot's state lives in two markdown files:
- data/inbox.md   — pending items
- data/archive.md — done / cancelled items

Each item is a level-2 heading followed by `- key: value` lines (see
data/README.md for the full spec). This module exposes typed helpers so
backends can manipulate the store without re-parsing markdown by hand.
"""
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
INBOX_PATH = DATA_DIR / "inbox.md"
ARCHIVE_PATH = DATA_DIR / "archive.md"

VALID_STATUSES = {"pending", "in_progress", "done", "cancelled"}
TERMINAL_STATUSES = {"done", "cancelled"}
VALID_TYPES = {None, "project"}
VALID_MODES = {None, "sequential", "parallel"}
# Status removed: status changes go through set_item_status / move_to_archive,
# not the general update path, so the in_progress invariant is enforced.
# Type / mode / project also not here — they're set at creation, not edited.
UPDATABLE_FIELDS = {"title", "due", "tags", "notes", "alerts"}

INBOX_HEADER = "# Inbox\n\nActive todos. New items appended at the top.\n\n---\n"
ARCHIVE_HEADER = "# Archive\n\nCompleted and cancelled items. Most recent first.\n\n---\n"

_FIELD_LINE = re.compile(r"^- (\w+):\s*(.+)$")


def _parse_offsets(field_name: str, value: Optional[str]) -> list[int]:
    """Parse a comma-separated string of non-negative ints (minute offsets).
    Empty / None returns []. 0 is valid and means "fire at the due time
    itself" (T-0). Raises ValueError on malformed input.
    """
    if not value:
        return []
    out: list[int] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        try:
            n = int(chunk)
        except ValueError as exc:
            raise ValueError(
                f"invalid {field_name} entry {chunk!r}: not an integer"
            ) from exc
        if n < 0:
            raise ValueError(
                f"invalid {field_name} entry {n}: must be non-negative"
            )
        out.append(n)
    return out


@dataclass
class Item:
    title: str
    created: str
    status: str = "pending"
    # Project / step extension fields. type="project" marks a Project record;
    # mode is required on projects (sequential | parallel). project=<name>
    # marks a step belonging to that project. See data/README.md for the spec.
    type: Optional[str] = None
    mode: Optional[str] = None
    project: Optional[str] = None
    due: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    # Scheduler state. alerts = declared T-N offsets (minutes before due),
    # comma-separated. alerted = subset that has already fired. alerted_stale
    # = timestamp of the last stale-section appearance in morning digest.
    # All three are internal to the scheduler — _item_payload filters them
    # out before handing items to the LLM. See docs/decisions/0006-scheduler-state-schema.md.
    alerts: Optional[str] = None
    alerted: Optional[str] = None
    alerted_stale: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")
        if self.type not in VALID_TYPES:
            raise ValueError(f"invalid type: {self.type!r}")
        if self.mode not in VALID_MODES:
            raise ValueError(f"invalid mode: {self.mode!r}")
        if self.type == "project":
            if self.mode is None:
                raise ValueError("project record must have mode")
            if self.project is not None:
                raise ValueError("project record cannot have a project field")
        else:
            if self.mode is not None:
                raise ValueError("non-project item cannot have mode")
        # alerts / alerted: parse to validate format; alerted must be a
        # subset of alerts so we can't accidentally record a fire for an
        # offset the user never declared.
        declared = set(_parse_offsets("alerts", self.alerts))
        fired = set(_parse_offsets("alerted", self.alerted))
        extras = fired - declared
        if extras:
            raise ValueError(
                f"alerted contains offsets not in alerts: {sorted(extras)}"
            )

    def to_markdown(self) -> str:
        lines = [f"## {self.title}", f"- created: {self.created}"]
        if self.type:
            lines.append(f"- type: {self.type}")
        if self.mode:
            lines.append(f"- mode: {self.mode}")
        if self.project:
            lines.append(f"- project: {self.project}")
        if self.due:
            lines.append(f"- due: {self.due}")
        lines.append(f"- status: {self.status}")
        if self.tags:
            lines.append(f"- tags: {self.tags}")
        if self.notes:
            lines.append(f"- notes: {self.notes}")
        if self.alerts:
            lines.append(f"- alerts: {self.alerts}")
        if self.alerted:
            lines.append(f"- alerted: {self.alerted}")
        if self.alerted_stale:
            lines.append(f"- alerted_stale: {self.alerted_stale}")
        return "\n".join(lines)


def _parse(content: str) -> list[Item]:
    """Split markdown content into Item objects, one per `## ` section."""
    items: list[Item] = []
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    for sec in sections:
        lines = sec.strip().split("\n")
        if not lines:
            continue
        title = lines[0].strip()
        fields: dict[str, str] = {}
        for line in lines[1:]:
            m = _FIELD_LINE.match(line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        items.append(
            Item(
                title=title,
                created=fields.get("created", ""),
                status=fields.get("status", "pending"),
                type=fields.get("type"),
                mode=fields.get("mode"),
                project=fields.get("project"),
                due=fields.get("due"),
                tags=fields.get("tags"),
                notes=fields.get("notes"),
                alerts=fields.get("alerts"),
                alerted=fields.get("alerted"),
                alerted_stale=fields.get("alerted_stale"),
            )
        )
    return items


def _ensure_file(path: pathlib.Path, header: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + "\n", encoding="utf-8")


def _write(path: pathlib.Path, header: str, items: list[Item]) -> None:
    body = "\n\n".join(item.to_markdown() for item in items)
    sep = "\n\n" if body else ""
    path.write_text(header + sep + body + "\n", encoding="utf-8")


def read_inbox() -> list[Item]:
    _ensure_file(INBOX_PATH, INBOX_HEADER)
    return _parse(INBOX_PATH.read_text(encoding="utf-8"))


def read_archive() -> list[Item]:
    _ensure_file(ARCHIVE_PATH, ARCHIVE_HEADER)
    return _parse(ARCHIVE_PATH.read_text(encoding="utf-8"))


def append_to_inbox(item: Item) -> None:
    """Add an item at the top of inbox (newest first)."""
    items = read_inbox()
    items.insert(0, item)
    _write(INBOX_PATH, INBOX_HEADER, items)


def update_inbox_item(
    title_substring: str, field: str, value: Optional[str]
) -> Optional[Item]:
    """Update one field on the first inbox item whose title contains
    `title_substring` (case-insensitive). Returns the updated item, or None
    if no item matched.

    Special handling for field='alerts': the new value is validated as a
    parseable offset list (raises ValueError on malformed input), and
    `alerted` is reset to None so previously-fired offsets from the old
    declaration don't carry over (a fresh declaration means fresh state).
    """
    if field not in UPDATABLE_FIELDS:
        raise ValueError(f"unknown field: {field!r}")
    if field == "alerts":
        _parse_offsets("alerts", value)  # validate; raises on bad input
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            setattr(item, field, value)
            if field == "alerts":
                item.alerted = None
            _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def move_to_archive(
    title_substring: str, terminal_status: str
) -> Optional[Item]:
    """Move the first matching inbox item to archive with the given terminal
    status. Returns the moved item, or None if no item matched.
    """
    if terminal_status not in TERMINAL_STATUSES:
        raise ValueError(
            f"terminal_status must be in {TERMINAL_STATUSES}, got {terminal_status!r}"
        )
    inbox = read_inbox()
    archive = read_archive()
    needle = title_substring.lower()
    target: Optional[Item] = None
    remaining: list[Item] = []
    for item in inbox:
        if target is None and needle in item.title.lower():
            target = item
        else:
            remaining.append(item)
    if target is None:
        return None
    target.status = terminal_status
    archive.insert(0, target)
    _write(INBOX_PATH, INBOX_HEADER, remaining)
    _write(ARCHIVE_PATH, ARCHIVE_HEADER, archive)
    return target


def find_item(title_substring: str) -> list[tuple[str, Item]]:
    """Search inbox + archive for items whose title contains `title_substring`
    (case-insensitive). Returns (location, item) tuples; location is
    "inbox" or "archive". Empty list = not found in either file.
    """
    needle = title_substring.lower()
    matches: list[tuple[str, Item]] = []
    for item in read_inbox():
        if needle in item.title.lower():
            matches.append(("inbox", item))
    for item in read_archive():
        if needle in item.title.lower():
            matches.append(("archive", item))
    return matches


def append_to_notes(title_substring: str, value: str) -> Optional[Item]:
    """Append text to an inbox item's notes field without overwriting the old
    value. Joins with "; " when notes already has content. notes is a single
    markdown line — newlines won't round-trip through _parse, so we deliberately
    keep the result one-line. Returns the updated item, or None if no item
    matched.
    """
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            if item.notes:
                item.notes = f"{item.notes}; {value}"
            else:
                item.notes = value
            _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def set_item_status(title_substring: str, new_status: str) -> Optional[Item]:
    """Change the status of the first matching inbox item. The in_progress
    invariant (sequential project: at most one in_progress step) is enforced
    by the caller (the set_status tool), not here — this helper only writes
    the new value. Terminal statuses should go through move_to_archive, not
    here, since they also move the item to archive.md.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status!r}")
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            item.status = new_status
            _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def mark_alerted(title_substring: str, offset: int) -> Optional[Item]:
    """Record that the T-{offset}-minute alert for an item has fired.
    Appends offset to the item's alerted list (no-op if already present).
    Raises ValueError if offset is not in the item's declared alerts —
    the scheduler should never record a fire for an offset the user didn't
    declare. Returns the updated item, or None if no item matched.
    """
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            declared = _parse_offsets("alerts", item.alerts)
            if offset not in declared:
                raise ValueError(
                    f"cannot mark offset {offset} as alerted on "
                    f"{item.title!r} — declared alerts are {declared}"
                )
            fired = _parse_offsets("alerted", item.alerted)
            if offset not in fired:
                fired.append(offset)
                item.alerted = ",".join(str(o) for o in fired)
                _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def mark_stale_alerted(title_substring: str, when: str) -> Optional[Item]:
    """Record the timestamp when an item last appeared in the morning
    digest's stale section. Returns the updated item, or None if no item
    matched.
    """
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            item.alerted_stale = when
            _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def skip_remaining_alerts(title_substring: str) -> Optional[Item]:
    """Mark every declared alert offset on the matching item as already
    fired (set alerted := alerts). Used when the user replies to a late
    alert with 'skip <item>' — the remaining T-N pushes are suppressed
    without losing the user's declared preferences (alerts field stays).
    No-op (still returns the item) if there are no declared alerts or
    they're all already fired. Returns None if no item matched.
    """
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            declared = _parse_offsets("alerts", item.alerts)
            if not declared:
                return item
            new_alerted = ",".join(str(o) for o in declared)
            if item.alerted != new_alerted:
                item.alerted = new_alerted
                _write(INBOX_PATH, INBOX_HEADER, items)
            return item
    return None


def get_stale_items(
    days: int = 7, now: Optional[datetime] = None
) -> list[Item]:
    """Return pending inbox items that have no due date, were created
    more than `days` ago, and have not been surfaced in the stale digest
    section within the last `days`. `now` lets tests inject a fixed time.

    Stale = the user forgot about it. We only consider status=pending
    (in_progress means actively being worked on, not stale). We only
    consider items with due=None — items with a due date are surfaced
    by the Today / Upcoming / Overdue groups instead.
    """
    if now is None:
        now = datetime.now()
    threshold = timedelta(days=days)
    out: list[Item] = []
    for item in read_inbox():
        if item.status != "pending":
            continue
        if item.due is not None:
            continue
        try:
            created = datetime.strptime(item.created, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if now - created < threshold:
            continue
        if item.alerted_stale:
            try:
                last = datetime.strptime(item.alerted_stale, "%Y-%m-%d %H:%M")
            except ValueError:
                last = None
            if last is not None and now - last < threshold:
                continue
        out.append(item)
    return out
