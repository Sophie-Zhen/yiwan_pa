"""Guard test for the /<domain> slash-command tagging in bot.py.

Checks the pure, bug-prone parts WITHOUT Telegram or a network call: that every
command maps to a real domain and genuinely shrinks the prefix, that names don't
collide with the existing commands, and that the command-body parser survives the
edge cases (no body, @bot suffix, multi-line body).

Run: conda run -n assistant python scripts/test_domain_commands.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import bot
from llm.tooldefs import TOOLS, build_tools
from prompts import ALL_DOMAINS, render_personal_assistant

# Commands already registered in main() that a domain tag must not shadow.
_RESERVED = {"id", "digest", "todos", "active"}


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    full_tool_count = len(TOOLS)

    for cmd, (domains, desc) in bot._DOMAIN_COMMANDS.items():
        unknown = domains - set(ALL_DOMAINS)
        expect(f"/{cmd}: domains are real ({sorted(domains)})", not unknown)
        expect(f"/{cmd}: has a menu description", bool(desc))
        expect(f"/{cmd}: name not reserved", cmd not in _RESERVED)
        sysp = render_personal_assistant("Sophie", domains)
        tools = build_tools(domains)
        expect(f"/{cmd}: non-empty system prompt", bool(sysp))
        expect(f"/{cmd}: tool list shrinks ({len(tools)} < {full_tool_count})",
               len(tools) < full_tool_count)

    # Body parser: token stripped, first-whitespace-only split, edge cases.
    expect("body: simple", bot._command_body("/spend 咖啡 8.99") == "咖啡 8.99")
    expect("body: empty -> ''", bot._command_body("/spend") == "")
    expect("body: @bot suffix", bot._command_body("/spend@MyBot 牛奶 2") == "牛奶 2")
    expect("body: multi-line survives", bot._command_body("/spend a\nb") == "a\nb")
    expect("body: collapses leading run", bot._command_body("/spend   x") == "x")

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
