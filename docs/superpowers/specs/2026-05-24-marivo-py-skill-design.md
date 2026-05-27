# Marivo Python-Library Skill Pack Design

Status: design draft (2026-05-24)

## Background

Marivo today exposes two parallel agent surfaces:

- The **stdio MCP track** — `marivo-analysis` / `marivo-semantic-layer` /
  `marivo-datasource` skills under `marivo-skill/` drive `marivo/runtime/*`
  through MCP tools, and are the path used by conversational analysis flows.
- The **Python-native track** — `marivo/semantic_py/` and `marivo/analysis_py/`
  let agents declare semantic models and run analysis intents directly from
  Python scripts. Specs:
  - `docs/superpowers/specs/2026-05-23-python-semantic-layer-design.md`
  - `docs/superpowers/specs/2026-05-24-python-analysis-design.md`
  - `docs/superpowers/specs/2026-05-24-python-analysis-v1.1-design.md`

The Python-native track has shipped, but general-purpose coding agents
(Claude Code, similar assistants) have no dedicated guidance for using it.
They have to derive intent, frame, session, and lineage semantics from raw
source code, which is high-cost and error-prone for a library that is not
in their training data.

**Strategic direction:** the Python-library track is intended to be the
long-term agent surface. Once this design is validated end-to-end
(post-Phase 3), the stdio MCP track and `marivo/runtime/*` are slated for
removal in a follow-up effort that is out of scope for this spec. The
skills designed here therefore claim the full task surface — they are
not "Python-only" specialists coexisting with a generalist MCP skill,
they are the replacement.

This design specifies a self-contained **Python-library skill pack** —
two new skills plus a small set of agent-ergonomic additions to the two
Python libraries — that brings the agent's cost of using `marivo.semantic_py`
and `marivo.analysis_py` close to "using a library it has seen before."

## Scope

In scope:

- Two new skill packages under `marivo-skill/`:
  - `marivo-skill/marivo-py-semantic/`
  - `marivo-skill/marivo-py-analysis/`
- Agent-ergonomic additions inside `marivo/semantic_py/` and
  `marivo/analysis_py/` only:
  - Top-level introspection / help functions
  - Structured exception `__str__` templates
  - Frame `__repr__` and `summary()` standardization
- Runnable example files under each skill's `references/examples/` directory,
  plus shared `_fixtures/`.
- A drift-prevention CI hook: a new `make examples-check` target,
  inserted into the existing `make check` recipe alongside
  `lint` / `typecheck` / `test`; `make test` itself is not modified.

Out of scope:

- Any change to `marivo/runtime/*`, `marivo/adapters/*`, `marivo/contracts/*`,
  `marivo/main.py`, MCP transports, HTTP transports.
- Any change to the existing stdio MCP skills
  (`marivo-skill/marivo-analysis/`, `marivo-skill/marivo-semantic-layer/`,
  `marivo-skill/marivo-datasource/`).
- New CLI commands, new MCP servers, new HTTP endpoints.
- A `mv.dryrun(...)` / `mv.validate(script_path)` validator. Static feedback
  is `make typecheck` / `make examples-check` (mypy); runtime feedback is
  structured exceptions.
- A `marivo doctor` / bootstrap skill. Environment readiness is covered by a
  short paragraph inside each new SKILL.md instead of its own skill.
- Pre-populating an exhaustive example matrix. Examples grow from real
  observed agent failures (see Phase 4).

## Design Principles

1. **Python-library track is the long-term surface.** The existing stdio
   MCP skills and `marivo/runtime/*` are slated for removal once this
   design is validated end-to-end (post-Phase 3). The new skills therefore
   claim the full task surface (semantic modeling, analysis,
   investigation) with broad triggers and do not need disambiguation
   wording against the legacy MCP skills.
2. **The Python library is the only surface.** No new CLI, no new MCP server.
   Agents work by writing `.py` scripts (or `.venv/bin/python -c '...'` for
   one-shot introspection) and reading stdout / tracebacks / mypy output.
   Bare `python` / `pytest` / `mypy` are forbidden per repository rules
   (`AGENTS.md`); skill content must always show the `.venv/bin/...` or
   `make ...` form.
3. **SDK changes serve agent ergonomics, not human ergonomics.** It is
   acceptable to add helper functions (`mv.help`, `ms.list_metrics`,
   `frame.summary`) that a human library user would not need, because the
   primary consumer is a coding agent.
