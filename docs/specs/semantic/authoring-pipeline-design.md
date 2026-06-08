# Authoring Pipeline Design

Status: draft design.

This document defines the target-state authoring pipeline for Marivo semantic
layer construction. It replaces the `NextCheck`-driven choreography with a
three-phase authoring loop backed by one composed static-assessment API and
one closeout readiness gate.

It complements `docs/specs/semantic/python-semantic-layer.md` (which owns the
Python semantic object model and decorator contracts) and supersedes the
authoring workflow sections of both
`docs/specs/semantic/skill-semantic-layer-authoring-design.md` and
`docs/specs/semantic/agent-semantic-layer-authoring-design.md`. Where this
document conflicts with those documents' workflow and `NextCheck`-driven
choreography, this document is the target-state replacement.

## Problem Statement

The current authoring pipeline exposes a 13-step choreography via the
`NextCheck` enum:

```python
NextCheck = Literal[
    "inspect_source_context",
    "inspect_column_context",
    "check_authoring_inputs",
    "write_semantic_python",
    "reload_project",
    "inspect_authored_object",
    "preview_dataset",
    "preview_field",
    "preview_metric",
    "parity_check",
    "readiness",
    "richness",
    "ask_user",
]
```

This design has several structural problems:

1. **No state machine.** `AssessmentResult.next_checks` returns a tuple of
   `NextCheck` values, but there is no encoded transition logic. What comes
   after `inspect_source_context`? The agent must infer from skill
   documentation, not from code.

2. **No data flow between steps.** `inspect_source_context` returns source
   facts; the agent must extract schema and column profiles from it and
   manually feed them into `check_authoring_inputs(columns=...)` and
   `inspect_column_context(columns=...)`. Method signatures do not accept
   prior-step outputs as inputs.

3. **Asymmetric write step.** `"write_semantic_python"` has no corresponding
   `SemanticProject` method — it is an instruction for the agent to write a
   file. The 12 API-call steps are interleaved with a non-API step, splitting
   the pipeline into pre-write and post-write halves with no structural
   acknowledgment of that split.

4. **Branch logic in prose.** `check_authoring_inputs` may return `blocked`,
   `needs_evidence`, or `supported`. The target contract renames the middle
   state to `needs_input`, but either shape requires different handling
   (supplement context → re-check → write → reload), and that branching is
   described only in skill markdown, not in executable code.

5. **Skill as orchestrator.** The 16 non-negotiable rules and 9-stage workflow
   in the skill document serve as the de facto pipeline orchestrator. If the
   skill document and the API drift apart, there is no compile-time or
   runtime signal.

## Core Principle

The project API provides facts and assessments. The agent provides creative
decisions — what to author, how to name it, what business definition to write.
`assess_authoring(...)` must only consume inputs that help Marivo inspect its
own current metadata, bounded samples, datasource access, decision ledger, and
semantic registry state. It must not accept draft authored-object content or
persist authoring evidence as a side effect. Source and column observations
are treated as fresh inspection results, not durable project truth. The
pipeline should compose the API's static assessment checks into fewer, larger
operations that the agent calls at natural decision points.
Runtime closeout checks that execute previews or parity are owned by
`readiness(...)`, which reports per-ref blockers and warnings without asking
the agent to choreograph separate validation calls.

## Persistence Boundary

The target pipeline does not define a persistent source, column, or authoring
evidence store. Persisting sampled source facts is fragile because datasource
content can change, and persisting loosely related authoring notes creates a
false sense of auditability for agent decisions.

Marivo persists only durable project artifacts:

- authored semantic Python files;
- explicit decision-ledger records for dangerous or user-confirmed choices;
- datasource definitions and project configuration.

Inspection APIs may return rich facts and may use short-lived in-process caches
within one `SemanticProject` session. Those observations are not reused across
sessions as authoritative input to assessment or readiness.

## Three-Phase Authoring Flow

### Phase 1: Discovery

The agent determines which tables belong to the semantic model and what
candidate semantic objects they might produce.

