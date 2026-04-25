"""LLM backend selection.

The LLM_BACKEND env var picks an implementation; bot.py only knows the
abstract interface defined in `base.py`. To add a new backend:

1. Subclass LLMBackend in a new module under this package.
2. Register it in `get_backend()` below.
3. Set LLM_BACKEND=<name> in .env to switch.
"""
import os

from .base import LLMBackend
from .claude_code import ClaudeCodeBackend


def get_backend() -> LLMBackend:
    name = os.getenv("LLM_BACKEND", "claude_code")
    if name == "claude_code":
        return ClaudeCodeBackend()
    raise ValueError(f"Unknown LLM_BACKEND: {name!r}")