4. **Errors carry the fix.** Every structured exception prints not only what
   went wrong but a minimal correct code snippet the agent can paste.
5. **Examples are the canonical reference; markdown is a pointer.** SKILL.md
   contains decision trees and template skeletons; complete runnable code
   lives in `.py` files under `references/examples/` that CI typechecks and
   executes.
6. **Drift fails CI.** Renaming a SDK symbol, changing an exception text
   template, or removing a `Literal[...]` value must break `make
   examples-check` immediately. Documentation cannot lag the SDK silently.
7. **Growth is observation-driven.** New examples and pitfalls are added in
   response to real observed agent failures, not pre-filled to cover a matrix.

## Overall Shape

```
marivo-skill/
├── marivo-py-semantic/             ← new
│   ├── SKILL.md
│   ├── references/
│   │   ├── examples/
│   │   ├── cheatsheet.md
│   │   └── pitfalls.md
│   └── evals/
└── marivo-py-analysis/             ← new
    ├── SKILL.md
    ├── references/
    │   ├── examples/
    │   ├── cheatsheet.md
    │   └── pitfalls.md
    └── evals/
```

SDK additions live only in `marivo/semantic_py/` and `marivo/analysis_py/`.
No other marivo subpackage is touched.

Skill triggers (in `description:` frontmatter) are **broad**. The
Python-library track is the planned long-term agent surface; once it is
fully validated, the existing stdio MCP skills (`marivo-analysis`,
`marivo-semantic-layer`, `marivo-datasource`) are slated for removal. The
new skills therefore claim the full task surface — anything involving
marivo for semantic modeling or analysis — without any qualifier about
"Python scripts" or "imports".

Concrete suggested wording:

- `marivo-py-semantic`: "Use when the task involves declaring a Marivo
  semantic model — datasource, dataset, field, metric, or relationship."
- `marivo-py-analysis`: "Use when the task involves Marivo analysis —
  observe, compare, decompose, detect, correlate, or any
  investigation/diagnosis flow over a Marivo semantic model."

During the transition period (Phases 0–3, while the MCP track still
exists), if both a new skill and a legacy MCP skill match a prompt,
Claude Code's normal skill-selection behavior decides. No special
disambiguation is built. Once Phase 3 has been validated in real use,
the MCP skills and the `marivo/runtime/*` track are deleted in a
follow-up PR (out of scope for this spec).

## SKILL.md Structure

Both SKILL.md files use the same skeleton, capped at ~600 lines so an agent
loads each in one read:

1. One-line positioning + when NOT to use.
2. 30-second overview: import line + name-only listing of main
   objects/functions/decorators with one-line summaries.
3. Standard workflow (numbered):
   - Before writing: call `ms.list_*()` / `mv.session.current()` to read
     project state, e.g. via `.venv/bin/python -c 'import marivo.analysis_py as mv; print(mv.session.current())'`.
   - After writing: run `make typecheck` and fix red errors first.
   - Run: `.venv/bin/python <script>` (bare `python` is forbidden).
   - On error: read the traceback's hint + example_fix section and apply it.
4. Fill-in-the-blank templates (5–8 highest-frequency patterns), each
   ending with `See also: references/examples/<file>.py`.
5. Decision trees (explicit branches, not prose) for "which intent /
   decorator should I use here".
6. Common pitfalls (symptom → cause → fix), each cross-linking to
   `references/pitfalls.md`.
7. Further reading: index of `cheatsheet.md` / `pitfalls.md` / `examples/`.

### Skill-Specific Differences

| Axis              | marivo-py-semantic                                       | marivo-py-analysis                                                  |
|-------------------|----------------------------------------------------------|---------------------------------------------------------------------|
| Main objects      | `@ms.datasource` / `@ms.dataset` / `@ms.metric` + `ms.reload()` | `mv.session` + 5 intents + 4 Frame families                         |
| Template coverage | Register datasource, declare dataset, define metric (aggregate + derived), reload | observe / compare / decompose / detect plus 1–2 end-to-end DAGs     |
| Decision trees    | "field vs metric", "ibis expression vs SQL string"       | "which intent", "when alignment is required"                        |
| Pitfall focus     | Decorator registration timing, backend source, reload effects | Frame type immutability, window string format, cross-script session continuity |

