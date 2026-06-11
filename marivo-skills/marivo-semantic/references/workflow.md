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

Bind datasource access once after loading the project, then collect a
`SourceEvidencePack` for each physical source. Choose the datasource backend
from the physical source first: use native backends by default (Trino for
Hive/Iceberg lakehouse, ClickHouse for ClickHouse tables, MySQL for MySQL tables,
DuckDB for local files). Do not route ClickHouse or MySQL tables through a Trino
catalog unless the user explicitly requires Trino federation.

```python
import marivo.semantic as ms

project = ms.find_project()
catalog = ms.load()
table_context = project.inspect_table(
    "warehouse",
    ms.table("orders", database="sales_mart"),
)
column_contexts = project.inspect_columns("warehouse", ms.table("orders"))
```

Stop on insufficient evidence — fix datasource access or request missing context
before continuing. `inspect_table` reads metadata only; `inspect_columns` reads
a fixed 5-row sample and closes its datasource connection.

For metadata only (no row reads):

```python
table_context = project.inspect_table("warehouse", ms.table("orders"))
```

For Trino without a default schema, pass the schema as `database`:

```python
table_context = project.inspect_table(
    "warehouse",
    ms.table("orders", database="sales_mart"),
)
```

### Column Deep Dives

Deep-dive selected columns after source evidence:

```python
evidence = project.inspect_columns(
    "warehouse",
    ms.table("orders"),
    columns=("status", "amount"),
)
for col in evidence:
    print(col.column, col.data_type, col.sample_values)
```

Use this for time/enum/amount/join-key columns. Sample-derived values are facts
about the fixed 5-row sample only — never treat them as full-table truth.

Assess candidate authoring inputs before writing semantic files:

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
```

## Phase 2: Assess and Author Each Candidate Object

Call `project.assess_authoring(...)` before writing each candidate object. It
collects current source context through project datasource configuration,
checks the source roles and semantic refs, and returns facts, issues, and questions.

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
