# Skill / Semantic Layer Authoring Overall Design

Status: draft target design, v1 scoped.

This document defines the joint target design for the Marivo semantic
authoring skill and the `SemanticProject` APIs that support it. The design is
intentionally breaking where the old candidate/classifier workflow gets in the
way. It does not define compatibility shims or data migration for
`propose_candidates(...)` / `open_questions(...)`.

It complements `docs/specs/semantic/python-semantic-layer.md` and
`docs/specs/semantic/agent-semantic-layer-authoring-design.md`. Where this
document conflicts with the current authoring workflow in those documents, this
document is the target-state replacement for skill-driven construction. The
Python semantic object model remains owned by
`python-semantic-layer.md`.

## Thesis

The project API should not be an automatic semantic model generator and should
not become a second semantic DSL. Its main job is to give the agent enough
reliable source, column, sample, provenance, and validation evidence to author
Python semantic objects well.

The desired authoring loop is:

```text
skill chooses the current authoring stage
  -> agent calls a project evidence API
  -> project returns facts, profiles, provenance, and explicit missing evidence
  -> agent writes or revises Python semantic objects
  -> agent reloads the project and runs explicit preview/parity/readiness checks
  -> agent asks the user only when evidence cannot settle business meaning
```

This design optimizes for clear evidence boundaries. Weak structural signals
must never look like instructions, and project APIs must not output
recommendations that are really business interpretation.

## V1 Scope

V1 keeps the useful construction support and cuts the parts that are
theoretically clean but expensive to implement or weak in practice.

V1 includes:

- source and column evidence collection;
- bounded sample profiles with explicit sample scope;
- persisted evidence metadata for multi-process agent runs;
- a single lightweight authoring input check;
- cheap authored-object inspection after reload;
- explicit use of existing preview, parity, readiness, and richness APIs.

V1 does not include:

- per-object draft DTOs that mirror the semantic Python DSL;
- per-object `assess_dataset_draft(...)`,
  `assess_time_field_draft(...)`, `assess_field_draft(...)`,
  `assess_metric_draft(...)`, or `assess_relationship_draft(...)`;
- public `Hypothesis` or candidate-like worklist DTOs;
- a replacement typed evidence graph for every ledger decision;
- a mini-readiness API that hides backend preview, parity, or relationship
  diagnostics behind one broad `review_authored_object(...)` call;
- mandatory relationship orphan, fanout, or referential-integrity scans.

## Superseded Current Surfaces

Target-state semantic authoring removes the current candidate/classifier
workflow from the public skill path.

| Current surface | Target status | Replacement |
| --- | --- | --- |
| `project.propose_candidates(...)` | Remove from authoring workflow | `inspect_source_context(...)` and `inspect_column_context(...)` return facts; the skill chooses what to author |
| `project.open_questions(candidates=...)` | Remove from candidate workflow | `AuthoringQuestion` entries returned only by evidence checks when a real business decision is missing |
| `Candidate`, `ProposalResult`, `ResidualColumn` as skill worklist types | Remove | `SourceEvidencePack`, `ColumnEvidence`, `AssessmentResult` |
| `project.collect_source_preview(...)` as a separate authoring step | Fold into source inspection | `inspect_source_context(..., sample_policy=...)` persists preview evidence metadata |
| `mv.datasources.inspect_source(...)` as a skill step | Internal injected dependency | `inspect_source_context(..., inspect_source=...)` |
| `project.audit(...)` as authoring revalidation | Remove from the main workflow | cheap authored-object inspection plus final `readiness(...)` |

`preview_dataset(...)`, `preview_field(...)`, `preview_metric(...)`,
`parity_check(...)`, `readiness(...)`, and `richness(...)` remain explicit
runtime and handoff APIs. They should not be hidden behind a broad review API.

## Core Boundary

Project APIs provide:

- facts from metadata, samples, existing semantic objects, source SQL,
  knowledge documents, and user confirmations;
- provenance for every fact, including source, sample policy, limits, warnings,
  redaction, and truncation status;