### Template Convention

Every fill-in-the-blank template in SKILL.md uses this fixed shape so the
agent recognizes the structure:

```python
# Pattern: <one-line business intent>
# When to use: <which situation picks this template>
# See also: references/examples/<file>.py
import marivo.analysis_py as mv

cur  = mv.observe("<metric_id>", window="<current_window>")
base = mv.observe("<metric_id>", window="<baseline_window>")
delta = mv.compare(cur, base, compare_type="<yoy|mom|qoq|custom>")
print(delta.summary())
```

Four required elements: intent comment, when-to-use, references link,
empty slots marked with `<...>` rather than concrete values.

### Decision-Tree Convention

Decision trees are written as explicit branches, not paragraphs:

```
Q: Should this concept be a field or a metric?
├─ Computable from a single record (e.g. user.country) → field
├─ Requires cross-record aggregation (e.g. sum(revenue)) → metric
└─ Composition of metrics (e.g. a / b)                  → derived metric
```

## SDK Additions

All additions are confined to `marivo/semantic_py/` and `marivo/analysis_py/`.
Existing function signatures, parameter semantics, return types, session
persistence formats, and ibis execution paths are not modified.

### Introspection (top-level exports)

```python
# semantic_py
ms.list_datasources() -> list[DatasourceInfo]
ms.list_datasets(datasource_id: str | None = None) -> list[DatasetInfo]
ms.list_metrics(dataset_id: str | None = None) -> list[MetricInfo]
ms.describe(name: str) -> DetailInfo           # name = any datasource/dataset/metric id
ms.help(symbol: str | None = None) -> None     # print signature + docstring + 1 example

# analysis_py
mv.session.list() -> list[SessionInfo]
mv.session.current() -> SessionInfo | None
mv.session.history(limit: int = 5) -> list[JobInfo]
mv.calendar.windows() -> list[WindowInfo]
mv.help(symbol: str | None = None) -> None
```

All Info / Detail types are Pydantic models with full type hints. Returning
Pydantic gives the agent a `__repr__` it can read in stdout and a typed
shape it can pass through mypy.

### Structured Exceptions

The two libraries already define base classes `AnalysisError` (in
`marivo/analysis_py/errors.py`) and `SemanticError` (in
`marivo/semantic_py/errors.py`), each with multiple concrete subclasses.
No new base class is introduced.

This design overrides `__str__` on `AnalysisError` and `SemanticError`
(and, where needed, on individual subclasses that need richer context)
to enforce a single fixed template:

```
<ErrorType>: <one-line fact>

发生位置: <file:line or intent call name>
原因: <why this happened, citing concrete parameter values>
建议: <actionable fix>

正确写法:
  <minimum pasteable correct snippet>

相关文档: <SKILL.md or cheatsheet.md path>
```

Each concrete subclass populates the template fields via a
`_template_fields() -> dict` hook (or equivalent), so the base class
formatting stays single-source. Class identity (name, inheritance, the
constructor signature) does not change.

The subclasses below are the agent-visible subset that this design
standardizes. The columns mark whether the class already exists today or
needs to be added.

| Exception class                | Triggered by                                                       | Existing? |
|--------------------------------|--------------------------------------------------------------------|-----------|
| `SemanticKindMismatchError`    | Passing a frame of the wrong kind (e.g. DeltaFrame to `compare`)   | yes       |
| `MetricNotFoundError`          | `observe` called with an unregistered metric_id                    | yes       |
| `WindowInvalidError`           | window string the calendar cannot parse                            | yes       |
| `AlignmentFailedError`         | `compare` cannot align two frames' schemas                         | yes       |
| `NoBackendFactoryError`        | datasource function did not return an ibis backend                 | yes       |
| `FrameMutationError`           | Direct mutation of `Frame.data` attempted (not `to_pandas()`)      | yes       |
| `NoActiveSessionError`         | Operation requires an active session but none is attached          | yes       |
| `DatasourceNotRegisteredError` | `@ms.dataset` referencing an undeclared datasource                 | **new**   |
| `IRReloadRequiredError`        | `.py` source changed but `ms.reload()` was not called              | **new**   |
| `LineageBrokenError`           | `decompose` input Frame lacks a valid lineage chain                | **new**   |

