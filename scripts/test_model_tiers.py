"""Guard test for per-task model tiers in AnthropicBackend.

Asserts the model picked by input type, via a capturing fake client (no network):
everyday text and image screenshots run on the Sonnet tier, while PDF extraction
keeps the strong Opus model. A mis-wire here would silently downgrade the
high-stakes document path or upgrade everyday text — both worth catching.

Run: conda run -n assistant python scripts/test_model_tiers.py
"""
import os
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-never-used")

from llm.anthropic_api import DOCUMENT_MODEL, MODEL, VISION_MODEL, AnthropicBackend


class _FakeMessages:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        usage = types.SimpleNamespace(
            input_tokens=0, output_tokens=0,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        text_block = types.SimpleNamespace(type="text", text="ok")
        return types.SimpleNamespace(
            stop_reason="end_turn", content=[text_block], usage=usage,
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    # The intended assignment.
    expect("default text model is Sonnet 4.6", MODEL == "claude-sonnet-4-6")
    expect("vision model is Sonnet 4.6", VISION_MODEL == "claude-sonnet-4-6")
    expect("document model is Opus 4.8", DOCUMENT_MODEL == "claude-opus-4-8")

    backend = AnthropicBackend()
    backend.client = _FakeClient()

    backend.chat("记一下明天交房租", "SYS")
    expect("text  -> default (Sonnet)",
           backend.client.messages.last_kwargs["model"] == MODEL)

    backend.chat("看图", "SYS", images=[b"fake-image-bytes"])
    expect("image -> vision (Sonnet)",
           backend.client.messages.last_kwargs["model"] == VISION_MODEL)

    backend.chat("读保单", "SYS", documents=[b"%PDF-fake"])
    expect("PDF   -> document (strong Opus)",
           backend.client.messages.last_kwargs["model"] == DOCUMENT_MODEL)

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
