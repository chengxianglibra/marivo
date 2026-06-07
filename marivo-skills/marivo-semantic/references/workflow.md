# marivo-semantic workflow

This is the three-phase authoring pipeline for agents building reusable Marivo
semantic objects. It is evidence-first, assessment-gated, and
readiness-closed.

## Phase 1: Discovery

### Stage 1: Project Discovery

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

### Stage 2: Source Evidence

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

### Stage 3: Column Deep Dives

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

## Phase 2: Authoring

Assess each candidate before writing it:

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
    raise RuntimeError([issue.message for issue in assessment.issues])
if assessment.status == "needs_input":
    raise RuntimeError([question.prompt for question in assessment.questions])
```

Write all confirmed objects into one `.marivo/semantic/<model>/_model.py`:

```python
# .marivo/semantic/sales/_model.py
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales", description="Sales analytics")
warehouse = md.ref("warehouse")

orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Preview raw orders before analysis handoff."],
    },
)

@ms.time_field(
    dataset=orders,
    name="order_date",
    data_type="date",
    granularity="day",
    ai_context={
        "business_definition": "Daily order partition.",
        "guardrails": ["Use as the default reporting window axis."],
    },
)
def order_date(table):
    return table.dt

@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="revenue",
    source_sql="select sum(amount) as revenue from orders where paid",
    source_dialect="duckdb",
    ai_context={
        "business_definition": "Paid order revenue before refunds.",
        "guardrails": ["Excludes unpaid orders."],
    },
    verification_mode="sql_parity",
)
def revenue(table):
    return table.amount.sum()
```

Record source SQL or knowledge evidence before assessing metrics that need it:

```python
sql_ref = project.record_authoring_evidence(
    ms.AuthoringEvidenceInput(
        kind="source_sql",
        subject_refs=("sales.revenue",),
        content="select sum(amount) as revenue from orders where paid",
        source_dialect="duckdb",
    )
)
```

Do not reload between objects in the same file. Reload is deferred until
closeout.

## Phase 3: Validation

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
    parity_rel_tol=1e-6,
)
print(report.to_dict())
if report.status == "blocked":
    raise RuntimeError([issue.message for issue in report.blockers])
```

Do not hand off to `marivo-analysis` while readiness is blocked. Warnings
include parity and richness follow-up work. Richness gaps are folded into
readiness warnings; a separate `project.richness(...)` call is optional for
deeper advisory coverage.