Phase 1 standardizes the three highest-frequency analysis exceptions
(`SemanticKindMismatchError`, `MetricNotFoundError`, `WindowInvalidError`).
Phase 2 adds the new semantic exceptions and standardizes
`NoBackendFactoryError`. Phase 3 standardizes the remaining analysis
exceptions (`AlignmentFailedError`, `NoActiveSessionError`,
`FrameMutationError`) and adds `LineageBrokenError`.

Other existing exception classes in `errors.py` that are not in this table
(e.g. `SliceInvalidError`, `CrossBackendMetricError`,
`DuplicateSessionNameError`, etc.) inherit the new base `__str__`
automatically but do not need bespoke `_template_fields()` content in v1;
they will adopt richer templates in Phase 4 as real agent failures surface
them.

### Frame Standardization

`MetricFrame` / `DeltaFrame` / `AttributionFrame` (and any future Frame
family) share a unified `__repr__` and a new `summary()` method.

`__repr__` example:

```
<MetricFrame metric=revenue rows=12 cols=[bucket,value] window=q3_2026 session=s_abc123>
  bucket      value
  2026-07-01  120341.5
  2026-08-01  118203.0
  2026-09-01  131045.2
  ... (9 more rows, use .to_pandas() to materialize)
```

Fixed elements: frame type + key meta + shape + `head(3)` + explicit
guidance on how to fetch the full data. Default `print(frame)` therefore
cannot leak a 50k-row dataset into the agent's context window.

`frame.summary()` returns a Pydantic `FrameSummary` with row count, column
list, null ratios, one-line lineage trace, and source `job_id`. The agent
calls `.summary()` to decide whether to materialize.

### Help Functions

```python
mv.help()                    # print top-level entry list (5 intents + session + calendar + help)
mv.help("compare")           # print compare's signature, docstring, 1 minimal example, Raises
mv.help("SemanticKindMismatchError") # also accepts exception class names
```

Implementation is a thin wrapper using `inspect.signature` plus parsed
docstrings. Each intent's docstring must include ≥ 1 worked example and
the full `Literal[...]` enumeration for each enum parameter.

### Off-Limits

The following are not modified:

- Existing intent / decorator function signatures, parameter order, return
  types.
- Session persistence file format under `<project_root>/.marivo/analysis/`.
- Ibis expression and backend execution path.
- `marivo/runtime/*`, `marivo/adapters/*`, `marivo/contracts/*`,
  `marivo/main.py`, MCP transports, HTTP transports.

## Examples Layout

Both skills follow the same directory pattern. The listing below is the
**full state after Phase 3 lands**, not the Phase 1 minimum. Phase 1
ships only the analysis half's minimum (see Minimum Coverage at v1 below);
later phases add the rest.

```
marivo-skill/marivo-py-semantic/references/examples/
├── 01_register_datasource.py
├── 02_declare_dataset.py
├── 03_define_metric_aggregate.py
├── 04_define_metric_derived.py
├── 05_relationships.py
├── 99_pitfall_decorator_at_import_time.py
├── 99_pitfall_reload_after_edit.py
└── _fixtures/
    └── tiny_db.py

marivo-skill/marivo-py-analysis/references/examples/
├── 01_observe_single_window.py
├── 02_compare_yoy.py
├── 03_decompose_attribution.py
├── 04_detect_anomaly.py
├── 05_correlate_two_metrics.py
├── 10_dag_yoy_then_attribute.py
├── 11_dag_anomaly_then_diagnose.py
├── 99_pitfall_pass_delta_to_compare.py
├── 99_pitfall_window_string_format.py
└── _fixtures/
    └── tiny_semantic.py
```

### Numbering Convention

- `01–09` — single intent / decorator minimal usage
- `10–19` — end-to-end DAG compositions
- `99_pitfall_*` — counter-examples that surface a specific structured
  exception
- `_fixtures/` — shared in-memory DuckDB backend and a minimal registered
  semantic model so examples run without any external database

### Per-File Structure

Every example file is shaped like this so an agent can mechanically copy
the pattern into its own script:

