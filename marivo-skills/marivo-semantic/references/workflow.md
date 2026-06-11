# marivo-semantic workflow

This is the evidence-driven workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## Phase 1: Discovery and Source Inspection

```bash
<venv>/bin/python - <<'PY'
import marivo.semantic as ms

ms.help()
catalog = ms.load()
catalog.list().show()
catalog.list("sales").show()
catalog.list("sales.orders").show()
PY
```

Reuse existing semantic refs when their definitions, guardrails, dependencies,
and provenance match the requested intent. Search before authoring.

After loading the project, collect a
`SourceEvidencePack` for each physical source. Choose the datasource backend
from the physical source first: use native backends by default (Trino for
Hive/Iceberg lakehouse, ClickHouse for ClickHouse tables, MySQL for MySQL tables,
DuckDB for local files). Do not route ClickHouse or MySQL tables through a Trino
catalog unless the user explicitly requires Trino federation.

```python
import marivo.semantic as ms

project = ms.find_project()
catalog = ms.load()
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
metadata = md.inspect_source(
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
collects current source context through the project's datasource access
(kernel defaults: `md.connect` and `md.inspect_source`),
checks the source roles and semantic refs, and returns facts, issues, and
questions.

```python
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
if assessment.status == "blocked":
    # resolve blockers first
    pass
```

Then author and load:

```python
# write .marivo/semantic/sales/_domain.py
catalog = ms.load()
project.inspect_authored_object("sales.orders")
```

### Time Dimension Authoring

Author time dimensions only after temporal evidence. If partition vs event-time
conflict, surface the `AuthoringQuestion`:

```python
assessment = project.assess_authoring(
    object_kind="time_dimension",
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

Reload so Marivo can auto-record `time_dimension_identity` decisions.

### Dimension Authoring

```python
assessment = project.assess_authoring(
    object_kind="dimension",
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
catalog = ms.load()
revenue = catalog.get("sales.revenue")
report = catalog.readiness(refs=[revenue.ref])
if report.blocked:
    report.show()
    raise SystemExit
```

Do not hand off to `marivo-analysis` while readiness is blocked. Richness gaps
are reported as readiness warnings and summarized in `richness_summary`.
