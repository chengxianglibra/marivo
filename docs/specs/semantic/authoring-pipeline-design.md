# Authoring Pipeline Design

Status: draft design.

This document defines the target-state authoring pipeline for Marivo semantic
layer construction. It replaces the `NextCheck`-driven choreography with a
three-phase authoring loop backed by one composed static-assessment API and
explicit runtime validation.

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

2. **No data flow between steps.** `inspect_source_context` returns a
   `SourceEvidencePack`; the agent must extract schema, column profiles, and
   evidence refs from it and manually feed them into
   `check_authoring_inputs(columns=...)`,
   `inspect_column_context(columns=...)`, and `record_authoring_evidence(...)`.
   Method signatures do not accept prior-step outputs as inputs.

3. **Asymmetric write step.** `"write_semantic_python"` has no corresponding
   `SemanticProject` method — it is an instruction for the agent to write a
   file. The 12 API-call steps are interleaved with a non-API step, splitting
   the pipeline into pre-write and post-write halves with no structural
   acknowledgment of that split.

4. **Branch logic in prose.** `check_authoring_inputs` may return `blocked`,
   `needs_evidence`, or `supported`. Each status requires different handling
   (supplement evidence → re-check → write → reload), but the branching is
   described only in skill markdown, not in executable code.

5. **Skill as orchestrator.** The 16 non-negotiable rules and 9-stage workflow
   in the skill document serve as the de facto pipeline orchestrator. If the
   skill document and the API drift apart, there is no compile-time or
   runtime signal.

## Core Principle

The project API provides facts and assessments. The agent provides creative
decisions — what to author, how to name it, what business definition to write.
The pipeline should compose the API's static evidence checks into fewer,
larger operations that the agent calls at natural decision points. Runtime
checks that execute previews or parity stay explicit so agents can control
backend cost, limits, tolerances, and blocker attribution.

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
pack = project.inspect_source_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders", database="sales_mart"),
    sample_policy=ms.BoundedProfilePolicy(limit=100, max_profiled_columns=50),
)
```

3. The agent ranks columns from pack facts (type, comments, nullable,
   partition hints, sampled values). Deep-dive a small set if needed:

```python
evidence = project.inspect_column_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
    columns=("status", "amount"),
    sample_policy=ms.SelectedColumnsPolicy(limit=100, columns=("status", "amount")),
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

**2.1.0 Collect source evidence** — skip if a pack with matching structural
fingerprint already exists.

**2.1.1 Assess each candidate object** — the agent calls
`project.assess_authoring(...)` for each candidate. The API internally
orchestrates evidence checks and returns an `AuthoringAssessment`:

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
    evidence=ms.AuthoringEvidenceInput(
        kind="source_sql",
        subject_refs=("sales.revenue",),
        content="SELECT SUM(amount) AS revenue FROM orders WHERE paid",
        source_dialect="trino",
    ),
    ai_context=ms.AiContextInput(business_definition="Paid order revenue."),
)

if assessment.status == "blocked":
    # resolve blockers from assessment.issues, then re-assess
    pass
elif assessment.status == "needs_evidence":
    # ask user about assessment.questions, supplement evidence, re-assess
    pass
# status == "supported" → proceed to write
```

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
               granularity="day", date_format="yyyymmdd")
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
`AuthoringSourceInput` per physical source role. A relationship must cite both
the from-side and to-side source evidence before it can be ready:

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
    evidence_refs=(relationship_confirmation_ref.id,),
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
    evidence_refs=(metric_sql_ref.id,),
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

All semantic objects are validated after all code is written. Runtime checks
stay explicit in the agent workflow because they execute backend work and need
caller-controlled limits, tolerances, and retry scope.

```python
project.reload()

project.inspect_authored_object("sales.revenue")
project.preview_dataset("sales.orders", limit=20)
project.preview_field("sales.orders.region", limit=20)
project.preview_metric("sales.revenue", limit=20)
project.parity_check("sales.revenue", rel_tol=1e-6)
report = project.readiness(refs=("sales.revenue", "sales.orders"))
richness = project.richness()
```

Validation closeout rules:

- load or authored-object blockers return the agent to Phase 2;
- preview failures block the affected ref, with the failed check reported per
  ref;
- parity drift blocks metrics whose `verification_mode="sql_parity"`;
- readiness blockers prevent analysis handoff;
- richness is advisory and never blocks handoff.

`project.verify_authoring(...)` may exist as a convenience batch API, but it is
not the primary agent workflow. If exposed, it must accept the same runtime
controls as the narrow APIs and return per-ref `skipped`, `failed`, and
`degraded` results rather than hiding them behind one aggregate status.

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
    evidence: AuthoringEvidenceInput | None = None,
    evidence_refs: Sequence[str] = (),
    ai_context: AiContextInput | None = None,
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
    evidence_refs: tuple[str, ...] = ()
```

