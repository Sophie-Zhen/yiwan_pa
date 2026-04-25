"""ClaudeCodeBackend — shell out to the local `claude` CLI.

Reuses the user's Claude Code subscription auth (no API key needed). Each
call spawns a fresh, non-interactive `claude -p` process. Trade-off: 1-3s
startup overhead per call, no streaming, no token usage telemetry — but zero
extra cost on top of an existing subscription.
"""
import subprocess

from .base import LLMBackend


class ClaudeCodeBackend(LLMBackend):
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def chat(self, user_message: str) -> str:
        result = subprocess.run(
            ["claude", "-p", user_message],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited with {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout.strip()
