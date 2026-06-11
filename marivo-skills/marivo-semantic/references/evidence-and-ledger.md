# Evidence and Ledger

## Two Output Levels

- **Fact**: An observed, verifiable piece of evidence (column type, null count,
  sample top values).
- **Assessment**: A rule-based evaluation of whether authoring can proceed.
  `AuthoringAssessment` contains `facts`, `issues`, and `questions`. Issues have
  severity (`blocker`/`warning`/`info`); questions represent unresolved
  business decisions.

## Evidence DTOs

| DTO | Purpose |
| --- | ------- |
| `TableContext` | Basic table/file metadata from `inspect_table` |
| `ColumnContext` | Fixed-sample column details from `inspect_columns` |
| `TableSource` | Physical table source (table name, optional database) |
| `FileSource` | Physical file source (path + format: parquet/csv/json) |
| `DatasetSource` | Type alias: `TableSource \| FileSource` |
| `MetadataOnlyPolicy` | No row reading, metadata-only profiling |
| `BoundedProfilePolicy` | Reads rows with a limit |
| `SelectedColumnsPolicy` | Reads selected columns with a limit |
| `SamplePolicy` | Type alias: `MetadataOnlyPolicy \| BoundedProfilePolicy \| SelectedColumnsPolicy` |
| `EvidenceFact` | Single observed fact |
| `ColumnProfile` | Bounded-sample profile for one column |
| `SourceEvidencePack` | Collected facts and profiles for a source |
| `ColumnEvidence` | Deep-dive evidence for one source column |
| `AssessmentIssue` | A single rule-based assessment issue |
| `AuthoringQuestion` | An unresolved business decision |
| `AuthoringAssessment` | Facts, issues, and questions from an authoring assessment |

## Collecting Evidence

```python
# Table metadata
table_context = project.inspect_table("warehouse", ms.table("orders"))

# Column deep-dive
evidence = project.inspect_columns(
    "warehouse",
    ms.table("orders"),
    columns=("status", "amount"),
)

assessment = project.assess_authoring(
    object_kind="entity",
    subject_ref="sales.orders",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
        ),
    ),
)
```

## Auto-Recorded Decisions

On load, Marivo auto-records `metric_decomposition` and `time_field_identity`
decisions for authored metrics and time fields. These are the sole mechanism
for writing `DecisionRecord` entries and satisfy the
`dangerous_decision_recorded` rule in `inspect_authored_object`.