- rule-based checks where each rule is explicit and auditable;
- missing-evidence reports that explain why authoring input is not yet
  supported;
- machine-readable questions for unresolved business decisions.

The skill and agent provide:

- workflow order and stopping rules;
- interpretation of evidence in the user task context;
- decisions about which semantic objects to create or reuse;
- `description`, `ai_context.business_definition`, guardrails, synonyms, and
  examples;
- user questions when business meaning is not supported by evidence;
- edits to `.marivo/semantic/<model>/_model.py`;
- explicit runtime validation through preview, parity, readiness, and richness.

The project API should only output what evidence can support. If a conclusion
requires business interpretation, the API reports available evidence and the
missing evidence rather than presenting the conclusion as a suggestion.

## Output Levels

Project API output has two public levels. Consumers must not conflate them.

| Level | Meaning | Default behavior |
| --- | --- | --- |
| Fact | Directly observed or read from a source | Always returned when collected |
| Assessment | Transparent rule applied to facts | Returned with `rule_id` and evidence refs |

Examples of facts:

- table and column names;
- physical namespace;
- Ibis types;
- table and column comments;
- nullable flags;
- partition hints;
- bounded sample summaries with explicit sample scope;
- sampled top values for enum-like columns;
- sampled numeric min/max/null counts;
- sampled time ranges and parse formats.

Sample-derived values are facts about the bounded sample, not facts about the
full table. They must carry `sample_scope` and must not be used as complete
cardinality, min/max, or enum proof.

Examples of assessments:

- an authoring input references a missing physical column;
- a time-field input lacks evidence for its declared format;
- a metric input lacks source SQL, BI definition, or `python_native` decision
  evidence;
- a relationship input lacks join, documentation, metadata, or user-confirmed
  relationship intent;
- a dataset is reachable but has no bounded raw preview evidence;
- a handoff ref lacks `ai_context.business_definition`.

Weak signals may exist as private implementation details, but V1 does not
define a public `Hypothesis` DTO. If a source pack says a column is
`time_column_review` or `measure_review`, agents will treat it like a
candidate worklist. The skill should rank columns from facts instead.
`info` severity is for non-blocking evidence state about authored objects or
evidence packs. It must not be used to smuggle candidate suggestions such as
"consider authoring this column" back into the public API.

## Typed DTOs

All new APIs return stable dataclasses. The names below are the target public
shape; implementations may add fields only when they are optional and
JSON-safe.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvidenceKind = Literal[
    "catalog_metadata",
    "table_comment",
    "column_comment",
    "schema",
    "raw_preview_profile",
    "source_sql",
    "knowledge_document",
    "user_confirmation",
]

Severity = Literal["blocker", "warning", "info"]

IssueKind = Literal[
    "missing_evidence",
    "stale_metadata_evidence",
    "missing_source",
    "missing_column",
    "static_check_failed",
    "authored_object_invalid",
]

ReviewStatus = Literal[
    "supported",
    "needs_evidence",
    "blocked",
]

SourceKind = Literal["table", "file"]

AuthoringObjectKind = Literal[
    "dataset",
    "field",
    "time_field",
    "metric",
    "relationship",
]

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

@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: EvidenceKind
    datasource: str | None
    source: "DatasetSource | None"
    collected_at: str
    structural_fingerprint: str | None = None
    content_fingerprint: str | None = None

@dataclass(frozen=True)
class DatasetSource:
    kind: SourceKind
    table: str | None = None
    database: str | tuple[str, ...] | None = None
    path: str | None = None
    format: str | None = None

@dataclass(frozen=True)
class SamplePolicy:
    mode: Literal["metadata_only", "bounded_profile", "selected_columns_profile"]
    limit: int | None = None
    columns: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    redact: bool = True

@dataclass(frozen=True)
class AiContextInput:
    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None

