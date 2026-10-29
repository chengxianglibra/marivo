---
name: marivo-skill-maintainer
description: Maintain the packaged marivo-semantic and marivo-analysis workflow skills under marivo/skills. Use for routing, judgment, handoff, and skill-boundary changes.
---

# Marivo Skill Maintainer

The distributed skills live under `marivo/skills/`, included by `pyproject.toml`.
Each directory contains exactly one `SKILL.md`; do not add packaged references
or examples. `.agents/skills/` is ignored maintainer guidance and must never be
read by packaged code or repository tests.

## Choose the owner

- `marivo-semantic` owns datasource and reusable semantic authoring, business
  authority, coherent-slice validation, and analysis-ready handoff.
- `marivo-analysis` owns investigation boundaries, evidence continuity,
  question-scoped judgment, recovery, and closeout.
- `marivo.help(...)` owns static signatures, fields, examples, and navigation;
  `.show()` owns captured facts and state-dependent continuations; structured
  errors own concrete repair guidance.

Change a skill only when the requested change affects an agent decision or
workflow. Keep field-only or runtime-only changes in their owning implementation,
Help, and docs. Do not duplicate API inventories, error tables, host runtime
mechanics, or another owner's rules.

Before adding a rule, identify the observed wrong decision or demonstrated guidance
gap, the intended decision, and the owner that can actually change it. A tool,
environment, or host integration defect needs repair there; add skill guidance only
when it changes a reusable agent judgment. Prefer replacing a misleading rule to
accumulating instructions for individual failures.

## Edit and verify

Read the affected skill, the docs routed by `AGENTS.md`, current live Help, and
`tests/packaging/test_skills.py`. Preserve root-before-child discovery:
`marivo.help("authoring")` for semantic work and `marivo.help("analysis")` for
analysis. Resolve focused targets through that same public coordinator.

Keep the skill self-contained and decision-focused. Reuse existing authorization;
ask only for material unresolved business meaning or missing execution scope.
Keep semantic authoring and analysis distinct, and continue the parent task after
handoff. Avoid fixed call recipes and repeated approval checkpoints.

For a workflow change, choose a representative request and a nearby misuse that
distinguish the intended decision. Check observable outcomes such as finishing a
single-value question, reusing completed data after a parameter repair, or returning
to the parent question after semantic authoring. Use only the cases relevant to the
change; wording edits do not require a new model run or a fixed scenario matrix.

Run `make test TESTS='tests/packaging/test_skills.py'`, resolve affected
live Help targets, and check whitespace and stale references. Add affected Help,
protocol, or domain checks when their contracts change. Do not pin incidental
prose or use package shape alone as evidence of correct agent behavior.

Model-backed profiles, prompts, scorers, trials, and transcripts belong in the
standalone `marivo-agent-evals` project. This repository owns deterministic
surface, protocol, drift, and skill-shape contracts.

## Verify host integration when it is in scope

Record the interpreter, imported Marivo package path and version, and the actual
loaded skill's source path and content hash. Same-name project skills may override
the packaged copy; a package version or requested skill name does not prove which
instructions were used. If the host cannot disclose that identity, report the gap
rather than claiming the packaged skill was evaluated.

The host owns skill selection and provenance disclosure. Adapters own tool parameter
guidance and version-specific API seams. Keep those repairs out of packaged workflow
skills, and distinguish integration failures from agent decisions. Record unnecessary
questions, failed calls, repeated discovery and repeated data reads when they explain
the outcome; tool-call counts alone are not a correctness or usability verdict.
