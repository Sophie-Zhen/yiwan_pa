"""Guard test for the Anthropic->Gemini tool-schema translation.

Covers the bug-prone pure logic in llm/gemini_api.py — schema sanitizing and
function-declaration assembly — WITHOUT the google-genai SDK or a network call
(the module imports the SDK lazily, so these helpers run anywhere). The live
agent loop is exercised separately by scripts/spike_gemini.py once a key is set.

Run: conda run -n assistant python scripts/test_gemini_backend.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from llm.gemini_api import _function_declarations, _sanitize_schema
from llm.tooldefs import TOOLS


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    decls = _function_declarations(TOOLS)
    names = {d["name"] for d in decls}

    # web_search (the only `type`-bearing tool) is dropped; every custom tool kept.
    custom = [t for t in TOOLS if not str(t.get("type", "")).startswith("web_search")]
    expect("one declaration per custom tool", len(decls) == len(custom))
    expect("web_search dropped", "web_search" not in names)
    expect("every decl has name/description/parameters",
           all(d["name"] and "description" in d and "parameters" in d for d in decls))

    # Union type ["string","number"] on amend_purchase.value -> anyOf.
    amend = next(d for d in decls if d["name"] == "amend_purchase")
    value = amend["parameters"]["properties"]["value"]
    expect("union type rewritten to anyOf",
           value.get("anyOf") == [{"type": "string"}, {"type": "number"}] and "type" not in value)

    # Unsupported keys stripped; nested object/array structure preserved.
    rp = next(d for d in decls if d["name"] == "record_purchase")
    items = rp["parameters"]["properties"]["items"]
    expect("array `items` preserved", items["type"] == "array" and "items" in items)
    item_props = items["items"]["properties"]
    expect("nested object properties preserved", {"item", "quantity"} <= set(item_props))
    expect("nested `required` preserved", "item" in items["items"]["required"])

    # Sanitizer drops a non-whitelisted key but keeps the supported ones.
    dirty = {"type": "object", "additionalProperties": False,
             "properties": {"x": {"type": "string", "description": "d"}},
             "required": ["x"]}
    clean = _sanitize_schema(dirty)
    expect("additionalProperties stripped", "additionalProperties" not in clean)
    expect("supported keys kept",
           clean["properties"]["x"]["description"] == "d" and clean["required"] == ["x"])

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
