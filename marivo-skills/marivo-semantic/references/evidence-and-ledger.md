# Evidence and Ledger

## Two Output Levels

- **Fact**: An observed, verifiable piece of evidence (column type, null count,
  sample top values). Facts carry `evidence_refs` linking back to the
  `SourceEvidencePack` or `AuthoringEvidenceInput` that produced them.
- **Assessment**: A rule-based evaluation of whether authoring can proceed.
  `AuthoringAssessment` contains `facts`, `issues`, and `questions`. Issues have
  severity (`blocker`/`warning`/`info`); questions represent unresolved
  business decisions.

## Evidence DTOs

| DTO | Purpose |
| --- | ------- |
| `TableSource` | Physical table source (table name, optional database) |
| `FileSource` | Physical file source (path + format: parquet/csv/json) |
| `DatasetSource` | Type alias: `TableSource \| FileSource` |
| `MetadataOnlyPolicy` | No row reading, metadata-only profiling |
| `BoundedProfilePolicy` | Reads rows with a limit |
| `SelectedColumnsPolicy` | Reads selected columns with a limit |
| `SamplePolicy` | Type alias: `MetadataOnlyPolicy \| BoundedProfilePolicy \| SelectedColumnsPolicy` |
| `EvidenceRef` | Reference to collected authoring evidence |
| `EvidenceFact` | Single observed fact with evidence refs |
| `ColumnProfile` | Bounded-sample profile for one column |
| `SourceEvidencePack` | Collected facts and profiles for a source |
| `ColumnEvidence` | Deep-dive evidence for one source column |
| `AssessmentIssue` | A single rule-based assessment issue |
| `AuthoringQuestion` | An unresolved business decision |
| `AuthoringAssessment` | Facts, issues, and questions from assess_authoring |
| `AuthoringSourceInput` | Role-tagged source input for assess_authoring |
| `AuthoringEvidenceInput` | Source SQL / knowledge / confirmation input |
| `AiContextInput` | Agent-authored ai_context fields for an assessment |

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
    source=ms.TableSource(table="orders"),
    sample_policy=ms.BoundedProfilePolicy(limit=100),
)

# Column deep-dive
evidence = project.inspect_column_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
    columns=("status", "amount"),
    sample_policy=ms.SelectedColumnsPolicy(limit=100, columns=("status", "amount")),
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

## Assessing Authoring

After collecting evidence, assess each candidate object before writing it:

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
    # resolve blockers first
    raise RuntimeError([issue.message for issue in assessment.issues])
if assessment.status == "needs_input":
    # ask user or provide missing context
    raise RuntimeError([question.prompt for question in assessment.questions])
# assessment.status == "supported" -> proceed to author
```

## Retrieving Evidence

```python
# Source-keyed lookup
refs = project.list_evidence(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
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

## Durable Decision Ledger

Evidence collection produces fresh inspection facts. Authoring assessments and
readiness reports consume those facts. The durable decision ledger records
auto-recorded object decisions (`metric_decomposition`, `time_field_identity`)
and user confirmations. Ledger entries persist across reloads and sessions;
fresh evidence facts do not invalidate them unless staleness is explicitly
checked.