1. Load the project and inspect existing refs; search for reuse before
   authoring.
2. For each relevant table, collect source metadata:

```python
project.bind_datasource_access(
    inspect_source=mv.datasources.inspect_source,
    backend_factory=mv.datasources.build_backend,
)
source_facts = project.inspect_source_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders", database="sales_mart"),
    sample_policy=ms.BoundedProfilePolicy(limit=100, max_profiled_columns=50),
)
```

3. The agent ranks columns from source facts (type, comments, nullable,
   partition hints, sampled values). Deep-dive a small set if needed:

```python
columns = project.inspect_column_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
    columns=("status", "amount"),
    sample_policy=ms.BoundedProfilePolicy(limit=100),
)
```

4. The agent decides candidates: which tables enter the semantic model, and
   what semantic objects (dataset, time_field, field, metric) each table
   should produce.

The API provides information for the agent's judgment. It does not return a
candidate worklist or suggest what to author.

### Phase 2: Authoring

The agent authors semantic objects iteratively in the model's single
`_model.py` file. No reload occurs during this phase.

#### 2.0 Create `_model.py`

Every model has exactly one authoring file. It contains the model declaration,
datasource references, datasets, fields, metrics, relationships, and derived
metrics:

```python
# .marivo/semantic/sales/_model.py
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales", description="Sales analytics")
warehouse = md.ref("warehouse")
```

#### 2.1 Per-source authoring

For each dataset derived from a single source:

**2.1.0 Inspect source context** — collect current metadata and bounded sample
facts for the source.

**2.1.1 Assess each candidate object** — the agent calls
`project.assess_authoring(...)` for each candidate. The API internally
orchestrates current inspection checks and returns an `AuthoringAssessment`:

```python
assessment = project.assess_authoring(
    object_kind="metric",
    subject_ref="sales.revenue",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("amount", "paid"),
        ),
    ),
    semantic_refs=("sales.orders",),
)

if assessment.status == "blocked":
    # resolve blockers from assessment.issues, then re-assess
    pass
elif assessment.status == "needs_input":
    # ask user about assessment.questions, supplement context, re-assess
    pass
# status == "supported" → proceed to write
```

If the assessment reports that a business decision is unresolved, the agent
asks the user or consults external project context, then writes the resolved
semantic object content or an explicit decision-ledger record. The assessment
call itself does not persist source, column, or authoring evidence.

**2.1.2 Append confirmed objects to `_model.py`** — the agent writes all
confirmed objects for this source in dependency order:

```python
orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when the table exposes a test flag."],
    },
)

@ms.time_field(dataset=orders, name="log_date", data_type="string",
               granularity="day", date_format="%Y%m%d")
def log_date(table):
    return table.dt

@ms.field(dataset=orders, name="region")
def region(table):
    return table.region

@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="revenue",
    verification_mode="sql_parity",
    source_sql="SELECT SUM(amount) AS revenue FROM orders",
    source_dialect="trino",
    ai_context={
        "business_definition": "Gross order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using as net revenue."],
    },
)
def revenue(table):
    return table.amount.sum()
```

No reload is needed between writing different objects in `_model.py`. Python
variable references (`orders`, `revenue`) resolve when the file is loaded in
Phase 3.

#### 2.2 Cross-dataset objects (multi-dataset models)

When the model contains multiple datasets, relationships, cross-dataset
metrics, and derived metrics are also authored in `_model.py`.

**2.2.0 Assess each candidate** — use `assess_authoring` with one
`AuthoringSourceInput` per physical source role. A relationship must have
current from-side and to-side source context before it can be ready:

```python
assessment = project.assess_authoring(
    object_kind="relationship",
    subject_ref="sales.orders_to_customers",
    sources=(
        ms.AuthoringSourceInput(
            role="from",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("customer_id",),
        ),
        ms.AuthoringSourceInput(
            role="to",
            datasource="warehouse",
            source=ms.TableSource(table="customers"),
            columns=("customer_id",),
        ),
    ),
    semantic_refs=(
        "sales.orders",
        "sales.customers",
        "sales.orders.order_customer_id",
        "sales.customers.customer_id",
    ),
)
```

