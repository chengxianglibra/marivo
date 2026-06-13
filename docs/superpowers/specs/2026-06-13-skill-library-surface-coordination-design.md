# Skill / Library Surface Coordination Design

**Date:** 2026-06-13

**Status:** Approved design; implementation plan to follow.

**Goal:** Define how the Marivo skills (`marivo-semantic`, `marivo-analysis`)
divide labor with the Marivo Python library's agent-facing surfaces
(`help()` / `describe()` / `.show()` / `__repr__` / structured errors), evict
the contract detail that has leaked into skill files, and add a guard that keeps
it from leaking back.

---

## Background

The agent-facing library surface is now mature: `help()` is a bounded,
family-folded index; `describe(symbol)` carries signature, fields, constraints,
runnable examples, methods, and `next_intents`; every public result type has a
bounded `__repr__` and `.show()`; errors are structured and carry a concrete fix
snippet. The library can teach its own contract from real state at the call
site.

The skills predate much of that surface. They are the first thing an agent loads
for a Marivo task, and they have accumulated content that restates the
library's contract — field tables, frame-flow matrices, error catalogs. That
content is now a second source of truth for facts the library already emits, and
it drifts the moment the code changes underneath it.

This design draws the line between the two surfaces, specifies the cleanup, and
adds a regression guard.

## Principle: contract vs. process

Two surfaces, two distinct responsibilities.

**The library owns the contract.** Anything derivable from real state at the
call site is the library's to emit, never the skill's to transcribe:

- signatures and field lists (`help('<Type>')` renders them from the dataclass);
- valid next steps (`next_intents` on a frame descriptor);
- constraints and runnable examples (`describe(symbol)`);
- error meaning and the concrete fix (structured errors at raise time).

**The skill owns the process.** The cross-object, cross-time judgment that no
single object's help can express, because it spans objects and spans steps:

- which capability to reach for, and in what order;
- when to stop and read versus when to bundle a chain;
- session discipline (one session per task, recovery, deletion);
- closeout and final-report synthesis;
- how to react to a returned status (e.g. `blocked` / `needs_input` /
  `sufficient`) — the decision, not the field that carries it.

### The eviction test

For any line in a skill file, ask:

> **Could the library teach this from real state at the call site?**

- **Yes** → delete it from the skill; repoint to `help('<x>')` or the structured
  error. The library is the single source.
- **No** (it requires cross-object or cross-step judgment) → it is process; it
  stays in the skill.

### Explicit non-goal: zero redundancy

The target is **not** the removal of all redundancy. Orienting prose, decision
trees, and compact priming summaries are valuable: a decision tree up front
saves an agent a sequence of exploratory `help()` calls, and a 30-second
overview primes the whole workflow shape. Those stay.

The target is narrower and sharper: **authoritative contract duplicated in a
place where it can drift.** A field table transcribed from a dataclass is
off-side; a sentence that says "observe returns a MetricFrame; read its
`next_intents`" is fine. Redundancy that orients is kept; redundancy that
re-states a drift-prone contract is evicted.

## The coordination contract

How the two surfaces hand off to each other:

1. **The skill establishes the loop.** It names the entry points and frames the
   `write → run → read → decide` cycle. It tells the agent that the library's
   help/show/error output is the authoritative per-object contract.
2. **The skill routes; it does not copy.** For any contract detail, the skill
   points at `help('<x>')` or the structured error rather than transcribing the
   detail inline. A skill references a contract **by pointer, never by copy.**
3. **Glosses migrate into code.** Where a skill genuinely adds value by glossing
   a contract — for example the per-field "Purpose" prose in the Brief tables —
   that gloss moves into the code (field docstrings consumed by `describe()`),
   so the library becomes the single source for it too. The skill then drops the
   transcription and lets `help()` carry the gloss.

This keeps the skill thin and durable: it changes only when the *process*
changes, not when a field is renamed.

## Cleanup pass

Concrete, file-by-file. Line ranges are current as of this design and should be
re-confirmed at implementation time.

### `marivo-skills/marivo-semantic/references/object-briefs.md`

The clearest violation. It hand-transcribes a field table for every `*Brief`
type. `ms.help('EntityBrief')` already emits the authoritative field list with
types from the dataclass, so the tables are redundant and drift-prone.

- **Delete** the per-kind field tables.
- **Keep** the `status → action` table (`blocked` / `needs_input` /
  `sufficient` → what the agent does) — this is process.
- **Keep** the ladder ordering guidance.
- **Repoint** field detail to `ms.help('<Brief>')`.
- **Migrate** any per-field "Purpose" gloss worth keeping into field docstrings
  on the Brief dataclasses, so `describe()` can emit it.

### `marivo-skills/marivo-analysis/references/cheatsheet.md`

- **Drop** the "Frame Flow / valid next step" column — it duplicates
  `next_intents`, which the SKILL.md itself instructs the agent to read from
  help.
- **Keep** the intent-selection routing matrix (which intent for which question)
  — that is process.

### `marivo-skills/marivo-analysis/SKILL.md`

- **Evict** the "Cross-dataset observe" repair-code list (the
  `component-axis-unreachable` … `nested-derived-unsupported` enumeration) and
  the "Error → example reference" table. Both transcribe error contract that the
  structured errors already teach at raise time. Replace with a single pointer:
  on error, read the structured output (it carries `code`, `candidates`,
  `repair`) and apply the fix.
- **Keep** split-vs-bundle guidance, session discipline, the decision tree,
  closeout, and the final-report routing — all process.

### Remaining references

Audit the rest of `marivo-skills/**` against the eviction test and record any
further transcriptions found (e.g. `typed-facts.md`, `pitfalls.md`). Trim or
repoint as the test dictates; note in the implementation plan anything kept and
why.

## The guard

A test prevents contract from re-accumulating in the skills, in the same
snapshot-with-allowlist spirit as the existing `__all__` and fold-partition
guards.

**Location:** `tests/test_skill_surface_discipline.py`.

**Checks (best-effort heuristics, not a proof):**

- No markdown field-table block in a skill restates the fields of a known public
  dataclass (cross-reference table headers / first-column tokens against public
  type field names).
- No transcribed `*Error` / error-code catalog in skill markdown (flag tables or
  lists whose entries match public exception names beyond a small threshold).
- Public Brief and result-type fields carry docstrings, so the library can emit
  the gloss that the skills used to carry.

**Allowlist:** a pinned set of deliberate exceptions, consistent with the
existing snapshot tests. Any intentional contract mention in a skill is a
reviewed entry in the allowlist, not a silent pass.

The heuristics are approximate by design: false positives are resolved by adding
a reviewed allowlist entry, never by weakening the check. The guard's value is
that re-introducing a field table or error catalog forces a conscious decision.

## Verification & rollout

- `make examples-check` stays green — skill example scripts still run.
- The new discipline test is green with its initial allowlist.
- `make test`, `make typecheck`, `make lint` clean for touched modules.
- For each skill, capture a before/after of what an agent loads (line counts and
  the shape of the top-level read), to confirm the skill got thinner and the
  contract detail now resolves through `help()` / errors.
- English-only in all code, tests, and skill artifacts. Repository entrypoints
  only. Each commit carries the co-author trailer.

## Scope boundary

This spec is architecture plus the concrete cleanup edits. The task-by-task
implementation sequence (red/green tests, commit boundaries) is produced by the
`writing-plans` step that follows this design.