```python
"""
Pattern: <one-line business intent, matching the SKILL.md template title>
When to use: <which situation picks this template>
Output shape: <what gets printed when this runs>
"""
from __future__ import annotations

# Setup (minimal, not part of the pattern)
from _fixtures.tiny_semantic import ensure_loaded
ensure_loaded()

# Main pattern
import marivo.analysis_py as mv

cur  = mv.observe("revenue", window="2026Q3")
base = mv.observe("revenue", window="2025Q3")
delta = mv.compare(cur, base, compare_type="yoy")
print(delta.summary())

# Expected output:
# DeltaFrame metric=revenue rows=1 cols=[delta_abs,delta_pct] ...
```

### Pitfall File Convention

Every `99_pitfall_*.py` must run, raise a structured exception, catch it,
print it, and exit 0. The file's `Expected output:` comment block contains
the key phrases (exception class name + the headline of the suggested fix)
that CI asserts on stdout.

```python
"""
Pitfall: passing DeltaFrame back into compare
When triggered: agent uses `delta` instead of `base` for second compare call
"""
import marivo.analysis_py as mv
from _fixtures.tiny_semantic import ensure_loaded
ensure_loaded()

cur  = mv.observe("revenue", window="2026Q3")
base = mv.observe("revenue", window="2025Q3")
delta = mv.compare(cur, base, compare_type="yoy")

try:
    wrong = mv.compare(cur, delta)
except mv.errors.SemanticKindMismatchError as e:
    print(e)

# Expected output:
# SemanticKindMismatchError: compare(a, b) expected MetricFrame for `b`, got DeltaFrame.
# ...
# 正确写法:
#   delta = mv.compare(cur, base)
```

### Minimum Coverage at v1

| Skill              | Minimum examples                                                                 |
|--------------------|----------------------------------------------------------------------------------|
| marivo-py-semantic | 4 single-unit + 1 pitfall + 1 shared fixture                                     |
| marivo-py-analysis | 4 single-intent + 2 end-to-end DAGs + 1 pitfall + 1 shared fixture               |

Subsequent additions are driven by observed agent failures (Phase 4),
capped at 20 examples per skill.

### Bidirectional Linking

- Every SKILL.md template ends with `See also: references/examples/<file>.py`.
- Every example file's docstring first line is
  `Pattern: <title>` and that title appears verbatim in the corresponding
  SKILL.md template.

CI enforces both directions (see Drift Protection).

## Drift Protection

### `make examples-check`

Added to the root `Makefile` as an independent target, using the existing
toolchain variables (`VENV_MYPY`, `VENV_PYTHON`) — no new tool is
introduced:

```make
examples-check: ## Typecheck + smoke-run skill examples
	@$(VENV_MYPY) marivo-skill/marivo-py-semantic/references/examples \
	              marivo-skill/marivo-py-analysis/references/examples
	@$(VENV_PYTHON) scripts/run_skill_examples.py
```

The repo's existing `typecheck` target runs `$(VENV_MYPY) marivo` and is
not modified; `examples-check` adds a second mypy invocation scoped to
the example trees only, so unrelated mypy noise in the examples cannot
leak back into `make typecheck`.

`scripts/run_skill_examples.py` (≤ 150 lines) does the following:

1. Walk `marivo-skill/marivo-py-*/references/examples/*.py` (skip
   `_fixtures/`).
2. Execute each file via subprocess with a 30-second timeout.
3. For `99_pitfall_*.py`: assert stdout contains the key phrases declared
   in the file's `Expected output:` comment block (exception class name +
   fix headline).
4. For all other examples: assert exit code 0 and non-empty stdout.
5. Run the bidirectional-link static check (see below).
6. Assert each `marivo-skill/marivo-py-*/SKILL.md` is ≤ 600 lines; fail
   with a remediation hint to split content into `references/` if exceeded.
7. On any failure: non-zero exit + named failing file + diff hint.

### Integration with the existing `make check` and CI

