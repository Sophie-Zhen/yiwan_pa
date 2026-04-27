"""LLM backend selection.

The LLM_BACKEND env var picks an implementation; bot.py only knows the
abstract interface defined in `base.py`. To add a new backend:

1. Subclass LLMBackend in a new module under this package.
2. Register it in `get_backend()` below.
3. Set LLM_BACKEND=<name> in .env to switch.

Available backends:
- claude_code (default): shells out to the local `claude` CLI; reuses the
  user's Claude Code subscription auth. No extra cost on top of the sub.
- anthropic: uses the anthropic Python SDK with a self-written agent loop;
  requires ANTHROPIC_API_KEY. Pay-per-token but more controllable.
"""
import os

from .base import LLMBackend
from .claude_code import ClaudeCodeBackend


def get_backend() -> LLMBackend:
    name = os.getenv("LLM_BACKEND", "claude_code")
    if name == "claude_code":
        return ClaudeCodeBackend()
    if name == "anthropic":
        # Imported lazily so users on the claude_code backend don't need
        # the anthropic package installed at all.
        from .anthropic_api import AnthropicBackend

        return AnthropicBackend()
    raise ValueError(f"Unknown LLM_BACKEND: {name!r}")
