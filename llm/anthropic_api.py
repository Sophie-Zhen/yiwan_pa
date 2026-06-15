"""AnthropicBackend — direct anthropic SDK with a self-written agent loop.

In contrast to ClaudeCodeBackend (which delegates the agent loop to the local
`claude` CLI), this backend implements the loop itself: call the Messages API,
inspect tool_use blocks, execute the corresponding Python function, send the
result back, repeat until the model is done.

This is the minimal harness — what Claude Code does at scale, in ~150 lines.

Tool schemas and dispatch no longer live here: they're split per domain under
`llm/tooldefs/`, which exposes `TOOLS` (the schema list, order-stable for prompt
caching) and `execute_tool(name, args)` (the dispatch map). Adding a scenario
touches one module there, never this file.

Each call is stateless: no conversation history is kept across invocations.
Persistent state lives in data/ files and the Google Sheets, manipulated via the
domain tool modules.

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
import base64
import json
import logging
from typing import Any

import anthropic

from .base import LLMBackend
from .tooldefs import TOOLS, execute_tool

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
# Vision calls swap to Sonnet 4.6 — ~5x cheaper input price ($3/M vs $15/M)
# with no quality loss observed on parcel screenshots in scripts/spike_vision.py.
# Image tokens (~1700 per phone screenshot) don't cache, so Opus pricing on
# them would noticeably bump the bill at the user's expected ~40 screenshots
# per shipment batch.
VISION_MODEL = "claude-sonnet-4-6"
# 16000 is the Anthropic-recommended default for non-streaming. It's a *cap*,
# not a target — short replies still cost only the tokens they actually use.
# Lowballing this (e.g. 1024) truncates batch operations: a single user message
# capturing N items emits N parallel tool_use blocks plus adaptive thinking,
# which easily exceeds 1024. Above ~16k, switch to streaming to avoid SDK
# HTTP timeouts.
MAX_TOKENS = 16000
MAX_LOOP_TURNS = 10  # safety bound — if exceeded, something is wrong


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
        images: list[bytes] | None = None,
        documents: list[bytes] | None = None,
        tools: list[Any] | None = None,
    ) -> str:
        # Per-message routed tool set (router/build_tools picks the active
        # domains' tools). None = the full TOOLS list. Frozen for the whole
        # loop below, so the cached prefix stays stable within a message.
        tools_to_use = tools if tools is not None else TOOLS
        # Conversation history (if any) goes at the front of the messages
        # list so the model sees prior turns as context for the new one.
        # The agent loop appends its own assistant + tool_result blocks on
        # top of this during a single chat() call.
        messages: list[dict[str, Any]] = list(history) if history else []

        if documents:
            # PDFs stay on the main model (Opus): document extraction accuracy
            # on dense legal/insurance text is the high-stakes, once-per-doc
            # step the whole fact-sheet rides on — unlike high-volume receipt
            # photos, which go to the cheaper VISION_MODEL below.
            model = self.model
            content: list[dict[str, Any]] = []
            for pdf in documents:
                content.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(pdf).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": user_message})
            messages.append({"role": "user", "content": content})
        elif images:
            # Per-call model swap: vision goes to Sonnet 4.6 (cheaper, equal
            # quality on parcel screenshots per the spike).
            model = VISION_MODEL
            content = []
            for img in images:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(img).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": user_message})
            messages.append({"role": "user", "content": content})
        else:
            model = self.model
            messages.append({"role": "user", "content": user_message})

        for turn in range(MAX_LOOP_TURNS):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "tools": tools_to_use,
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

            if response.stop_reason == "pause_turn":
                # A server-side tool (web_search) hit its internal iteration cap
                # mid-run. Re-send the assistant content to resume — do NOT add
                # a user message; the server picks up from the trailing
                # server_tool_use block.
                messages.append({"role": "assistant", "content": response.content})
                continue

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
                    result = execute_tool(block.name, block.input)
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