@dataclass(frozen=True)
class EvidenceFact:
    id: str
    label: str
    value: object
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class ColumnProfile:
    column: str
    data_type: str
    nullable: bool | None
    comment: str | None
    null_count: int | None = None
    empty_count: int | None = None
    distinct_count: int | None = None
    top_values: tuple[tuple[object, int], ...] = ()
    min_value: object | None = None
    max_value: object | None = None
    observed_formats: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    sample_scope: Literal["none", "bounded_sample"] = "bounded_sample"
    approximate: bool = True

@dataclass(frozen=True)
class SourceEvidencePack:
    datasource: str
    source: DatasetSource
    schema: tuple[tuple[str, str], ...]
    table_comment: str | None
    column_comments: tuple[tuple[str, str], ...]
    nullable: tuple[tuple[str, bool | None], ...]
    partition_hints: tuple[str, ...]
    key_hints: tuple[tuple[str, ...], ...]
    column_profiles: tuple[ColumnProfile, ...]
    metadata_warnings: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    sample_policy: SamplePolicy
    redaction_status: Literal["redacted", "not_redacted"]
    truncated: bool

@dataclass(frozen=True)
class ColumnEvidence:
    datasource: str
    source: DatasetSource
    column: str
    profile: ColumnProfile
    issues: tuple["AssessmentIssue", ...] = ()
    evidence_refs: tuple[str, ...] = ()

@dataclass(frozen=True)
class AssessmentIssue:
    kind: IssueKind
    severity: Severity
    refs: tuple[str, ...]
    message: str
    rule_id: str
    evidence_refs: tuple[str, ...]
    next_checks: tuple[NextCheck, ...] = ()

@dataclass(frozen=True)
class AuthoringQuestion:
    id: str
    decision_kind: str
    subject_refs: tuple[str, ...]
    prompt: str
    reason: str
    evidence_refs: tuple[str, ...]
    options: tuple[str, ...] = ()
    default_option: str | None = None
    readiness_effect: Literal["blocks", "warns", "advisory"] = "blocks"

@dataclass(frozen=True)
class AssessmentResult:
    status: ReviewStatus
    facts: tuple[EvidenceFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]
    next_checks: tuple[NextCheck, ...] = ()

@dataclass(frozen=True)
class AuthoringEvidenceInput:
    kind: Literal["source_sql", "knowledge_document", "user_confirmation"]
    subject_refs: tuple[str, ...]
    content: str
    source_document: str | None = None
    source_dialect: str | None = None
    content_fingerprint: str | None = None
```

`next_checks` is an enum, not free text. Agents should branch on it without
string parsing.

`AssessmentResult.status` is derived from issues and questions:

- `blocked` when any issue has `severity="blocker"` or any unanswered question
  has `readiness_effect="blocks"`;
- `needs_evidence` when required evidence is missing but no blocker has been
  reached yet;
- `supported` when required evidence is present and no blocking issue or
  blocking question remains.

## Evidence Lifecycle

Evidence ids are generated by project APIs, not handwritten by agents. The id
format is opaque except that it must be stable within the project until the
underlying structural or content evidence is refreshed.

Project evidence is persisted under `.marivo/semantic/.evidence/` as metadata
records. Raw rows are not persisted. The persisted record stores:

- the `EvidenceRef`;
- source identity;
- collection parameters such as `SamplePolicy`;
- redaction and truncation status;
- structural fingerprints for metadata, comments, schema, and source identity;
- content fingerprints for source SQL, knowledge documents, and user
  confirmations;
- bounded profile summaries when they are safe and JSON-serializable.

Only structural and content fingerprints participate in stale-decision checks.
Bounded profile changes are expected on live data and should not invalidate
object-level authoring decisions. Profile summaries are inspection evidence,
not a durable proof of business semantics.

Agents commonly run the skill as multiple short Python subprocesses. They are
not expected to keep evidence ids in memory. The project therefore exposes
simple retrieval APIs:

```python
project.list_evidence(
    datasource="warehouse",
    source=DatasetSource(kind="table", table="orders", database="sales_mart"),
) -> tuple[EvidenceRef, ...]

project.list_evidence(subject_refs=("sales.revenue",)) -> tuple[EvidenceRef, ...]

