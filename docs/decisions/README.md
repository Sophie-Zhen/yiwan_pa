# Engineering Decisions

This directory holds **design notes / decision records** for non-trivial choices made on this project. Each file captures the problem, the options considered, the decision, and the rationale.

## Why we keep these

- **Future me** — six months on, "why did I pick option X?" — reading this is faster than git blame.
- **Portfolio / interview** — these documents show engineering thinking, not just code. They're a direct answer to "tell me about a design decision you made."
- **Onboarding** — anyone reading the repo learns the *why*, not just the *what*.
- **Cost of bad ideas** — once an option is documented as rejected with reasons, future-me (or contributors) won't burn time re-evaluating it.

## Format

Each decision is a numbered markdown file: `NNNN-kebab-case-title.md`. Numbers grow monotonically.

Template:

```markdown
# NNNN. <Title>

- **Status**: Proposed | Decided | Superseded by NNNN | Deprecated
- **Date**: YYYY-MM-DD
- **Tags**: #area #version

## Problem
<One paragraph — what are we solving and why now>

## Context
<What's already in place that constrains us>

## Options considered
<Table summarising all options; deeper dive on the top candidates>

## Decision
<What we chose. If Proposed, note "pending" with link to whoever needs to weigh in.>

## Rationale
<Why this option beat the others — connect back to Context>

## Consequences
- What becomes easier
- What becomes harder
- What to revisit later

## Open questions
- Anything still undecided

## References
- Related TODOs items, prior decisions, external links
```

## When to write one

A new decision doc is worth it when:
- The choice is **non-trivial** — not "which variable name", but "in-memory dict vs disk-backed".
- Multiple **viable options exist** — there's a real trade-off, not just one obvious answer.
- The choice **shapes future work** — others (or future-me) will inherit the consequences.

Skip it for:
- Routine refactors with a clear winner.
- Pure bug fixes.
- Style / formatting choices.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| 0001 | [Conversation history mechanism](0001-conversation-history.md) | Decided | 2026-04-28 |
| 0002 | [Pluggable LLM backend abstraction](0002-pluggable-llm-backend.md) | Decided | 2026-04-26 |
| 0003 | [Self-written agent loop in AnthropicBackend](0003-self-written-agent-loop.md) | Decided | 2026-04-27 |
| 0006 | [Per-item scheduler state schema](0006-scheduler-state-schema.md) | Decided | 2026-05-20 |
