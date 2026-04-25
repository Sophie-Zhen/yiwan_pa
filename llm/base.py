"""Abstract LLM backend interface.

bot.py and other callers depend only on this interface, never on a concrete
backend. This is dependency inversion: swapping shell-out for SDK-based
backends should not require changes outside the `llm/` package.
"""
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @abstractmethod
    def chat(self, user_message: str) -> str:
        """Send a single user message and return the assistant's reply.

        v0.1: each call is stateless — no conversation history is preserved
        across invocations. Persistent memory will live in the storage layer
        (markdown files), which the LLM reads/writes via tools.
        """
        ...
