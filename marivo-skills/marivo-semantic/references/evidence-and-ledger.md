# Evidence and Ledger

## Two Output Levels

- **Fact**: An observed, verifiable piece of evidence (column type, null count,
  sample top values). Facts carry `evidence_refs` linking back to the
  `SourceEvidencePack` or `AuthoringEvidenceInput` that produced them.
- **Assessment**: A rule-based evaluation of whether authoring can proceed.
  `AssessmentResult` contains `facts`, `issues`, and `questions`. Issues have
  severity (`blocker`/`warning`/`info`); questions represent unresolved
  business decisions.

## Evidence DTOs

| DTO | Purpose |
| --- | ------- |
| `DatasetSource` | Physical table or file source identity |
| `SamplePolicy` | Controls profiling mode and limits |
| `EvidenceRef` | Reference to collected authoring evidence |
| `EvidenceFact` | Single observed fact with evidence refs |
| `ColumnProfile` | Bounded-sample profile for one column |
| `SourceEvidencePack` | Collected facts and profiles for a source |
| `ColumnEvidence` | Deep-dive evidence for one source column |
| `AssessmentIssue` | A single rule-based assessment issue |
| `AuthoringQuestion` | An unresolved business decision |
| `AssessmentResult` | Facts, issues, and questions from a check |
| `AuthoringEvidenceInput` | Source SQL / knowledge / confirmation input |
| `AiContextInput` | Agent-authored ai_context fields for a check |

## Collecting Evidence

```python
# Bind datasource access once after loading the project
project.bind_datasource_access(
    inspect_source=mv.datasources.inspect_source,
    backend_factory=mv.datasources.build_backend,
)

# Source evidence
pack = project.inspect_source_context(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    sample_policy=ms.SamplePolicy(mode="bounded_profile", limit=100),
)

# Column deep-dive
evidence = project.inspect_column_context(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    columns=("status", "amount"),
    sample_policy=ms.SamplePolicy(
        mode="selected_columns_profile", limit=100, columns=("status", "amount")
    ),
)

# Non-sample evidence
sql_ref = project.record_authoring_evidence(
    ms.AuthoringEvidenceInput(
        kind="source_sql",
        subject_refs=("sales.revenue",),
        content="select sum(amount) from orders where paid",
    )
)
```

## Retrieving Evidence

```python
# Source-keyed lookup
refs = project.list_evidence(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
)
pack = project.get_evidence_pack(refs[0].id)

# Subject-keyed lookup
refs = project.list_evidence(subject_refs=("sales.revenue",))
```

## Auto-Recorded Decisions

On reload, Marivo auto-records `metric_decomposition` and `time_field_identity`
decisions for authored metrics and time fields. These are the sole mechanism
for writing `DecisionRecord` entries and satisfy the
`dangerous_decision_recorded` rule in `inspect_authored_object`.

## User Confirmations

Record user confirmations with:

```python
project.record_authoring_evidence(
    ms.AuthoringEvidenceInput(
        kind="user_confirmation",
        subject_refs=("sales.order_date",),
        content="Use dt as the reporting time axis.",
    )
)
```

## Relationship Confirmations

Confirm relationships to satisfy the `relationship_unconfirmed` readiness gate:

```python
project.record_authoring_evidence(
    ms.AuthoringEvidenceInput(
        kind="relationship_confirmation",
        subject_refs=("sales.orders_to_items",),
        content="Confirmed join on order_id between orders and items.",
    )
)
```
