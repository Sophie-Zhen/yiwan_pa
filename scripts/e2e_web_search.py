"""End-to-end test for the web_search server tool.

Two parts:
  1. Direct API call with just the web_search tool — asserts a
     web_search_tool_result block comes back, proving the tool-type string is
     valid and Anthropic actually ran a search.
  2. Full backend.chat path with the real system prompt + tool list — proves
     the tool is wired in and the loop (incl. pause_turn) returns a real answer.

Hits the live web and the real API. Run:
    conda run -n assistant python scripts/e2e_web_search.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
from dotenv import load_dotenv

from llm import get_backend
from llm.anthropic_api import MODEL
from prompts import render_personal_assistant

load_dotenv()

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def part1_direct() -> None:
    print("\n=== Part 1: direct API call with web_search ===")
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[WEB_SEARCH_TOOL],
        messages=[{
            "role": "user",
            "content": "Search the web: what is the main public phone number for Dublin Airport in Ireland? Give just the number.",
        }],
    )
    types = [b.type for b in resp.content]
    print(f"stop_reason={resp.stop_reason}  block types={types}")
    text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    print(f"answer: {text}")
    assert any(t == "web_search_tool_result" for t in types), \
        f"no web_search_tool_result block — search did not run (got {types})"
    print("ok: Anthropic ran a web search and returned results")


def part2_backend() -> None:
    print("\n=== Part 2: full backend.chat path ===")
    backend = get_backend()
    system = render_personal_assistant(os.getenv("USER_NAME", "Sophie"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = f"[Now: {now}]\n帮我查一下都柏林机场（Dublin Airport）的客服电话，只要号码就行"
    reply = backend.chat(msg, system, None, None)
    print(f"reply: {reply}")
    assert reply and "[error]" not in reply and "[refusal]" not in reply, reply
    assert any(ch.isdigit() for ch in reply), "expected a phone number with digits"
    print("ok: backend returned a real answer with digits")


def main() -> None:
    part1_direct()
    part2_backend()
    print("\n[web_search e2e passed]")


if __name__ == "__main__":
    main()
