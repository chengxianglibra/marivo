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
```

## Auto-Recorded Decisions

On load, Marivo auto-records `metric_decomposition` and `time_field_identity`
decisions for authored metrics and time fields. These are the sole mechanism
for writing `DecisionRecord` entries and satisfy the
`dangerous_decision_recorded` rule in `inspect_authored_object`.
