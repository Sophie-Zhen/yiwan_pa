"""Live smoke test for GeminiBackend — run once after setting GEMINI_API_KEY.

Verifies the three things the offline unit test can't: that the model id is
right, that auth works, and that the function-call agent loop round-trips. It
prints the available model ids first so you can confirm/override GEMINI_MODEL.

  conda run -n assistant python scripts/spike_gemini.py          # text only (safe)
  conda run -n assistant python scripts/spike_gemini.py --tools  # also drive a read-only tool loop

The --tools run loads ONLY the documents domain and asks a read query
(list_documents), so it exercises function_call -> execute_tool ->
function_response without mutating any sheet or file.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()


def main():
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY (or GOOGLE_API_KEY) not set — add it to .env first.")
        return 1

    from google import genai

    from llm.gemini_api import MODEL, GeminiBackend
    from llm.tooldefs import build_tools

    client = genai.Client()
    print(f"configured GEMINI_MODEL = {MODEL}")
    print("available models (look for the 3.1 Pro id; set GEMINI_MODEL if it differs):")
    for m in client.models.list():
        name = getattr(m, "name", "")
        if "gemini" in name:
            print(f"  {name}")

    backend = GeminiBackend()

    print("\n[text round-trip]")
    reply = backend.chat("Reply with exactly the word: PONG", system_prompt="You are a test echo.")
    print(f"  reply: {reply!r}")

    if "--tools" in sys.argv:
        print("\n[tool loop — documents domain, read-only]")
        reply = backend.chat(
            "列出我存档的文档",
            system_prompt="You are Sophie's assistant. Use the document tools.",
            tools=build_tools({"documents"}),
        )
        print(f"  reply: {reply!r}")

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
