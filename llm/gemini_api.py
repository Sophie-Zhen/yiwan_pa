"""GeminiBackend — google-genai SDK with a self-written agent loop.

A cheaper-provider mirror of AnthropicBackend (Gemini 3.1 Pro: ~$2/$12 per 1M
vs Opus $5/$25). It reuses the SAME tool dispatch (execute_tool) and the SAME
routed prompt/tool prefix that bot.py already builds; only the wire format
differs — Anthropic tool schemas are translated to Gemini function
declarations, and the loop speaks Gemini's function_call / function_response
parts instead of tool_use / tool_result blocks.

Switch on with LLM_BACKEND=gemini (needs GEMINI_API_KEY). The google-genai
package is imported lazily so anthropic / claude_code users don't need it
installed, and so this module imports (for its pure translation helpers) even
where the SDK is absent.

v1 scope / known gaps:
- No prompt caching. Gemini's explicit context cache bills hourly storage
  (~$4.50/hr), which a sparse personal bot can't amortize, so v1 runs
  uncached. The `cache` flag is accepted for interface parity and ignored.
- No web_search. Anthropic's web_search server tool has no drop-in equivalent
  here; it is dropped from the Gemini tool list. Use LLM_BACKEND=anthropic for
  a message that needs a live web lookup.
- One model for everything. Gemini is natively multimodal, so images and PDFs
  go to the same MODEL — no Opus->Sonnet vision split like the Anthropic path.
"""
import logging
import os
from typing import Any

from .base import LLMBackend
from .tooldefs import TOOLS, execute_tool

logger = logging.getLogger(__name__)

# Best-guess model code per Gemini's naming convention (preview models are
# `gemini-<ver>-pro-preview`). CONFIRM the exact id once a key is set —
# `genai.Client().models.list()` and look for the 3.1 Pro entry — and override
# via GEMINI_MODEL in .env without editing source if it differs.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
MAX_OUTPUT_TOKENS = 16000
MAX_LOOP_TURNS = 10  # safety bound — matches the Anthropic backend

# JSON-schema keys Gemini's function-declaration schema accepts. Anything else
# in an Anthropic input_schema (none today, but e.g. $schema / additionalProperties)
# is stripped during translation so the SDK can't reject the whole tool list.
_SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum"}


def _sanitize_schema(node: Any) -> Any:
    """Translate one Anthropic input_schema (JSON Schema) node into the
    OpenAPI-subset schema Gemini accepts: keep only supported keys, and rewrite
    a union `type` (e.g. ["string", "number"]) as `anyOf`, which the
    single-typed Gemini schema needs instead of a list."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k not in _SCHEMA_KEYS:
            continue
        if k == "type" and isinstance(v, list):
            out["anyOf"] = [{"type": t} for t in v]
        elif k == "properties" and isinstance(v, dict):
            out["properties"] = {pk: _sanitize_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = _sanitize_schema(v)
        else:
            out[k] = v
    return out


def _function_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool schemas -> Gemini function-declaration dicts. Skips the
    web_search server tool: it has no local handler and no Gemini equivalent in
    v1 (identified by its `type` field; custom tools have no `type`)."""
    decls: list[dict[str, Any]] = []
    for t in tools:
        if str(t.get("type", "")).startswith("web_search"):
            continue
        decls.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": _sanitize_schema(
                    t.get("input_schema", {"type": "object", "properties": {}})
                ),
            }
        )
    return decls


def _make_declaration(types: Any, decl: dict[str, Any]) -> Any:
    """Build one types.FunctionDeclaration, tolerating SDK-version differences in
    how the parameter schema is passed (`parameters` as a Schema-coercible dict
    on older builds, `parameters_json_schema` for raw JSON Schema on newer)."""
    try:
        return types.FunctionDeclaration(
            name=decl["name"],
            description=decl["description"],
            parameters=decl["parameters"],
        )
    except Exception:
        return types.FunctionDeclaration(
            name=decl["name"],
            description=decl["description"],
            parameters_json_schema=decl["parameters"],
        )


class GeminiBackend(LLMBackend):
    def __init__(self, model: str = MODEL) -> None:
        from google import genai  # lazy: optional dep, only needed for gemini

        self.client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY from env
        self.model = model

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
        images: list[bytes] | None = None,
        documents: list[bytes] | None = None,
        tools: list[Any] | None = None,
        cache: bool = True,
    ) -> str:
        # v1 runs uncached (see module docstring); accept the flag for parity.
        del cache
        from google.genai import types

        tools_to_use = tools if tools is not None else TOOLS
        decls = _function_declarations(tools_to_use)
        gem_tools = (
            [types.Tool(function_declarations=[_make_declaration(types, d) for d in decls])]
            if decls
            else None
        )

        contents: list[Any] = []
        # History first (oldest -> newest). Gemini uses role "model" for the
        # assistant; map it from the interface's "assistant".
        for h in history or []:
            role = "model" if h["role"] == "assistant" else "user"
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=h["content"])])
            )

        # Current user turn: images / PDFs as inline_data parts, then the text.
        parts: list[Any] = []
        for img in images or []:
            parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
        for pdf in documents or []:
            parts.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
        parts.append(types.Part.from_text(text=user_message))
        contents.append(types.Content(role="user", parts=parts))

        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            tools=gem_tools,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            # Disable the SDK's built-in auto function-calling: we run our own
            # loop (same as the Anthropic backend) so dispatch stays in
            # execute_tool and is identical across providers.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        for turn in range(MAX_LOOP_TURNS):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                logger.exception("Gemini API error on turn %d", turn)
                raise RuntimeError(f"Gemini API error: {exc}") from exc

            usage = getattr(response, "usage_metadata", None)
            logger.info(
                "turn=%d in=%s out=%s cached=%s",
                turn,
                getattr(usage, "prompt_token_count", None),
                getattr(usage, "candidates_token_count", None),
                getattr(usage, "cached_content_token_count", None),
            )

            calls = response.function_calls or []
            if not calls:
                # No tool call => final answer. .text concatenates text parts;
                # may be empty (e.g. a safety stop) — return what we have.
                return (response.text or "").strip()

            # Append the model's function-call turn verbatim (preserves the
            # call parts as context), then dispatch and answer each call.
            contents.append(response.candidates[0].content)
            resp_parts: list[Any] = []
            for call in calls:
                args = dict(call.args or {})
                logger.info("function_call: %s(%s)", call.name, args)
                try:
                    result = execute_tool(call.name, args)
                except Exception as exc:
                    logger.exception("tool %s failed", call.name)
                    result = {"error": str(exc)}
                # function_response.response must be a JSON object; wrap so a
                # list/scalar tool result is still a valid struct.
                resp_parts.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": result}
                    )
                )
            contents.append(types.Content(role="user", parts=resp_parts))

        raise RuntimeError(
            f"agent loop exceeded MAX_LOOP_TURNS={MAX_LOOP_TURNS}; "
            "the model is likely stuck in a tool-use cycle."
        )
