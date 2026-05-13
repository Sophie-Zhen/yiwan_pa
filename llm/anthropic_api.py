"""AnthropicBackend — direct anthropic SDK with a self-written agent loop.

In contrast to ClaudeCodeBackend (which delegates the agent loop to the local
`claude` CLI), this backend implements the loop itself: define tool schemas,
call the Messages API, inspect tool_use blocks, execute the corresponding
Python function, send the result back, repeat until the model is done.

This is the minimal harness — what Claude Code does at scale, in ~150 lines.

Each call is stateless: no conversation history is kept across invocations.
Persistent state lives in data/inbox.md and data/archive.md, manipulated via
the storage.markdown helpers.

Notes on configuration:
- Model defaults to Opus 4.7. Switch to `claude-sonnet-4-6` for ~3x lower
  cost on simple workloads if Opus feels excessive.
- Top-level `cache_control={"type": "ephemeral"}` auto-caches the last
  cacheable block. With render order tools → system → messages, this caches
  tools + system together. Subsequent loop turns within the same chat()
  call (and chats within ~5 minutes) read from cache instead of paying full
  input price for the prefix.
- `thinking={"type": "adaptive"}` lets the model decide when extra reasoning
  helps. Off by default on Opus 4.7; turning it on gives headroom for harder
  intents without forcing thinking on simple ones.
"""
import json
import logging
from datetime import datetime
from typing import Any

import anthropic

from storage.markdown import (
    Item,
    append_to_inbox,
    find_item,
    move_to_archive,
    read_archive,
    read_inbox,
    update_inbox_item,
)

from .base import LLMBackend

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
# 16000 is the Anthropic-recommended default for non-streaming. It's a *cap*,
# not a target — short replies still cost only the tokens they actually use.
# Lowballing this (e.g. 1024) truncates batch operations: a single user message
# capturing N items emits N parallel tool_use blocks plus adaptive thinking,
# which easily exceeds 1024. Above ~16k, switch to streaming to avoid SDK
# HTTP timeouts.
MAX_TOKENS = 16000
MAX_LOOP_TURNS = 10  # safety bound — if exceeded, something is wrong


# Tool schemas — what the model "sees" as available capabilities. Anything not
# listed here, the model cannot call (the harness wouldn't know how to dispatch
# it anyway). Order matters for prompt caching: keep this list stable so the
# cached prefix stays valid.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_inbox",
        "description": "Read all pending todo items currently in the inbox. Returns a list with title, status, due, tags, and notes for each item.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_archive",
        "description": "Read all completed or cancelled todo items in the archive.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "append_to_inbox",
        "description": "Add a new pending item to the top of the inbox. Use this when the user describes a new task or commitment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title summarising the item.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional due date or datetime in YYYY-MM-DD or YYYY-MM-DD HH:MM format.",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional space-separated #category or #category/sub tags.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional free-form context.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_inbox_item",
        "description": "Update one field of an existing inbox item. The item is matched by the first whose title contains title_substring (case-insensitive). Use this for modifications such as changing the due date or tags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
                "field": {
                    "type": "string",
                    "enum": ["title", "due", "status", "tags", "notes"],
                    "description": "Which field to update.",
                },
                "value": {
                    "type": "string",
                    "description": "New value for the field.",
                },
            },
            "required": ["title_substring", "field", "value"],
        },
    },
    {
        "name": "move_to_archive",
        "description": "Move an item from inbox to archive with a terminal status. The item is matched by the first whose title contains title_substring (case-insensitive). Use this when the user marks something done or cancelled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
                "terminal_status": {
                    "type": "string",
                    "enum": ["done", "cancelled"],
                    "description": "Terminal status to record on the moved item.",
                },
            },
            "required": ["title_substring", "terminal_status"],
        },
    },
    {
        "name": "find_item",
        "description": "Search both inbox AND archive for items whose title contains the given substring (case-insensitive). Use this to verify whether an item exists or to look up its state. Do NOT infer 'item doesn't exist' from read_inbox alone — read_inbox only returns pending items, while completed or cancelled items live in archive. Returns a list of matches, each with location ('inbox' or 'archive') plus the item fields. Empty list means not found in either file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
            },
            "required": ["title_substring"],
        },
    },
]