The repo's existing top-level `check` target is `check: lint typecheck
test` — the full local pre-push gate. This design **extends** that target
to `check: lint typecheck examples-check test`, adding `examples-check`
between typecheck and test. `make test` is **not** modified, and the
existing local-gate semantics (running tests as part of `check`) are
preserved.

CI continues to run `make check` as a required step, so any skill drift
fails CI immediately alongside any other check or test failure.

`make examples-check` is also runnable on its own for fast iteration on
the skill content alone.

### Bidirectional-Link Static Check

`scripts/run_skill_examples.py` includes a static phase that:

1. Parses every `See also: references/examples/<file>.py` reference inside
   each SKILL.md and asserts the file exists.
2. Parses every example file's `Pattern: <title>` and asserts the exact
   title appears in the corresponding SKILL.md.
3. On mismatch: prints the broken link and instructs the developer to
   update either the SKILL.md reference or the example filename/title.

### SDK Rename / Removal Feedback

Because examples import `marivo.{semantic_py,analysis_py}` and are both
typechecked and executed:

- Renamed function → mypy fails examples
- Changed `Literal[...]` enum → mypy fails examples
- Renamed exception class → `except mv.errors.<Name>` import fails
- Changed exception `__str__` template → pitfall stdout assertion fails

In all cases `make examples-check` is the immediate signal. Modifying the
SDK without updating the corresponding skill content cannot pass CI.

### Failure Message Template

When `run_skill_examples.py` fails, stderr prints:

```
[examples-check] FAILED: marivo-skill/marivo-py-analysis/references/examples/02_compare_yoy.py

  Reason: <mypy error | runtime exception | missing pitfall keyword>

  Likely cause:
    - You changed a signature / exception text / enum in marivo.analysis_py
    - The skill example has not caught up

  Fix:
    1. Run .venv/bin/python <file> to see the full output
    2. Update the example to match the new SDK, or roll back the SDK change
    3. If SKILL.md references that template, update the See-also / decision tree
