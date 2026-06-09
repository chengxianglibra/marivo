# marivo-semantic workflow

This is the evidence-driven workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## Phase 1: Discovery and Source Inspection

```bash
<venv>/bin/python - <<'PY'
import marivo.semantic as ms

ms.help(format="json")
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

Bind datasource access once after loading the project, then collect a
`SourceEvidencePack` for each physical source. Choose the datasource backend
from the physical source first: use native backends by default (Trino for
Hive/Iceberg lakehouse, ClickHouse for ClickHouse tables, MySQL for MySQL tables,
DuckDB for local files). Do not route ClickHouse or MySQL tables through a Trino
catalog unless the user explicitly requires Trino federation.

```python
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
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

Stop on insufficient evidence — fix datasource access or request missing context
before continuing. `inspect_source_context` folds metadata inspection and bounded
preview into one call and persists evidence metadata under
`.marivo/semantic/.evidence/`.

For `metadata_only` policy (no row reads):

```python
pack = project.inspect_source_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
    sample_policy=ms.MetadataOnlyPolicy(),
)
```

For Trino without a default schema, pass the schema as `database`:

```python
metadata = mv.datasources.inspect_source(
    "warehouse",
    source=ms.table("orders", database="sales_mart"),
)
```

### Column Deep Dives

Deep-dive selected columns after source evidence:

```python
evidence = project.inspect_column_context(
    datasource="warehouse",
    source=ms.TableSource(table="orders"),
    columns=("status", "amount"),
    sample_policy=ms.SelectedColumnsPolicy(
        limit=100, columns=("status", "amount")
    ),
)
for col in evidence:
    print(col.column, col.profile.distinct_count, col.profile.top_values)
```

Use this for time/enum/amount/join-key columns. Sample-derived values are facts
about the bounded sample only — never treat them as full-table truth.

## Phase 2: Assess and Author Each Candidate Object

Call `project.assess_authoring(...)` before writing each candidate object. It
collects current source context through the datasource access bound in Phase 1,
checks the source roles and semantic refs, and returns facts, issues, and
questions.

```python
assessment = project.assess_authoring(
    object_kind="dataset",
    subject_ref="sales.orders",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
        ),
    ),
)
if assessment.status == "blocked":
    # resolve blockers first
    pass
```

Then author and load:

```python
# write .marivo/semantic/sales/_model.py
project.load()
project.inspect_authored_object("sales.orders")
```

### Time Field Authoring

Author time fields only after temporal evidence. If partition vs event-time
conflict, surface the `AuthoringQuestion`:

```python
assessment = project.assess_authoring(
    object_kind="time_field",
    subject_ref="sales.orders.dt",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("dt",),
        ),
    ),
    semantic_refs=("sales.orders",),
)
```

Reload so Marivo can auto-record `time_field_identity` decisions.

### Field Authoring

```python
assessment = project.assess_authoring(
    object_kind="field",
    subject_ref="sales.orders.amount",
    sources=(
        ms.AuthoringSourceInput(
            role="primary",
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            columns=("amount",),
        ),
    ),
    semantic_refs=("sales.orders",),
)
```

### Metric Authoring

Pass physical source roles and semantic dependencies into the assessment:

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
```

After authoring and load, run `inspect_authored_object`. Final runtime
preview, parity, and richness checks are composed by the readiness closeout.

### Relationship Authoring

Require relationship-intent evidence. Orphan/fanout/RI scans are optional
diagnostics, not gates.

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
    semantic_refs=("sales.orders", "sales.customers"),
)
```

## Phase 3: Single Readiness Closeout

```python
project.load()
project.inspect_authored_object("sales.revenue")
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        intents=("revenue trend",),
        run_history_refs=("sales.revenue",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
)
print(report.to_dict())
```

Do not hand off to `marivo-analysis` while readiness is blocked. Richness gaps
are reported as readiness warnings and summarized in `richness_summary`.
