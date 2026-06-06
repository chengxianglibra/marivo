# marivo-semantic workflow

This is the evidence-driven workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## Stage 1: Project Discovery

```bash
<venv>/bin/python - <<'PY'
import marivo.semantic as ms

print(ms.help(format="json"))
project = ms.find_project()
assert project is not None
print(project.load())
project.list_models()
project.list_datasets()
project.list_metrics()
PY
```

Reuse existing semantic refs when their definitions, guardrails, dependencies,
and provenance match the requested intent. Search before authoring.

## Stage 2: Source Evidence

For each physical source, collect a `SourceEvidencePack`. Choose the datasource
backend from the physical source first: use native backends by default (Trino for
Hive/Iceberg lakehouse, ClickHouse for ClickHouse tables, MySQL for MySQL tables,
DuckDB for local files). Do not route ClickHouse or MySQL tables through a Trino
catalog unless the user explicitly requires Trino federation.

```python
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
pack = project.inspect_source_context(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders", database="sales_mart"),
    inspect_source=mv.datasources.inspect_source,
    backend_factory=lambda name: mv.datasources.build_backend(name),
    sample_policy=ms.SamplePolicy(mode="bounded_profile", limit=100, max_profiled_columns=50),
)
```

Stop on insufficient evidence — fix datasource access or request missing context
before continuing. `inspect_source_context` folds metadata inspection and bounded
preview into one call and persists evidence metadata under
`.marivo/semantic/.evidence/`.

For `metadata_only` policy (no row reads):

```python
pack = project.inspect_source_context(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    inspect_source=mv.datasources.inspect_source,
    backend_factory=lambda name: mv.datasources.build_backend(name),
    sample_policy=ms.SamplePolicy(mode="metadata_only"),
)
```

For Trino without a default schema, pass the schema as `database`:

```python
metadata = mv.datasources.inspect_source(
    "warehouse",
    source=ms.table("orders", database="sales_mart"),
)
```

## Stage 3: Column Deep Dives

Deep-dive selected columns after source evidence:

```python
evidence = project.inspect_column_context(
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    columns=("status", "amount"),
    inspect_source=mv.datasources.inspect_source,
    backend_factory=lambda name: mv.datasources.build_backend(name),
    sample_policy=ms.SamplePolicy(
        mode="selected_columns_profile", limit=100, columns=("status", "amount")
    ),
)
for col in evidence:
    print(col.column, col.profile.distinct_count, col.profile.top_values)
```

Use this for time/enum/amount/join-key columns. Sample-derived values are facts
about the bounded sample only — never treat them as full-table truth.

## Stage 4: Dataset Authoring

Check authoring inputs before writing:

```python
result = project.check_authoring_inputs(
    object_kind="dataset",
    subject_ref="sales.orders",
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
)
if result.status == "blocked":
    # resolve blockers first
    pass
```

Then author and reload:

```python
# write .marivo/semantic/sales/_model.py and datasets.py
project.reload()
project.inspect_authored_object("sales.orders")
```

## Stage 5: Time Field Authoring

Author time fields only after temporal evidence. If partition vs event-time
conflict, surface the `AuthoringQuestion`:

```python
result = project.check_authoring_inputs(
    object_kind="time_field",
    subject_ref="sales.orders.dt",
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    columns=("dt",),
)
```

Reload so Marivo can auto-record `time_field_identity` decisions.

## Stage 6: Field Authoring

```python
result = project.check_authoring_inputs(
    object_kind="field",
    subject_ref="sales.orders.amount",
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    columns=("amount",),
)
```

## Stage 7: Metric Authoring

Record source SQL first, cite it in the check:

```python
sql_ref = project.record_authoring_evidence(
    ms.AuthoringEvidenceInput(
        kind="source_sql",
        subject_refs=("sales.revenue",),
        content="select sum(amount) as revenue from orders where paid",
        source_dialect="trino",
    )
)
result = project.check_authoring_inputs(
    object_kind="metric",
    subject_ref="sales.revenue",
    datasource="warehouse",
    source=ms.DatasetSource(kind="table", table="orders"),
    columns=("amount", "paid"),
    evidence_refs=(sql_ref.id,),
    ai_context=ms.AiContextInput(business_definition="Paid order revenue before refunds."),
)
```

After authoring and reload, run `inspect_authored_object` then previews/parity.

## Stage 8: Relationship Authoring

Require relationship-intent evidence. Orphan/fanout/RI scans are optional
diagnostics, not gates.

## Stage 9: Incremental Review & Closeout

```python
project.reload()
project.inspect_authored_object("sales.revenue")
# bounded preview where needed
project.collect_source_preview(
    datasource="warehouse", table="orders",
    backend_factory=lambda name: mv.datasources.build_backend(name),
)
report = project.readiness(
    require_preview=True,
    require_evidence_ledger=True,
    backend_factory=lambda name: mv.datasources.build_backend(name),
)
print(report.to_dict())
richness = project.richness()
print(richness.to_dict())
```

Do not hand off to `marivo-analysis` while readiness is blocked. Richness gaps
are advisory follow-up work.