def _items_to_payload(items: list[Item]) -> list[dict[str, Any]]:
    """Compact serialisation for tool_result content — drops empty fields."""
    return [
        {
            k: v
            for k, v in {
                "title": item.title,
                "due": item.due,
                "status": item.status,
                "tags": item.tags,
                "notes": item.notes,
            }.items()
            if v is not None
        }
        for item in items
    ]


def _matches_to_payload(
    matches: list[tuple[str, Item]],
) -> list[dict[str, Any]]:
    """Serialise find_item matches; each entry carries its location."""
    return [
        {
            "location": loc,
            **{
                k: v
                for k, v in {
                    "title": item.title,
                    "due": item.due,
                    "status": item.status,
                    "tags": item.tags,
                    "notes": item.notes,
                }.items()
                if v is not None
            },
        }
        for loc, item in matches
    ]


def _execute_tool(name: str, args: dict[str, Any]) -> Any:
    """Dispatch a tool_use block to the corresponding storage function."""
    if name == "read_inbox":
        return _items_to_payload(read_inbox())
    if name == "read_archive":
        return _items_to_payload(read_archive())
    if name == "append_to_inbox":
        item = Item(
            title=args["title"],
            created=datetime.now().strftime("%Y-%m-%d %H:%M"),
            status="pending",
            due=args.get("due"),
            tags=args.get("tags"),
            notes=args.get("notes"),
        )
        append_to_inbox(item)
        return {"ok": True, "title": item.title}
    if name == "update_inbox_item":
        updated = update_inbox_item(
            args["title_substring"], args["field"], args["value"]
        )
        if updated is None:
            return {"ok": False, "reason": "no item matched"}
        return {
            "ok": True,
            "title": updated.title,
            "field": args["field"],
            "value": args["value"],
        }
    if name == "move_to_archive":
        moved = move_to_archive(args["title_substring"], args["terminal_status"])
        if moved is None:
            return {"ok": False, "reason": "no item matched"}
        return {
            "ok": True,
            "title": moved.title,
            "status": args["terminal_status"],
        }
    if name == "find_item":
        return _matches_to_payload(find_item(args["title_substring"]))
    raise ValueError(f"unknown tool: {name!r}")


def _extract_text(content: list[Any]) -> str:
    """Concatenate text blocks from a model response. Skips thinking/tool_use blocks."""
    return "\n".join(b.text for b in content if b.type == "text").strip()


class AnthropicBackend(LLMBackend):
    def __init__(self, model: str = MODEL) -> None:
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> str:
        # Conversation history (if any) goes at the front of the messages
        # list so the model sees prior turns as context for the new one.
        # The agent loop appends its own assistant + tool_result blocks on
        # top of this during a single chat() call.
        messages: list[dict[str, Any]] = list(history) if history else []
        messages.append({"role": "user", "content": user_message})

        for turn in range(MAX_LOOP_TURNS):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": MAX_TOKENS,
                "tools": TOOLS,
                "messages": messages,
                "thinking": {"type": "adaptive"},
                "cache_control": {"type": "ephemeral"},
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            try:
                response = self.client.messages.create(**kwargs)
            except anthropic.APIStatusError as exc:
                logger.exception("Anthropic API error on turn %d", turn)
                raise RuntimeError(f"Anthropic API error: {exc}") from exc

            usage = response.usage
            logger.info(
                "turn=%d stop_reason=%s in=%d out=%d cache_read=%d cache_create=%d",
                turn,
                response.stop_reason,
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )

            if response.stop_reason == "refusal":
                return "[refusal] The model declined to respond."

            if response.stop_reason == "max_tokens":
                # Hit the per-response cap; surface what we have so far.
                partial = _extract_text(response.content)
                return partial + "\n[truncated: max_tokens reached]"

            if response.stop_reason in ("end_turn", "stop_sequence"):
                return _extract_text(response.content)

            if response.stop_reason != "tool_use":
                logger.warning("unexpected stop_reason: %s", response.stop_reason)
                return _extract_text(response.content)

            # tool_use: dispatch every tool_use block, then loop with results.
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                logger.info("tool_use: %s(%s)", block.name, block.input)
                try:
                    result = _execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                except Exception as exc:
                    logger.exception("tool %s failed", block.name)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": str(exc)}),
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"agent loop exceeded MAX_LOOP_TURNS={MAX_LOOP_TURNS}; "
            "the model is likely stuck in a tool-use cycle."
        )