Cross-dataset base metrics use multiple `primary` source roles rather than
`component` roles:

```python
assessment = project.assess_authoring(
    object_kind="metric",
    subject_ref="sales.revenue_per_active_customer",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("amount", "customer_id"),
        ),
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="customers"),
            columns=("customer_id", "is_active"),
        ),
    ),
    semantic_refs=(
        "sales.orders",
        "sales.customers",
        "sales.orders.customer_id",
        "sales.customers.customer_id",
    ),
)
```

**2.2.1 Append to `_model.py`** — use local decorated Python refs when the
target was declared earlier in the file. Use `ms.ref(...)` only for forward
references or generated tooling cases, and follow the current implementation's
plain semantic-id format:

```python
ms.relationship(
    name="orders_to_customers",
    from_dataset=orders,
    to_dataset=customers,
    from_fields=[order_customer_id],
    to_fields=[customer_id],
)
```

`ms.ref("sales.orders")` resolves to the deterministic semantic id, which does
not require an already-bound Python symbol.

### Phase 3: Validation

All semantic objects are validated after all code is written. Phase 3 uses
`readiness(...)` as the single closeout gate: it reloads the project state,
checks refs, runs required backend previews, runs eligible parity checks, and
folds richness findings into one `ReadinessReport`.

`readiness(...)` depends on backend access bound on the project, such as the
backend factory registered through `project.bind_datasource_access(...)`. The
agent must not pass a separate `backend_factory` at readiness time. If the
project has no bound backend access, readiness returns a blocker instead of a
fallback or degraded report.

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
    parity_rel_tol=1e-6,
)
```

Validation closeout rules:

- load errors, missing refs, invalid authored objects, missing backend access,
  datasource/backend failures, and preview/materialization failures are
  blockers because they can make later analysis fail;
- parity findings are warnings, including missing parity, unsupported parity,
  and drift from `source_sql`;
- readiness blockers prevent analysis handoff;
- richness findings are warnings and never block handoff.

### Why no reload in Phase 2

Same-file variable references work without reload. Forward references or
generated tooling refs use `ms.ref("sales.orders")` with the current
semantic-id format. Auto-recorded decisions (`metric_decomposition`,
`time_field_identity`) only affect validation, not authoring. Deferring reload
to Phase 3 reduces N reloads (N = number of objects) to 1.

## File Organization Contract

```
.marivo/semantic/<model>/
  _model.py         # all semantic declarations for the model