`sources=()` is allowed only for derived metrics or other objects whose
evidence is entirely semantic-ref based. Dataset, field, time-field, base
metric, and relationship assessment must include the relevant physical source
roles.

Source roles are interpreted as follows:

| Role | Use |
| --- | --- |
| `primary` | A source directly used by a dataset, field, time field, or base metric; cross-dataset base metrics pass one `primary` role per participating dataset |
| `from` | Relationship from-side source |
| `to` | Relationship to-side source |
| `component` | Source evidence for a metric component when the assessed object is component-driven but not a pure derived metric |

If an agent needs a non-default sample policy, it should pre-collect evidence
in Phase 1 with `inspect_source_context(..., sample_policy=...)`. For example,
metadata-only or larger-sample workflows first warm the evidence store with the
desired policy, then `assess_authoring(...)` reuses the current matching pack.

Parameters:

| Parameter | Purpose |
| --- | ------- |
| `object_kind` | The kind of semantic object being assessed |
| `subject_ref` | Target semantic id using the current implementation format (e.g. `"sales.revenue"` or `"sales.orders.region"`) |
| `sources` | Physical source roles and columns that support the target object |
| `semantic_refs` | Semantic refs the target depends on (e.g. dataset ref for a metric, both dataset refs and join-key field refs for a relationship) |
| `evidence` | Optional single evidence input to record before checking |
| `evidence_refs` | IDs of previously recorded evidence to cite in the check |
| `ai_context` | Intended `ai_context` for the target object |

`evidence` and `evidence_refs` serve different use cases:
- `evidence` is a convenience for recording a single piece of evidence
  (typically `source_sql` or `user_confirmation`) inline with the assessment.
- `evidence_refs` is for citing previously recorded evidence — for example,
  when a metric needs both source SQL and user confirmation, the agent
  records each separately and then passes both IDs.
- `AuthoringSourceInput.evidence_refs` cites evidence that supports a specific
  physical source role, such as source metadata or a source-specific owner
  note. Top-level `evidence_refs` cites evidence about the target semantic
  object, such as metric formula SQL or relationship intent.

Internal orchestration:

1. For each `AuthoringSourceInput`, check the evidence store for an existing
   `SourceEvidencePack` matching `datasource` + `source`. If none exists, call
   `inspect_source_context` with
   `BoundedProfilePolicy(limit=100, max_profiled_columns=50)`.
2. For each source role, if `columns` is non-empty and no column evidence
   exists, call `inspect_column_context` for those columns.
3. If `evidence` is provided, call `record_authoring_evidence` and add the
   returned ref id to `evidence_refs`.
4. Call the breaking multi-source `check_authoring_inputs(...)` with the full
   `sources` tuple, the full `semantic_refs` tuple, and the union of
   top-level `evidence_refs`, per-source `evidence_refs`, and any evidence
   recorded in step 3.
5. Return `AuthoringAssessment`.

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
  `needs_evidence`, which dominates `supported`);
- returned `EvidenceFact`, `AssessmentIssue`, and `AuthoringQuestion` payloads
  so source role and source identity remain visible to agents;
- tests and skill examples that call the old single-source
  `check_authoring_inputs(...)` signature.

The call is idempotent only when cached evidence matches the current structural
fingerprint. If the API cannot prove that match, it must refresh source
evidence or return a stale-evidence issue rather than silently accepting the
old pack. Re-assessing after resolving a blocker should be cheap when the
evidence refs are still current.

#### `AuthoringAssessment` DTO

```python
@dataclass(frozen=True)
class AuthoringAssessment:
    status: ReviewStatus
    packs: tuple[SourceEvidencePack, ...]
    column_evidence: tuple[ColumnEvidence, ...]
    facts: tuple[EvidenceFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]
```

`AuthoringAssessment.status` reuses the existing `ReviewStatus` vocabulary
used by `AssessmentResult`:

| Status | Meaning |
| --- | --- |
| `blocked` | A blocker issue or blocking question prevents authoring or handoff |
| `needs_evidence` | Required evidence is missing but no blocker exists |
| `supported` | Required evidence is present and no blocking issue or question remains |

Status derivation:

- `blocked` when any issue has `severity="blocker"` or any question has
  `readiness_effect="blocks"`.
