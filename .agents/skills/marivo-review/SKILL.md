---
name: marivo-review
description: Review Marivo diffs, merge requests, fixes, or design specs against owning contracts and observable behavior. Use for review or merge-readiness requests; publish findings only when requested.
---

# Marivo Review

Produce severity-ordered, evidence-backed findings for the requested revision.
Keep review read-only unless fixes or publication are authorized. Treat green
tests, descriptions, and prior approvals as evidence to assess, not a verdict.

## Bind the review

Read `AGENTS.md`, inspect worktree status, and establish the exact base, head, and
scope. Distinguish staged, unstaged, committed, and untracked changes. For a
GitLab MR, read its metadata, diff, relevant discussions, pipeline, and linked
issue contract through `glab`; fetch refs without overwriting user work.

Use `git show`, a temporary archive, or a detached worktree when isolation is
needed. Confirm tests import the reviewed snapshot. Record its SHA and refresh
it before closeout if the target moved.

## Review the owning contract

Use the documentation routing in `AGENTS.md`, the focused
`marivo.help("datasource.<target>")`, `marivo.help("semantic.<target>")`, or
`marivo.help("analysis.<target>")`, and directly affected consumers. Trace each
changed contract from construction through validation, execution, persistence,
and public rendering as applicable.

- Check semantic identity, ownership, grain, units, additivity, temporal scope,
  null/empty/duplicate inputs, partial coverage, and comparison alignment where
  they affect the result. Do not turn computation into business judgment.
- Check immutability, cross-session ownership, lineage, recovery, and structured
  error repairs. Verify datasource behavior on each affected backend; readiness,
  preview, source health, and business authority prove different things.
- Treat public API, Help, result/error guidance, skills, CLI, and latest EN/ZH
  docs as the disclosure contract in `AGENTS.md`. Registry ownership and
  independently resolvable guidance matter beyond reflected signatures.
- Check failure paths and xdist isolation. If a failure may predate the change,
  reproduce it against the exact base before attributing it to the diff.

Load specialized detail only when needed:

- [proposal-review.md](references/proposal-review.md) for semantic/analysis
  architecture, algebra, temporal policy, or public-constructor proposals.
- [help-review.md](references/help-review.md) for Help roots, focused leaves,
  registries, disclosure budgets, or resolver equivalence.

Committed specs establish intent. Distinguish implemented contracts from future
proposals and historical text; missing implementation is not a defect in a
proposal unless it claims the behavior is live. Re-review fixed findings against
current code, then inspect regressions and related seams.

## Validate and report

Follow proportional checks in `AGENTS.md`. Changes to packaged skills use
`tests/packaging/test_skills.py` and affected live Help resolution. Use
`make docs-api` for API documentation, site content verification/build for site
changes, and `make pypi-build pypi-check` for packaging. Shared runtime or public
behavior needs broad checks; a focused adversarial probe should establish an
edge-case claim that the existing tests do not settle.

Report only actionable findings with a severity, tight file/line location,
failing scenario, impact, direct evidence, and bounded correction direction:

- P0: exploitable security, unrecoverable loss, or widespread corruption.
- P1: wrong results, state corruption, broken current public contract, or another
  merge blocker.
- P2: material edge-case, repair, documentation, or coverage gap.
- P3: low-risk actionable improvement; omit speculative cleanup and style taste.

Lead with findings, then state checked scope and material gaps. Say `No findings`
when appropriate without implying untested behavior passed. Distinguish static
validation, readiness, and observed runtime proof.

## Publish only within authorization

For an explicitly requested GitLab review update, post one unresolved discussion
per finding. Prefer positional discussions; if rejected, use a normal discussion
with an explicit file/line anchor. Send multiline bodies through structured
arguments or a body file. Read back each note and verify it is resolvable and
unresolved. Do not duplicate a note after an uncertain write: check remote state
first. Approval, resolution, merge, and issue transitions require their own
user-authorized scope.
