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
from typing import Optional

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
INBOX_PATH = DATA_DIR / "inbox.md"
ARCHIVE_PATH = DATA_DIR / "archive.md"

VALID_STATUSES = {"pending", "done", "cancelled"}
TERMINAL_STATUSES = {"done", "cancelled"}
UPDATABLE_FIELDS = {"title", "due", "status", "tags", "notes"}

INBOX_HEADER = "# Inbox\n\nActive todos. New items appended at the top.\n\n---\n"
ARCHIVE_HEADER = "# Archive\n\nCompleted and cancelled items. Most recent first.\n\n---\n"

_FIELD_LINE = re.compile(r"^- (\w+):\s*(.+)$")


@dataclass
class Item:
    title: str
    created: str
    status: str = "pending"
    due: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None

    def to_markdown(self) -> str:
        lines = [f"## {self.title}", f"- created: {self.created}"]
        if self.due:
            lines.append(f"- due: {self.due}")
        lines.append(f"- status: {self.status}")
        if self.tags:
            lines.append(f"- tags: {self.tags}")
        if self.notes:
            lines.append(f"- notes: {self.notes}")
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
                due=fields.get("due"),
                tags=fields.get("tags"),
                notes=fields.get("notes"),
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
    """
    if field not in UPDATABLE_FIELDS:
        raise ValueError(f"unknown field: {field!r}")
    items = read_inbox()
    needle = title_substring.lower()
    for item in items:
        if needle in item.title.lower():
            setattr(item, field, value)
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