```

Rules:

- `_model.py` always exists and is the only normal authoring file for a model.
- The file starts with `ms.model(...)` and datasource refs, then declares
  datasets, fields, time fields, metrics, relationships, and derived metrics in
  dependency order when possible.
- Use local decorated Python refs for objects declared earlier in the file.
- Use `ms.ref("<semantic-id>")` only for forward references or generated
  tooling cases. This design follows the current implementation's semantic-id
  refs.
- The current loader can execute sibling files, but this authoring pipeline
  deliberately does not use them. Multi-file authoring remains a lower-level
  loader capability, not the normal agent authoring contract.

## New APIs

### `project.assess_authoring(...)`

```python
def assess_authoring(
    self,
    *,
    object_kind: Literal[
        "dataset", "field", "time_field", "metric", "derived_metric", "relationship"
    ],
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AuthoringAssessment:
```

#### `AuthoringSourceInput` DTO

```python
@dataclass(frozen=True)
class AuthoringSourceInput:
    role: Literal["primary", "from", "to", "component"]
    datasource: str
    source: DatasetSource
    columns: tuple[str, ...] = ()
```

`sources=()` is allowed only for derived metrics or other objects whose
grounding is entirely semantic-ref based. Dataset, field, time-field, base
metric, and relationship assessment must include the relevant physical source
roles.

Source roles are interpreted as follows:

| Role | Use |
| --- | --- |
| `primary` | A source directly used by a dataset, field, time field, or base metric; cross-dataset base metrics pass one `primary` role per participating dataset |
| `from` | Relationship from-side source |
| `to` | Relationship to-side source |
| `component` | Source context for a metric component when the assessed object is component-driven but not a pure derived metric |

If an agent needs a non-default sample policy for exploratory judgment, it can
call `inspect_source_context(..., sample_policy=...)` or
`inspect_column_context(...)` in Phase 1 and use the returned facts directly.
Those observations are not durable truth. `assess_authoring(...)` uses the
default bounded inspection policy unless the future API explicitly adds a
per-call inspection policy.

Parameters:

| Parameter | Purpose |
| --- | ------- |
| `object_kind` | The kind of semantic object being assessed |
| `subject_ref` | Target semantic id using the current implementation format (e.g. `"sales.revenue"` or `"sales.orders.region"`) |
| `sources` | Physical source roles and columns that support the target object |
| `semantic_refs` | Semantic refs the target depends on (e.g. dataset ref for a metric, both dataset refs and join-key field refs for a relationship) |

`assess_authoring(...)` does not accept `ai_context`, inline `evidence`, or
explicit `evidence_refs`, and it does not read a persistent evidence store:

- `ai_context` is authored content. The agent writes it into `_model.py` in
  Phase 2.1.2, and Phase 3 checks the loaded object.
- `source_sql`, BI definitions, and natural-language business definitions are
  authored content or user-facing context, not inputs to a Marivo assessment
  call.
- explicit `evidence_refs` make the agent pre-judge which evidence is
  relevant and create a false sense of auditability. Marivo should inspect the
  current datasource and registry state itself, then report what it can prove
  from those current observations.

Internal orchestration:

1. For each `AuthoringSourceInput`, inspect the current datasource/source with
   `BoundedProfilePolicy(limit=100, max_profiled_columns=50)` using project-
   bound datasource access.
2. For each source role, if `columns` is non-empty, inspect those columns in
   the current source.
3. Read explicit decision-ledger records relevant to dangerous choices such as
   metric decomposition, time-field identity, and relationship confirmation.
4. Call the breaking multi-source `check_authoring_inputs(...)` with the full
   `sources` tuple and the full `semantic_refs` tuple.
5. Return `AuthoringAssessment` with facts, issues, and questions that Marivo
   can derive from current project state.

This design intentionally changes `check_authoring_inputs(...)` from a
single-source guardrail to a multi-source guardrail. No compatibility shim is
defined for the old `datasource=...`, `source=...`, `columns=...` signature.
The checker owns role-aware physical-column validation, so a relationship
to-side column is never checked against the from-side source by accident.

The implementation work must update:

- `SemanticProject.check_authoring_inputs(...)` and
  `marivo.semantic.authoring_check.check_authoring_inputs(...)` signatures to
  accept `sources: Sequence[AuthoringSourceInput]` instead of
  `datasource`, `source`, and `columns`;
- column-existence checks to run per source role against that role's own
  source schema;
- status derivation for multi-source results (`blocked` dominates
  `needs_input`, which dominates `supported`);
- returned `AssessmentFact`, `AssessmentIssue`, and `AuthoringQuestion` payloads
  so source role and source identity remain visible to agents;
- tests and skill examples that call the old single-source
  `check_authoring_inputs(...)` signature.

The call is not a durable cache lookup. Re-assessing may re-inspect metadata or
bounded samples because datasource state can change. Implementations may keep a
short-lived in-process cache during one `SemanticProject` session, but cached
observations must never be treated as persisted project truth.

#### `AuthoringAssessment` DTO

```python
@dataclass(frozen=True)
class AuthoringAssessment:
    status: ReviewStatus
    facts: tuple[AssessmentFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]
```

`AuthoringAssessment.status` reuses the existing `ReviewStatus` vocabulary
used by `AssessmentResult`:

| Status | Meaning |
| --- | --- |
| `blocked` | A blocker issue or blocking question prevents authoring or handoff |
| `needs_input` | Required source context, semantic dependency, or user decision is missing but no blocker exists |
| `supported` | Required context is present and no blocking issue or question remains |

Status derivation:

- `blocked` when any issue has `severity="blocker"` or any question has
  `readiness_effect="blocks"`.
- `needs_input` when required context or a user decision is missing but no blocker exists.
- `supported` when required context is present and no blocking issue or
  question remains.

The API does **not** generate code. Code authoring is the agent's creative
decision — it has user prompts, knowledge bases, and business context that
the library does not.

### Internal `project.check_authoring_inputs(...)`

`check_authoring_inputs(...)` remains an implementation detail of
`assess_authoring(...)`, but its target shape is breaking and multi-source:

```python
def check_authoring_inputs(
    self,
    *,
    object_kind: AuthoringObjectKind,
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AssessmentResult:
```

The old `datasource`, `source`, and `columns` parameters are removed. Source
identity and physical columns are carried by each `AuthoringSourceInput`.
The old explicit `evidence_refs` and `ai_context` inputs are also removed:
the checker works from current inspection facts, and authored `ai_context` is
checked after reload against the actual object.

### `project.readiness(...)`

`readiness(...)` is the Phase 3 closeout API. It is the normal agent workflow
for deciding whether semantic refs can be handed to analysis.

```python
def readiness(
    self,
    *,
    refs: Iterable[str] | None = None,
    demand: DemandSignal | None = None,
    preview_limit: int = 20,
    parity_rel_tol: float | None = None,
    parity_abs_tol: float | None = None,
    redact: bool = True,
) -> ReadinessReport:
```

When `refs` is `None`, checks all loaded handoff refs. When `refs` is
provided, checks only the specified refs and their required dependencies.
`readiness(...)` uses the project's bound backend access. It must not accept a
per-call `backend_factory`; callers configure backend access before Phase 3.
If no backend access is bound, the report is blocked.

Internal orchestration:

1. `project.reload()`.
2. Resolve every requested ref and dependency from the loaded registry.
3. Run required backend previews/materialization for datasets, fields,
   time-fields, and metrics, using `preview_limit` and `redact`.
4. Run eligible metric parity checks using `parity_rel_tol` and
   `parity_abs_tol`.
5. Run readiness structural, provenance, and decision-ledger checks.
6. Run richness checks using `demand`.
7. Return one `ReadinessReport`.

The report must not collapse runtime detail into one opaque status. Preview,
parity, readiness, and richness findings must carry refs, severity, rule or
finding kind, and a repair hint.

#### `ReadinessReport` closeout fields

```python
@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["ready", "ready_with_warnings", "blocked"]
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    preview_summary: PreviewSummary
    parity_summary: ParitySummary
    richness_summary: RichnessSummary
    checked_at: str
```

Fields:

| Field | Content |
| --- | ------- |
| `status` | Aggregate closeout status |
| `analysis_ready_refs` | Refs without blockers |
| `blockers` | Hard failures that can make analysis fail |
| `warnings` | Non-blocking parity, richness, enrichment, provenance, or advisory findings |
| `input_summary` | Datasources, refs, metadata, and decision-ledger records used by the report |
| `preview_summary` | Required, completed, and failed backend previews |
| `parity_summary` | Eligible, verified, drifted, unverified, unsupported, and skipped metric parity refs |
| `richness_summary` | Advisory richness gaps folded into readiness warnings |

Status derivation:

- `blocked` when any blocker exists.
- `ready_with_warnings` when there are no blockers but at least one warning
  exists.
- `ready` when there are no blockers and no warnings.

Only hard operability failures become blockers. Parity and richness findings
are warnings even when parity drifts from `source_sql` or richness identifies
missing coverage.

## NextCheck Removal

The `NextCheck` type alias and the `next_checks` fields on
`AssessmentIssue` and `AssessmentResult` are removed from the public API.

`NextCheck` was a choreography hint — "the agent should call this method
next" — but `assess_authoring` composes the static assessment checks that used
to drive those hints, and `readiness(...)` composes Phase 3 closeout checks.
The agent no longer needs to dispatch on `next_checks`.

This is a breaking DTO change for every consumer of `AssessmentIssue` or
`AssessmentResult`, including `inspect_authored_object(...)` callers. The
implementation work must update:

- authoring-check code paths that currently populate `next_checks`;
- tests and skill examples that inspect `AssessmentResult.next_checks` or
  `AssessmentIssue.next_checks`.

If an issue needs a repair hint in the future, use a `suggested_action: str`
field (human-readable) rather than an agent-dispatchable enum.

## API Classification

### Stay public

These APIs have independent use cases beyond the authoring pipeline:

| API | Reason |
| --- | ------ |
| `inspect_source_context` | Standalone source exploration without authoring |
| `inspect_column_context` | Standalone column inspection |
| `inspect_authored_object` | Debugging helper for post-reload static inspection; readiness calls the equivalent checks during closeout |
| `preview_dataset` | Debugging helper for inspecting bounded runtime rows; readiness runs required previews during closeout |
| `preview_field` | Debugging helper for inspecting bounded runtime rows; readiness runs required previews during closeout |
| `preview_metric` | Debugging helper for inspecting bounded runtime values; readiness runs required previews during closeout |
| `parity_check` | Debugging helper for inspecting SQL-parity detail; readiness runs eligible parity checks and reports findings as warnings |
| `readiness` | Single closeout and analysis-handoff gate |
| `richness` | Debugging/advisory helper for richness-only reports; readiness folds richness gaps into warnings |
| `assess_authoring` | New composed API |

### Become internal

These APIs are implementation details of the composed static assessment flow and
no longer need to be part of the agent-facing authoring workflow:

| API | Called by |
| --- | --------- |
| `check_authoring_inputs` | `assess_authoring` |
| `collect_source_preview` | Removed from the normal authoring and readiness workflow; readiness runs live required previews |
| `record_authoring_evidence`, `list_evidence`, `get_evidence_pack` | Removed from the normal authoring API; use authored object fields and decision-ledger records instead |

"Internal" means: removed from the skill's authoring workflow and from the
public `SemanticProject` documentation aimed at agents. The methods may still
exist on `SemanticProject` for direct use in debugging or advanced scenarios,
but the skill does not direct agents to call them during normal authoring.

## Superseded Sections

| Source document | Superseded section | Replacement |
| --- | --- | --- |
| `skill-semantic-layer-authoring-design.md` | "Skill Workflow" (9 stages) | Three-phase flow in this document |
| `skill-semantic-layer-authoring-design.md` | `NextCheck` type and `next_checks` semantics | Removed; `assess_authoring` composes internally |
| `skill-semantic-layer-authoring-design.md` | "Light Authoring Input Check" as a standalone step | `assess_authoring` composes it |
| `skill-semantic-layer-authoring-design.md` | Multi-file authoring examples | Single `_model.py` authoring file |
| `agent-semantic-layer-authoring-design.md` | Phase 0 loop | Three-phase flow in this document |
| `python-semantic-layer.md` | General multi-file organization recommendation for agent-authored models | Single `_model.py` authoring file for this pipeline |

Sections of those documents covering persisted evidence stores or evidence-ref
choreography are superseded by this design. Sections covering provenance,
decision-ledger requirements, and anti-goals remain authoritative unless they
conflict with this document.

## Interaction Summary

The complete agent interaction removes manual static-check and runtime-check
choreography:

1. **Phase 1:** `inspect_source_context` + optional `inspect_column_context`
   (per source, not per object)
2. **Phase 2:** `assess_authoring` per candidate object + file write (no
   reload)
3. **Phase 3:** `readiness` for reload, ref checks, required backend previews,
   parity warnings, richness warnings, and handoff status

For a typical model with 3 datasets, 6 fields, 3 metrics, and 2
relationships, the current flow requires many individual API calls with
manual static orchestration. The target flow requires `assess_authoring` per
candidate object and one `readiness(...)` closeout call.
