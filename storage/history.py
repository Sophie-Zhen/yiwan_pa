"""Per-chat conversation history stored as JSONL files.

Each chat has its own file at data/history/<chat_id>.jsonl. Each line is a
JSON object:

    {"ts": <unix_seconds>, "role": "user" | "assistant", "content": "..."}

History is read with a sliding window: at most MAX_TURNS turns AND at most
MAX_AGE_SECONDS old, whichever is shorter. Writes are append-only; rotation
is intentionally not implemented in v0.1.1 (analysis in
docs/decisions/0001-conversation-history.md showed years of headroom at the
expected message rate).
"""
import json
import pathlib
import time
from typing import Any

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "history"

# Sliding window: 6 turns = 12 messages, OR 30 minutes — whichever is shorter.
MAX_TURNS = 6
MAX_AGE_SECONDS = 30 * 60


def _path(chat_id: int) -> pathlib.Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{chat_id}.jsonl"


def read_history(chat_id: int) -> list[dict[str, str]]:
    """Return recent history for a chat in oldest-first order.

    The result is shaped for direct injection into the Anthropic Messages API
    (or any API expecting a list of {role, content} dicts). Timestamps used
    for the age filter are stripped from the return value — the API doesn't
    want them.
    """
    path = _path(chat_id)
    if not path.exists():
        return []

    cutoff = time.time() - MAX_AGE_SECONDS
    raw_lines = path.read_text(encoding="utf-8").strip().splitlines()

    entries: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip a corrupted line rather than crash the whole conversation.
            # The next append will write fine; the bad line just gets ignored.
            continue

    fresh = [e for e in entries if e.get("ts", 0) >= cutoff]

    # Cap to MAX_TURNS turns = MAX_TURNS * 2 messages (user + assistant pairs).
    max_messages = MAX_TURNS * 2
    fresh = fresh[-max_messages:]

    return [{"role": e["role"], "content": e["content"]} for e in fresh]


def append_turn(chat_id: int, user_message: str, assistant_reply: str) -> None:
    """Append a single turn (user message + assistant reply) to the chat's history file.

    Both entries get the same timestamp — the assistant reply happens "at" the
    moment of the user message from the conversation's POV; sub-second
    precision isn't useful here.
    """
    now = time.time()
    path = _path(chat_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"ts": now, "role": "user", "content": user_message},
                ensure_ascii=False,
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {"ts": now, "role": "assistant", "content": assistant_reply},
                ensure_ascii=False,
            )
            + "\n"
        )
