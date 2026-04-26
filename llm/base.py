"""Abstract LLM backend interface.

bot.py and other callers depend only on this interface, never on a concrete
backend. This is dependency inversion: swapping shell-out for SDK-based
backends should not require changes outside the `llm/` package.
"""
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @abstractmethod
    def chat(self, user_message: str, system_prompt: str = "") -> str:
        """Send a single user message and return the assistant's reply.

        v0.1: each call is stateless — no conversation history is preserved
        across invocations. Persistent memory lives in the storage layer
        (markdown files), which the LLM reads/writes via tools.

        Args:
            user_message: the raw text the user typed.
            system_prompt: persona / instructions / format spec. If empty,
                the backend's own default behavior applies (typically a
                generic coding-assistant persona for shell-out).
        """
        ...
