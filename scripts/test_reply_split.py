"""Guard test for bot._split_for_telegram.

Pins the fix for the silent-drop bug: a reply over Telegram's 4096-char limit
raised BadRequest from reply_text (outside the handler's try/except) and lost the
whole message. The splitter must keep every chunk under the limit without losing
content. Run: conda run -n assistant python scripts/test_reply_split.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot import TELEGRAM_MAX_CHARS as L
from bot import _split_for_telegram as split


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= cond

    # Empty / whitespace -> a non-empty placeholder (reply_text rejects "").
    expect("empty -> placeholder", split("") == ["(empty reply)"])
    expect("whitespace -> placeholder", split("   \n ") == ["(empty reply)"])

    # Short reply passes through as one chunk, unchanged.
    expect("short -> single chunk", split("hello there") == ["hello there"])

    # Long prose: every chunk under the limit, nothing empty, all words kept.
    prose = " ".join(f"word{i}" for i in range(3000))  # ~> 2x the limit
    chunks = split(prose)
    expect("prose: >1 chunk", len(chunks) > 1)
    expect("prose: every chunk <= limit", all(len(c) <= L for c in chunks))
    expect("prose: no empty chunk", all(c.strip() for c in chunks))
    expect("prose: words preserved", " ".join(chunks).split() == prose.split())

    # A single boundary-less giant word: hard-cut, still under limit, no chars lost.
    giant = "x" * (L * 2 + 137)
    gchunks = split(giant)
    expect("giant: every chunk <= limit", all(len(c) <= L for c in gchunks))
    expect("giant: content preserved", "".join(gchunks) == giant)

    # Paragraph text prefers the \n\n boundary.
    paras = ("A" * 3000) + "\n\n" + ("B" * 3000)
    pchunks = split(paras)
    expect("paragraphs: split on blank line", pchunks == ["A" * 3000, "B" * 3000])
    expect("paragraphs: under limit", all(len(c) <= L for c in pchunks))

    print("PASS" if passed else "SOME CHECKS FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