project.get_evidence_pack(evidence_id: str) -> SourceEvidencePack | ColumnEvidence | None
```

`list_evidence(...)` supports source-keyed lookup for source and column packs
and subject-ref lookup for source SQL, knowledge documents, owner notes, and
user confirmations. V1 should not hide evidence lookup behind implicit
auto-resolution. If an assessment lacks required evidence, it should return
`needs_evidence` with `next_checks`, not silently guess which persisted pack the
caller meant.

## Static vs Runtime Checks

V1 has one static check surface: `check_authoring_inputs(...)`. It checks
whether the information the agent is about to use is supported by known source
and knowledge evidence. It does not model complete semantic objects and does
not parse executable Python.

Static checks can verify:

- referenced datasource and source identity;
- referenced physical columns;
- type, nullable, partition, comment, and bounded profile evidence;
- `ai_context` presence and schema;
- whether a metric authoring input cites source SQL, BI definition, user
  confirmation, or an explicit `python_native` decision;
- whether the metric's referenced physical columns exist;
- whether relationship intent evidence exists;
- whether business claims cite adequate evidence.

Static checks are evidence and completeness checks. They do not prove that an
opaque metric formula string or field expression is executable or correct.
They also do not validate metric decomposition, filter semantics, units, or
sign conventions unless those are explicitly present in cited source SQL,
documentation, owner notes, or user confirmation. Decomposition remains an
authored semantic property that is auto-recorded after reload.

Runtime checks remain explicit and happen after the agent writes semantic
Python and reloads the project:

- `project.preview_dataset(...)`;
- `project.preview_field(...)`;
- `project.preview_metric(...)`;
- `project.parity_check(...)`;
- `project.readiness(...)`;
- `project.richness(...)`.

## Necessary Evidence

The API reports sufficiency by claim. Sufficiency means enough evidence for a
specific claim, not enough information to author everything automatically.

Dataset evidence is sufficient when:

- the physical source exists;
- the datasource is reachable when live access is required;
- schema and source identity are known;
- the source can be referenced with `ms.table(...)` or `ms.file(...)`.

Field evidence is sufficient when:

- the physical column or static expression inputs exist;
- the field meaning is non-business structural metadata, or business meaning is
  supported by comments, documentation, source SQL, or user confirmation.

Time-field evidence is sufficient when:

- the physical value exists;
- type or samples support the declared temporal representation;
- partition or event-time role is supported by metadata, source SQL,
  documentation, or user confirmation;
- required format fields such as `date_format` or `required_prefix` are
  justified by observed values.

Metric evidence is sufficient when:

- the intended formula is documented by source SQL, BI definition, code owner
  confirmation, or explicit `python_native` decision;
- referenced physical columns exist;
- material filters, exclusions, units, and sign conventions are documented
  when they are known from source SQL, BI definitions, owner notes, or user
  confirmation.

Relationship evidence is sufficient when:

- relationship intent comes from metadata, source SQL joins, documentation, or
  user confirmation;
- each side's key fields have source or semantic evidence collected before
  authoring.

The V1 static check does not compare two physical sources in one call. It does
not prove cross-source key compatibility. If key compatibility matters, collect
column evidence for both sides and run an explicit optional diagnostic.

Business definition evidence is sufficient when it comes from comments,
documentation, BI definitions, source SQL, owner notes, or user confirmation.
Preview data alone is not sufficient to prove business meaning.

## Project APIs

The target API is organized around source evidence, column evidence, lightweight
checks, and explicit runtime validation.

### Source Evidence

```python
project.inspect_source_context(
    datasource="warehouse",
    source=DatasetSource(kind="table", table="orders", database="sales_mart"),
    inspect_source=mv.datasources.inspect_source,
    backend_factory=mv.datasources.build_backend,
    sample_policy=SamplePolicy(
        mode="bounded_profile",
        limit=100,
        timeout_seconds=30,
        max_profiled_columns=50,
        redact=True,
    ),
) -> SourceEvidencePack
```

`marivo.semantic` must not import `marivo.analysis`; live metadata and backend
access are injected.

`SourceEvidencePack` contains:

- `source`;
- `schema`;
- `table_comment`;
- `column_comments`;
- `nullable`;
- `partition_hints`;
- `key_hints`;
- `column_profiles`;
- `metadata_warnings`;
- `evidence_refs`;
- `sample_policy`;
- `redaction_status`;
- `truncated`.

This API folds the current separate `inspect_source(...)` and
`collect_source_preview(...)` authoring steps into one evidence collection call.
It persists preview evidence metadata, not raw rows.

`SamplePolicy.mode` controls backend cost:

- `metadata_only` collects schema, comments, nullable flags, partition hints,
  and key hints without row profiling.
- `bounded_profile` collects bounded profiles up to `max_profiled_columns`;
  skipped columns are listed as warnings.
- `selected_columns_profile` profiles only `SamplePolicy.columns`.

`columns` must be non-empty for `selected_columns_profile` and must be empty
for `metadata_only` and `bounded_profile`. `limit` is required for modes that
read rows and ignored for `metadata_only`.

Implementations must enforce `limit`, `timeout_seconds`, and
`max_profiled_columns`. If a profile budget is exceeded, the API returns a
partial `SourceEvidencePack` with warnings rather than silently issuing
unbounded backend queries.

For bounded profiles, values such as `distinct_count`, `top_values`,
`min_value`, and `max_value` are scoped to the sample. Agents may use them to
decide what to inspect next, but not to prove full-column cardinality, complete
enum values, or global ranges.

When the agent bases a time format, enum, or low-cardinality decision on a
bounded sample, it should state that the decision is sample-supported and offer
the relevant optional diagnostic when out-of-sample variation would be risky.

### Column Evidence

```python
project.inspect_column_context(
    datasource="warehouse",
    source=DatasetSource(kind="table", table="orders", database="sales_mart"),
    columns=("amount", "status", "dt"),
    inspect_source=mv.datasources.inspect_source,
    backend_factory=mv.datasources.build_backend,
    sample_policy=SamplePolicy(
        mode="selected_columns_profile",
        limit=100,
        columns=("amount", "status", "dt"),
        timeout_seconds=30,
        redact=True,
    ),
) -> tuple[ColumnEvidence, ...]
```

This call is optional after `inspect_source_context(...)`. It is used when the
agent needs deeper evidence for a small set of columns, such as:

- possible time columns;
- enum/status/code columns;
- amount or unit-bearing columns;
- join-key columns;
- columns involved in a metric formula.

It should not repeat a full table profile unless requested.

### Knowledge And Source SQL Evidence

```python
source_sql_ref = project.record_authoring_evidence(
    AuthoringEvidenceInput(
        kind="source_sql",
        subject_refs=("sales.revenue",),
        content="select sum(amount) as revenue from orders where paid = 1",
        source_dialect="trino",
        source_document="bi://revenue-dashboard",
    )
)
```

This API records non-sample evidence such as source SQL, BI definitions,
knowledge documents, owner notes, or user confirmations. It returns an
`EvidenceRef` whose id can be used by checks and decision records.

### Light Authoring Input Check

```python
project.check_authoring_inputs(
    object_kind="metric",
    subject_ref="sales.revenue",
    datasource="warehouse",
    source=DatasetSource(kind="table", table="orders", database="sales_mart"),
    columns=("amount", "paid"),
    semantic_refs=("sales.orders",),
    evidence_refs=(source_sql_ref.id,),
    ai_context=AiContextInput(
        business_definition="Paid order revenue before refunds.",
        guardrails=("Excludes unpaid orders.",),
    ),
) -> AssessmentResult
```

`check_authoring_inputs(...)` is not a semantic declaration. It is a cheap
pre-authoring guardrail for refs, columns, and evidence. It should not attempt
to parse metric formulas, compile expressions, or infer business definitions.

For relationships, the V1 check verifies relationship-intent evidence and any
columns present on the provided source. It does not compare two physical
sources. Cross-source key compatibility, orphan rate, fanout, and
referential-integrity scans are optional diagnostics, not part of the main
authoring contract.

### Authored Object Inspection

```python
project.inspect_authored_object("sales.revenue") -> AssessmentResult
```

This runs after `project.reload()`. It is intentionally cheap and backend-free.
It can inspect:

- loaded registry data;
- required `ai_context` fields;
- known source and column evidence refs;
- object-level decision ledger state;
- whether dangerous authored objects have auto-recorded or manually recorded
  decisions.

It must not materialize Ibis tables, execute previews, run parity, or scan
relationship fanout. Those actions remain explicit runtime calls.

### Explicit Runtime Validation

Runtime validation uses existing narrow APIs:

```python
project.preview_dataset("sales.orders", limit=20, backend_factory=backend_factory)
project.preview_field("sales.order_date", limit=20, backend_factory=backend_factory)
project.preview_metric("sales.revenue", limit=20, backend_factory=backend_factory)
project.parity_check("sales.revenue", backend_factory=backend_factory)
project.readiness(backend_factory=backend_factory, refs=("sales.revenue",))
project.richness()
```

The skill should call only the runtime checks needed for the authored object
class and batch or defer expensive checks when backend cost matters.

### Questions And Ledger

Checks may return `AuthoringQuestion` objects when evidence cannot settle a
business decision. The agent asks the user only for these concrete unresolved
decisions.

V1 should not replace the object-level evidence ledger with a typed evidence
graph. Decision records remain object-level records. They may cite structural
or content fingerprints and qualifying sources, but bounded profile
fingerprints do not participate in stale-decision semantics.

Auto-record remains responsible for decisions that are fully implied by
authored semantic Python, such as `metric_decomposition` and
`time_field_identity`. If a thin `record_authoring_decision(...)` wrapper is
added, it should build the existing object-level decision record shape rather
than introduce a new ledger schema.

## Structural Triage For Wide Tables

There is one structural triage story: source evidence packs.

For wide tables, `inspect_source_context(...)` returns a column inventory with
facts. The skill uses that inventory to choose which columns deserve deeper
`inspect_column_context(...)` calls. The project does not return a candidate
worklist, and the agent should not call a separate `propose_candidates(...)`
path.

Column ranking belongs in the skill, not in the API contract. The skill can use
facts such as type, comments, nullable flags, primary-key hints, partition
hints, sampled formats, and sampled low-cardinality values to choose the next
columns to inspect.

## Skill Workflow

The semantic skill becomes a staged authoring runbook.

### 1. Project Discovery

The agent loads the project, lists existing models and refs, searches for
reuse, and describes candidate refs before adding new objects. No new object is
authored before reuse has been checked.

### 2. Source Evidence Collection

For each physical source, the agent calls `inspect_source_context(...)`. If the
source evidence is insufficient, the skill stops the flow and asks the agent to
fix datasource access or request missing source context.

### 3. Column Deep Dives

For the small set of columns that matter to authoring decisions, the agent calls
`inspect_column_context(...)`. This avoids profiling every column repeatedly and
keeps backend cost bounded.

### 4. Dataset Authoring

The agent checks source refs with `check_authoring_inputs(...)`, writes the
dataset Python declaration, reloads, and runs `inspect_authored_object(...)`.

### 5. Time Field Authoring

The agent authors a time field only after temporal evidence exists. If
partition time and business event time conflict, the check returns an
`AuthoringQuestion`.

### 6. Field Authoring

The agent authors fields worth exposing. The project checks physical support
and evidence sufficiency. It does not decide business dimensions from names
alone.

### 7. Metric Authoring

The agent authors metrics from explicit formulas, source SQL, BI definitions,
or confirmed business rules. Static checks only confirm supporting evidence.
Materialization, semantic preview, and parity happen after writing and reload.

### 8. Relationship Authoring

The agent authors relationships only from metadata, join SQL, documentation, or
user confirmation. Static checks can verify key existence and type
compatibility. Same-name columns alone are not sufficient. Orphan, fanout, and
referential-integrity scans are optional diagnostics.

### 9. Incremental Review And Closeout

For each authored object or object class:

- reload;
- inspect the authored object cheaply;
- run bounded runtime previews where appropriate;
- run parity where source SQL exists;
- record decisions only when evidence is sufficient;
- ask the user only for unresolved business decisions.

Final closeout still uses readiness, parity, and richness. Those APIs remain
handoff gates and advisory checks, not the main source of authoring context.

## Handling Questions

Questions are generated from missing evidence, not from generic candidate
uncertainty. A question includes:

- the semantic ref or authored object it affects;
- the exact decision needed;
- available evidence;
- why the evidence is insufficient;
- possible answers when safe to enumerate;
- the consequence for readiness or reuse.

The agent asks the user only when the decision cannot be resolved from
metadata, preview, source SQL, documentation, existing semantic objects, or
safe local checks.

## Optional Diagnostics

Some checks are useful in limited projects but should not be part of the core
authoring API:

- full-table aggregates for exact distinct counts, min/max, or enum coverage;
- relationship orphan-rate scans;
- relationship fanout scans;
- referential-integrity scans;
- deeper statistical profiling.

These should be explicit, opt-in diagnostics with clear cost controls. They
must not block normal semantic construction unless the user or project policy
requires them.

## Provenance And Persistence

Evidence returned by project APIs includes stable provenance:

- datasource and source identity;
- query or metadata source;
- sample limit and policy;
- redaction status;
- collection time;
- warning and truncation status;
- structural or content fingerprints as appropriate.

Raw sample rows are not persisted by default. Persist evidence metadata,
bounded summaries, and fingerprints, not sensitive row payloads. This preserves
cross-process usability without turning previews into hidden semantic state.

## Anti-Goals

- Do not make project APIs generate final business definitions.
- Do not make field names or comments alone sufficient for metric semantics.
- Do not expose weak heuristics as public suggestions or hypotheses.
- Do not promote optimization advice into hard validation unless it affects
  correctness or handoff safety.
- Do not make readiness or richness compensate for missing authoring evidence.
- Do not create a second semantic DSL outside `.marivo/semantic/<model>/`.
- Do not replace the object-level evidence ledger with a typed evidence graph
  in V1.
- Do not preserve the old candidate/classifier authoring workflow for
  compatibility.

## Implementation Slices

Because this is a breaking target design, implementation should replace old
authoring APIs rather than build compatibility shims. V1 should stay narrow:

1. Add `inspect_source_context(...)` as the primary source evidence API,
   including metadata injection, bounded preview metadata persistence, and
   machine-readable provenance.
2. Add `inspect_column_context(...)` for selected-column deep dives.
3. Add simple evidence retrieval with `list_evidence(...)` and
   `get_evidence_pack(...)`.
4. Add `record_authoring_evidence(...)` for source SQL, knowledge documents,
   owner notes, and user confirmations.
5. Add `check_authoring_inputs(...)` as the single static evidence check.
6. Add `inspect_authored_object(...)` as a cheap post-reload registry and
   ledger inspection API.
7. Rewrite the semantic skill so construction is driven by source/column
   evidence and explicit runtime checks.
8. Remove `propose_candidates(...)`, `open_questions(...)`, and `audit(...)`
   from the public authoring workflow.

Later, optional work can add expensive diagnostics or richer ledger provenance
only after V1 proves that agents actually need them during construction.

## Success Criteria

The design is successful when:

- an agent can build a semantic layer by following the skill without manually
  inventing metadata queries;
- project API output separates facts and assessments;
- every assessment cites evidence and the rule that produced it;
- weak signals never appear as authoritative suggestions;
- business definitions are written only from adequate knowledge evidence or
  user confirmation;
- the agent does not have to author a second draft semantic object before
  writing Python semantic declarations;
- runtime cost is explicit because preview, parity, readiness, and optional
  diagnostics remain separate calls;
- readiness remains the handoff gate, while evidence APIs become the primary
  construction support.