```

### Out of Scope

- A dedicated `marivo-py-skill-maintainer` skill or separate CI job.
  Extending `make check` so that `examples-check` runs alongside the
  existing local gate is sufficient — CI already runs `make check`.
- Typechecking SKILL.md embedded code blocks. All executable code is in
  `.py` example files; SKILL.md only references them.
- Full stdout snapshot comparison. Exit code 0 plus pitfall keyword
  assertions are enough and avoid snapshot brittleness.

## Phased Delivery

### Phase 0 — CI harness skeleton

Goal: Establish the drift-protection infrastructure so Phase 1 and
Phase 2 can land independently and in any order.

Changes:

- `Makefile`: add a new `examples-check` target and update the existing
  `check` recipe to `check: lint typecheck examples-check test` (insert
  the new step alongside the current ones). `make test` is **not**
  modified.
- `scripts/run_skill_examples.py`: full implementation, even though no
  examples exist yet. On an empty examples tree it succeeds trivially.
- CI workflow: ensure `make check` (already the required step today)
  continues to run; no new CI job is added.
- Create the two skill directory skeletons
  (`marivo-skill/marivo-py-semantic/`, `marivo-skill/marivo-py-analysis/`)
  with placeholder `SKILL.md` files, empty `references/examples/`
  directories, and empty `_fixtures/` directories so the runner has paths
  to walk.

Acceptance:

- `make examples-check` is green on the empty tree.
- `make check` passes on CI.
- Adding any file under `references/examples/*.py` (in a subsequent
  phase) is immediately covered by the runner without further wiring.

### Phase 1 — Minimum viable analysis loop

Depends on: Phase 0.

Goal: An agent can complete `mv.observe → mv.compare` end-to-end and
recover from its own errors using the structured exception template.

Changes:

- `marivo/analysis_py/errors.py`: override `AnalysisError.__str__` with
  the fixed template; add the `_template_fields()` hook on
  `SemanticKindMismatchError`, `MetricNotFoundError`, `WindowInvalidError`.
- `marivo/analysis_py/frames/*.py`: unify `__repr__` and add `summary()`
  returning a Pydantic `FrameSummary`.
- `marivo/analysis_py/__init__.py`: export `mv.help`,
  `mv.session.list/current/history`.
- Fill in `marivo-skill/marivo-py-analysis/SKILL.md` per the skeleton
  above, plus 4 minimum examples, 1 pitfall, and
  `_fixtures/tiny_semantic.py`.

Acceptance:

- `make examples-check` is green.
- In a clean working directory, an agent reading the new SKILL.md can
  complete `observe → compare → print summary` without needing follow-up
  questions about SDK shape.

### Phase 2 — Semantic modeling half

Depends on: Phase 0. Independent of Phase 1.

Goal: An agent can author a usable `.marivo/semantic/<model>/` Python
model from scratch.

Changes:

- `marivo/semantic_py/errors.py`: override `SemanticError.__str__` with
  the fixed template; add new subclasses `DatasourceNotRegisteredError`
  and `IRReloadRequiredError` (and any others needed for semantic); add
  the `_template_fields()` hook on `NoBackendFactoryError` (which lives
  in `analysis_py/errors.py` but covers semantic-side wiring failures).
- `marivo/semantic_py/__init__.py`: export `ms.help`,
  `ms.list_datasources/datasets/metrics`, `ms.describe`.
- Fill in `marivo-skill/marivo-py-semantic/SKILL.md` with 4 examples, 1
  pitfall, and `_fixtures/tiny_db.py`.

(`scripts/run_skill_examples.py` is already in place from Phase 0; the
new files under `references/examples/` are picked up automatically.)

Acceptance:

- `make examples-check` is green.
- An agent reading the new SKILL.md can produce a fresh datasource +
  dataset + 2 metrics from an empty directory; `ms.reload()` succeeds and
  `ms.list_metrics()` returns the new entries.

### Phase 3 — End-to-end DAG + long-tail exceptions

Goal: Agents fluently chain modeling and analysis; exception coverage
extends to the long tail.

Changes:

- `marivo/analysis_py/errors.py`: add `_template_fields()` hooks on
  `AlignmentFailedError`, `NoActiveSessionError`, `FrameMutationError`;
  add new subclass `LineageBrokenError`.
- Both SKILL.md files get 2 end-to-end DAG examples (YoY attribution,
  anomaly diagnosis) and full decision-tree coverage.
- Bidirectional link static check integrated into
  `scripts/run_skill_examples.py`.
- `mv.calendar.windows()` implementation and export.

Acceptance: An agent completes "build a revenue metric, run quarterly YoY,
decompose by region" without hand-holding. The trajectory triggers at
least one structured exception and the agent self-recovers from the
example_fix snippet.

### Phase 4 — Observation-driven maintenance

Triggered only after Phase 1–3 have been used in real sessions:

- Add new pitfall examples (one per observed real-world failure).
- Fine-tune exception text and hint sections.
- Optionally add `evals/` data sets for each skill to quantify agent
  success rate.

This phase explicitly does not pre-fill a 20-example matrix; growth is
demand-driven.

### Inter-phase Dependencies

```
Phase 0 (CI harness, skeleton dirs)
   │
   ├─► Phase 1 (analysis content)  ──┐
   │                                  ├─► Phase 3 (DAG + long-tail)
   └─► Phase 2 (semantic content) ───┘
                                          │
                                          └─► Phase 4 (observation-driven)
```

Phase 0 must land first because it establishes both the runner and the
Makefile target. Once Phase 0 is in, Phase 1 and Phase 2 are genuinely
independent — different contributors can take them in either order or in
parallel. Phase 3 needs both content halves landed because its DAG
examples cross them. Phase 4 is open-ended and contributor-driven.

Each phase has its own self-contained acceptance criterion, so any phase
can be interrupted without leaving the system half-done.

## Risks and Mitigations

| Risk                                                            | Mitigation                                                              |
|-----------------------------------------------------------------|-------------------------------------------------------------------------|
| `mv.help` / `ms.help` strings drift from the actual code        | Docstrings are parsed live; example imports + execution catch drift     |
| `_fixtures/` is too simplistic to mirror real backends          | Allow `_fixtures/small_real/` later if needed, without changing layout  |
| mypy noise on examples                                          | mypy is invoked separately for the examples tree in `examples-check`; `make typecheck` is not affected |
| Agent copies fixture lines as part of the real pattern          | Mandatory `# Setup (minimal, not part of the pattern)` comment header   |
| SKILL.md grows past 600 lines                                   | Hard cap enforced by `scripts/run_skill_examples.py` static check       |

## Success Criteria

The design is successful when:

1. An agent receiving a fresh repo and a prompt like "use marivo to do a
   YoY comparison of revenue and break down the change by region" can
   complete the task end-to-end by reading only the two new SKILL.md
   files plus the example files they cross-reference — no human follow-up
   on SDK shape, exception meaning, or session resumption.
2. Any future change to `marivo/semantic_py/` or `marivo/analysis_py/`
   that affects an agent-visible surface (signature, enum, exception text,
   help output) breaks `make examples-check` immediately.
3. Pitfall coverage grows monotonically: each observed real-world agent
   failure adds exactly one `99_pitfall_*.py` file and at most one new
   exception class, never more.
