# Analysis Replay Script (replay.py) — Design

Date: 2026-06-03

Status: draft design proposal, pending written-spec review.

This document specifies how an agent, after completing a Marivo analysis task,
produces a single self-contained Python script (`replay.py`) that re-runs the
analysis against a live datasource. The script is shareable: a recipient with
their own datasource credentials can execute it to reproduce the analysis.

It refines `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md`,
which already requires `replay.py` as a mandatory attachment in the
`analysis_report` package and records its `generated_by` / `input_mode` /
`validation` fields in the manifest. That design states *that* `replay.py`
exists; this document specifies *how the agent produces a faithful one* and
*what statically checking it means*.

## 1. Motivation

A finished Marivo analysis today leaves behind persisted session state (jobs,
frames, evidence) and, separately, an HTML report. Neither is a runnable recipe:
a colleague cannot take "the analysis" and re-run it on demand. We want a
shareable, executable artifact that reproduces the analysis steps and their
numbers against current data — the reproducibility leg of the report package.

**Goal:** after a non-trivial analysis, emit a single `replay.py` that another
person can run (with their own credentials) to re-execute the same logical
analysis and obtain the same numbers when the underlying data is unchanged.

**Non-goal (this revision):** proving the script reproduced the report. v1 ships
a statically checked recipe, not an executed-and-reconciled proof (see §11).

## 2. Relationship to existing designs

- **Publishing design** (`2026-06-01-semantic-report-publishing-design.md`):
  owns the `analysis_report` package layout, manifest, publish pipeline, and the
  requirement that `replay.py` + an embedded semantic copy are present. This
  spec fills the gap it left open: the assembly procedure and the static-check
  definition.
- **Executed-SQL audit trail** (`docs/specs/analysis/2026-06-03-executed-sql-audit-trail-design.md`)
  and **evidence surface** (`docs/specs/analysis/python-analysis-design.md`):
  provide the persisted `jobs[]` DAG (intent + params + frame refs, now also
  `queries[]`) and deterministic `artifact_id`s. v1 does not consume these for
  generation, but they are the substrate for the optional hardening in §11.

## 3. Decisions

| Decision | Choice |
|---|---|
| Replay data model | `live_datasource`: re-run intents against the recipient's live datasource. Credentials are never packaged; they come from the caller's environment. |
| Time / relative params | Pinned absolute windows. `timescope` is serialized as the resolved absolute interval; relative expressions survive only as comments. |
| Replay scope | Data / number layer only: the intent DAG and its frames/numbers. Narrative stays in `index.html`; the evidence chain stays in `evidence/`. |
| Generation | Agent-authored (`generated_by: "skill"`). The agent assembles `replay.py` from the step-scripts it actually ran during the analysis. |
| Stitch model | In-memory, single process: frames flow through Python variables; no reliance on the session store / refs for cross-step handoff. |
| Verification gate | `static_checked` only. No execution, no number reconciliation, in v1. The package must label the script as not reproduced. |
| Script capture | Analysis steps must run as `.py` scripts written to a session-scoped directory, so they can be captured for assembly. |

## 4. Replay semantics

Running `replay.py` re-issues the analysis against the live datasource and
recomputes the numbers. Because windows are pinned to absolute intervals, the
script asks the *same logical question* every time it runs:

- Data unchanged since the report → numbers match the report.
- Data backfilled / corrected → numbers differ, which is itself the audit
  signal ("the same question now yields a different answer").

The script reproduces frames and their key numbers only. It does not re-derive
prose conclusions or evidence objects (findings, propositions, assessments).
Those are reproduced by the report package as a whole (`index.html`, `evidence/`,
`flow.json`), not by `replay.py`.

## 5. Working-mode requirement (capture precondition)