- `needs_evidence` when required evidence is missing but no blocker exists.
- `supported` when required evidence is present and no blocking issue or
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
    evidence_refs: Sequence[str] = (),
    ai_context: AiContextInput | None = None,
) -> AssessmentResult:
```

The old `datasource`, `source`, and `columns` parameters are removed. Source
identity and physical columns are carried by each `AuthoringSourceInput`.

### Optional `project.verify_authoring(...)`

`verify_authoring(...)` is optional convenience, not the normal agent
workflow. Agents should prefer the explicit runtime calls in Phase 3 when they
need to tune preview limits, parity tolerances, redaction, backend access, or
retry scope.

```python
def verify_authoring(
    self,
    *,
    refs: Iterable[str] | None = None,
    preview_limit: int = 20,
    parity_rel_tol: float | None = None,
    parity_abs_tol: float | None = None,
    redact: bool = True,
) -> AuthoringVerification:
```

When `refs` is `None`, validates all loaded objects. When `refs` is provided,
validates only the specified objects.

Internal orchestration:

1. `project.reload()`.
2. For each target ref, `inspect_authored_object`.
3. For each target ref, `preview_*` by object kind.
4. For metrics with `source_sql`, `parity_check`.
5. `project.readiness(refs=...)`.
6. `project.richness()`.
7. Return `AuthoringVerification`.

The batch API must not collapse runtime detail into one opaque result. Every
preview, parity, readiness, and richness item must carry a ref, status, and
failure or skipped reason.

#### `AuthoringVerification` DTO

```python
@dataclass(frozen=True)
class RuntimeCheckResult:
    ref: str
    status: Literal["passed", "warning", "failed", "skipped", "degraded"]
    result: object | None
    reason: str | None = None


@dataclass(frozen=True)
class AuthoringVerification:
    status: Literal["verified", "warnings", "failed"]
    readiness: ReadinessReport
    richness: RichnessReport
    issues: tuple[AssessmentIssue, ...]
    preview_results: tuple[RuntimeCheckResult, ...]
    parity_results: tuple[RuntimeCheckResult, ...]
```

Fields:

| Field | Content |
| --- | ------- |
| `status` | Aggregate verification status (see below) |
| `readiness` | Full readiness report with blockers and warnings |
| `richness` | Advisory richness report (never affects status) |
| `issues` | Issues from `inspect_authored_object` for all target refs |
| `preview_results` | Preview check results; each item records ref, status, result, and failure or skipped reason |
| `parity_results` | Parity check results; each item records ref, status, result, and failure or skipped reason |

Status derivation:

- `failed` when any `issues` have `severity="blocker"` or `readiness`
  reports any blocker.
- `warnings` when there are no blockers but `readiness` has warnings or
  `issues` have `severity="warning"`.
- `verified` when there are no blockers and no warnings.

## NextCheck Removal

The `NextCheck` type alias and the `next_checks` fields on
`AssessmentIssue` and `AssessmentResult` are removed from the public API.

`NextCheck` was a choreography hint — "the agent should call this method
next" — but `assess_authoring` composes the static evidence checks that used
to drive those hints. Runtime checks remain explicit Phase 3 calls. The agent
no longer needs to dispatch on `next_checks`.

This is a breaking DTO change for every consumer of `AssessmentIssue` or
`AssessmentResult`, including `inspect_authored_object(...)` callers. The
implementation work must update:

- authoring-check code paths that currently populate `next_checks`;
- evidence-store serialization and deserialization for stored assessment
  issues;
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
| `record_authoring_evidence` | Evidence recording outside assess_authoring |
| `inspect_authored_object` | Explicit post-reload static inspection |
| `preview_dataset` | Explicit runtime validation with caller-controlled limits |
| `preview_field` | Explicit runtime validation with caller-controlled limits |
| `preview_metric` | Explicit runtime validation with caller-controlled limits |
| `parity_check` | Explicit SQL-parity validation with caller-controlled tolerances |
| `readiness` | Independent readiness checks |
| `richness` | Independent richness reporting |
| `assess_authoring` | New composed API |
| `verify_authoring` | Optional batch convenience API, not the primary agent workflow |

### Become internal

These APIs are implementation details of the composed static evidence flow and
no longer need to be part of the agent-facing authoring workflow:

| API | Called by |
| --- | --------- |
| `check_authoring_inputs` | `assess_authoring` |
| `collect_source_preview` | `inspect_source_context` during normal authoring |

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

Sections of those documents covering evidence DTOs, evidence lifecycle,
sufficiency rules, provenance, and anti-goals are **not** superseded and
remain authoritative.

## Interaction Summary

The complete agent interaction keeps runtime checks explicit while removing
manual static-check choreography:

1. **Phase 1:** `inspect_source_context` + optional `inspect_column_context`
   (per source, not per object)
2. **Phase 2:** `assess_authoring` per candidate object + file write (no
   reload)
3. **Phase 3:** `reload`, `inspect_authored_object`, targeted
   `preview_*`, targeted `parity_check`, `readiness`, and `richness`

For a typical model with 3 datasets, 6 fields, 3 metrics, and 2
relationships, the current flow requires many individual API calls with
manual static orchestration. The target flow requires `assess_authoring` per
candidate object and keeps runtime validation at explicit, natural decision
points.
