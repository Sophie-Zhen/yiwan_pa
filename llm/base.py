"""Abstract LLM backend interface.

bot.py and other callers depend only on this interface, never on a concrete
backend. This is dependency inversion: swapping shell-out for SDK-based
backends should not require changes outside the `llm/` package.
"""
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @abstractmethod
    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
        images: list[bytes] | None = None,
        documents: list[bytes] | None = None,
        tools: list | None = None,
        cache: bool = True,
    ) -> str:
        """Send a single user message and return the assistant's reply.

        Args:
            user_message: the raw text the user typed.
            system_prompt: persona / instructions / format spec. If empty,
                the backend's own default behavior applies (typically a
                generic coding-assistant persona for shell-out).
            history: optional conversation history as a list of
                {"role": "user"|"assistant", "content": str} dicts, oldest
                first. Backends that support multi-turn use this as the
                messages prefix; backends without multi-turn support may
                ignore it. None or empty means stateless (no prior context).
            images: optional list of image bytes (JPEG/PNG) to send alongside
                the user message. Backends without vision support log a
                warning and ignore. Image bytes are NOT persisted in history
                — only the text user_message survives across turns.
            documents: optional list of PDF bytes to send alongside the user
                message (e.g. an insurance policy to extract). Backends without
                document support log a warning and ignore. Like images, not
                persisted in history.
            tools: optional per-message tool schema list (the router/build_tools
                pick only the active domains' tools to shrink the prefix). None
                means the backend uses its full default tool set. Backends that
                manage their own tools (e.g. the shell-out CLI) ignore it.
            cache: whether to mark prompt-cache breakpoints. Default True. Pass
                False for isolated one-off calls (e.g. cron digests fired hours
                apart): their cache write is never read back before the 5-min
                TTL expires, so caching only adds the 1.25x write premium over
                1.0x uncached. Backends without prompt caching ignore it.

        See docs/decisions/0001-conversation-history.md for the storage
        layer that produces `history`.
        """
        ...
