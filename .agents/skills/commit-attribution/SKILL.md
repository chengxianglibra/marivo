---
name: commit-attribution
description: Use when drafting, editing, or checking commit messages in Marivo that need AI co-author attribution, including commits made with Codex, Claude Code, or other agent-assisted tools.
---

# Commit Attribution

Use this skill when a user asks to commit changes, write a commit message, add co-author
attribution, or verify AI attribution for a commit.

## Required attribution

When AI assistance contributes to a commit, add one attribution line at the end of the commit
message:

```text
Co-Authored-By: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2] ...
```

Field rules:

- `AGENT_NAME`: the actual agent or client that assisted, such as `Codex CLI`, `Codex`, or `Claude Code`.
- `MODEL_VERSION`: the model identifier from the **runtime environment** — specifically the value reported by `"You are powered by the model <ID>"` in the system context. Do NOT substitute a Claude model-family reference (e.g. `claude-sonnet-4-6`) when the runtime model is different (e.g. `glm-5.1`). Use the actual running model ID verbatim; if it is a non-Claude model, use its real name (e.g. `glm-5.1`, `gpt-5.4`). If the exact version is truly unknown, use only a confirmed product/model name and do not invent precision.
- `[TOOL]`: substantive tool categories used for the contribution, such as `[Edit]`, `[Bash]`, `[Search]`, `[Review]`, or `[Browser]`.

## Placement and format

- Put the attribution as the final non-empty line of the commit message.
- Use exactly `Co-Authored-By` with that capitalization and hyphenation.
- Use one line per assisting agent if multiple agents materially contributed.
- Do not add the traditional GitHub `Co-authored-by: Name <email>` trailer unless the user explicitly asks for it.

## Examples

```text
feat: add source mapping validation

Validate source-to-engine mappings before runtime compilation.

Co-Authored-By: Codex CLI:gpt-5.4 [Edit] [Bash]
```

```text
fix: tighten semantic binding checks

Reject bindings that reference execution-side locators.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash] [Review]
```