The `marivo-analysis` skill already prescribes a script-based workflow: one
session per task, a sequence of `.py` step-scripts that reuse the same session,
splitting a new script only when the next intent depends on output the agent
must read first (`marivo-skills/marivo-analysis/SKILL.md`, "When to split
scripts", "Session", "Standard workflow"). This design hardens that into a
capture precondition:

- Analysis steps that produce frames **must** run as `.py` scripts. Using
  `python -c '...'` one-liners or an interactive REPL for frame-producing steps
  is disallowed, because such steps leave no assemblable source.
- The agent writes step-scripts to a session-scoped directory:

  ```text
  <project_root>/.marivo/analysis/sessions/<session_id>/scripts/NN_<intent>.py
  ```

  `NN` is a zero-padded run order. This directory is project-local analysis
  state (consistent with the existing `.marivo/analysis/sessions/<id>/` home for
  jobs and frames) and is the input the close-out step reads.

Retries and exploratory dead-ends are allowed to accumulate here; curation
(§6.1) selects the canonical subset.

## 6. Close-out assembly procedure (agent-owned, in the skill)

At analysis close-out, the agent assembles `replay.py` from its step-scripts in
four steps. The Marivo library does not author the script; it only provides the
deterministic static check (§8).

### 6.1 Curate

Select the ordered subset of step-scripts that constitute the final analysis
path. Drop retries (superseded fixes of an earlier error) and exploratory
branches that did not feed the conclusion. The agent that wrote the scripts is
the curator; it has the context to know which steps mattered.

### 6.2 Stitch (in-memory, single process)

Merge the curated step-scripts into one linear script:

- **One bootstrap, not N.** Replace the repeated
  `ms.find_project() -> project.load() -> mv.session.get_or_create(name=...)`
  preambles with a single bootstrap that (a) loads the embedded semantic tree
  under `semantic-embed/` rather than discovering the original project's
  `.marivo/semantic`, (b) creates a fresh local session, and (c) wires the
  backend from the caller's environment.
- **Variable handoff.** Replace cross-script `load_frame(ref)` reloads and any
  re-`get_or_create` with direct Python variable references: a frame produced by
  one step is held in a variable and passed to the next intent. The script must
  not depend on the original session's persisted frames/refs.

### 6.3 Pin

- `timescope` is written as the resolved absolute interval
  (`{"start": "...", "end": "..."}`), never a relative expression.
- "Read-then-decided" handoffs — where the agent inspected a `summary()` and
  chose a value (a `discover` rank, a `decompose` segment to drill) — are frozen
  as explicit literals, with a comment recording the decision and its basis.

### 6.4 Preamble / tail

- **Preamble:** fail fast on missing credentials. For each datasource env
  reference the script needs, raise a clear error naming the missing variable
  (e.g. `missing required datasource env var: WAREHOUSE_DSN_ENV`). No secret
  values are embedded.
- **Tail:** `print` the key frames' `summary()` / numbers so a runner sees the
  reproduced output.

## 7. `replay.py` structure (illustrative)

The exact loader/backend-wiring calls are bound during planning against the
current loader and session APIs; the shape below is illustrative.

```python
# replay.py — live re-run, pinned windows.
# validation: static_checked — this is a reproducibility recipe, NOT a proof of reproduction.
import os
import marivo.analysis as mv
import marivo.semantic as ms

# --- bootstrap: embedded semantic copy + fresh session + backend from caller env ---
for var in ("WAREHOUSE_DSN_ENV",):  # datasource env references; secrets come from env
    if not os.environ.get(var):
        raise SystemExit(f"missing required datasource env var: {var}")
project = ms.load_embedded_project("semantic-embed")  # illustrative; loads embedded tree
project.load()
session = mv.session.get_or_create(name="replay", backend_factory=...)  # wired from env

# --- step 1: revenue, current vs baseline (pinned absolute windows) ---
cur = session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2026-05-01", "end": "2026-05-08"}, grain="day",
    dimensions=[mv.DimensionRef("region")],
)
base = session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2026-04-24", "end": "2026-05-01"}, grain="day",
    dimensions=[mv.DimensionRef("region")],
)
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))

# --- step 2: attribute the change by region ---
attribution = session.decompose(delta, axis=mv.DimensionRef("region"))

# --- step 3: drill the top driver ---
# frozen decision: region == "APAC" (rank-1 driver from step-2 attribution ranking)
apac = session.observe(
    mv.MetricRef("sales.revenue"),
    timescope={"start": "2026-05-01", "end": "2026-05-08"}, grain="day",
    where=[mv.SlicePredicate(...)],  # region == "APAC"
)
anomalies = session.discover.point_anomalies(apac, threshold=1.0)

print(delta.summary())
print(attribution.summary())
print(anomalies.summary())
```

## 8. Static-check gate

A deterministic library helper validates `replay.py` before publishing. It does
not execute the script. Checks:

- The script parses and its imports resolve.
- Every intent call uses a known intent name.
- Every `MetricRef` / `DimensionRef` id resolves against the embedded
  `semantic-embed/` model (e.g. via the loaded project's metric/field listing).
- Each frame variable is defined before it is used (no dangling handoff left
  over from stitching).
- Every `timescope` is an absolute interval, not a relative expression.
- No plaintext secrets / credential values appear in the script.

On success the helper reports `static_checked`; the package manifest records
this mode. A failing check blocks packaging and returns a structured report. A
`static_checked` (or `not_run`) script must be labeled "not reproduced" in both
`manifest.json` and `index.html`, per the publishing design.

## 9. Library surface and ownership boundary

- **Skill (agent) owns generation:** curate, stitch, pin, preamble/tail, and
  copying the dependent semantic model `.py` files into `semantic-embed/`.
- **Library owns the deterministic gate:** a small helper (under the analysis
  publish package introduced by the publishing design, e.g.
  `marivo/analysis/publish/`) that performs the §8 static check and returns a
  pass/fail report plus the `validation` value for the manifest. Publishing
  itself (`mv.publish.report_package(...)`) is already specified by the
  publishing design and is not re-specified here.

## 10. Out of scope / non-goals

- `included_frames` and `mixed` replay modes (packaged frame snapshots). v1 is
  `live_datasource` only.
- Executing `replay.py` at publish time or reconciling its numbers against
  recorded artifacts (deferred to §11).
- Re-deriving evidence objects or prose conclusions inside `replay.py`.
- Re-specifying the report package layout, manifest, or publish pipeline (owned
  by the publishing design).
- Rolling / parameterized (relative-window) replay templates.

## 11. Known residual risk and future work

In-memory stitching (§6.2) is a genuine rewrite of the captured scripts, and the
v1 gate is static-only. A static check confirms the script parses and its
references resolve; it cannot confirm the script computes *the same thing* — a
mis-wired variable or a mis-pinned literal would pass. This is the accepted
cost of `static_checked`.

Optional future hardening, none of which v1 implements:

- **Shadow-DAG structural diff:** deterministically derive a step list from the
  persisted `jobs[]` DAG and diff the assembled script's executable steps
  against it, flagging divergence — a structural oracle that needs no live run.
- **Execute + reconcile:** opt-in execution of `replay.py` in staging, with its
  outputs reconciled against recorded artifact numbers (`row_count`,
  `queries[]`), promoting the manifest `validation` to `executed`.

## 12. Affected files

- `marivo-skills/marivo-analysis/SKILL.md` — add the working-mode capture
  requirement (§5) and a close-out assembly section (§6).
- `marivo-skills/marivo-analysis/references/` — a replay-assembly reference and
  a runnable assembled-`replay.py` example.
- New deterministic static-check helper under the analysis publish package
  introduced by the publishing design (e.g. `marivo/analysis/publish/`).
- `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md` —
  optional cross-reference noting this spec defines replay generation and
  static-checking.

## 13. Acceptance criteria

- After a multi-step analysis (e.g. observe x2 -> compare -> decompose -> drill),
  the close-out produces a single `replay.py` that: imports only `marivo`, uses
  one session bootstrap, passes frames via variables, and contains only absolute
  `timescope` intervals.
- Frame-producing analysis steps are run as `.py` scripts under
  `.marivo/analysis/sessions/<id>/scripts/`; a one-liner / REPL frame step is
  rejected by the skill workflow.
- The static-check helper passes on a well-formed assembled script and fails,
  with a structured report, on each of: unresolved `MetricRef`, an undefined
  frame variable, a relative `timescope`, and an embedded secret.
- The package manifest records `generated_by: "skill"`,
  `input_mode: "live_datasource"`, `validation: "static_checked"`, and both
  `manifest.json` and `index.html` label the script as not reproduced.
- `make typecheck` and the relevant `make test` targets pass for the new helper.
