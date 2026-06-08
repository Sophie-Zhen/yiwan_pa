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

        See docs/decisions/0001-conversation-history.md for the storage
        layer that produces `history`.
        """
        ...
