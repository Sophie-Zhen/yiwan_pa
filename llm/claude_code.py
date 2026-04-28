"""ClaudeCodeBackend — shell out to the local `claude` CLI.

Reuses the user's Claude Code subscription auth (no API key needed). Each
call spawns a fresh, non-interactive `claude -p` process. Trade-off: 1-3s
startup overhead per call, no streaming, no token usage telemetry — but zero
extra cost on top of an existing subscription.
"""
import pathlib
import subprocess

from .base import LLMBackend

# Project root = parent of the `llm/` package directory. Subprocesses run
# with this as cwd so file paths in the system prompt (e.g. data/inbox.md)
# resolve consistently regardless of where the user launched bot.py from.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Whitelist of tools claude is allowed to use when invoked from the bot.
# Restricts blast radius: no Bash, no web fetch, no random exploration.
_ALLOWED_TOOLS = ["Read", "Write", "Edit"]


class ClaudeCodeBackend(LLMBackend):
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> str:
        # `history` is accepted to satisfy the LLMBackend interface but
        # intentionally ignored in v0.1.1 — see
        # docs/decisions/0001-conversation-history.md. CC's own exploratory
        # tool use partially compensates by reading inbox/archive on demand.
        # If we want true multi-turn here later, the path is `claude -p
        # --resume <session-id>` keyed on chat_id.
        del history

        cmd = [
            "claude",
            "-p",
            user_message,
            "--allowed-tools",
            " ".join(_ALLOWED_TOOLS),
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited with {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout.strip()
