# Marivo

Metric-centered analysis runtime for AI agents.

Marivo is a Python library, not a hosted service. A Marivo project keeps
datasource declarations, semantic declarations, analysis sessions, frames, and
evidence under the project-local `.marivo/` directory. Agents use that project
state to discover trusted metrics, run typed analysis, and explain how a result
was produced.

Marivo is not a text-to-SQL wrapper. Python declarations are the contract, ibis
expressions are the execution language, and typed frames are the analysis
boundary between steps.

Marivo gives agents a unified semantic layer over your data and a structured
analysis workflow they can drive safely and repeatably:

- **Unified semantics** — Datasources, entities, metrics, and relationships are
  declared in Python. Agents address data by semantic ref (`sales.revenue`),
  not raw table/column names. `ai_context` on every object carries business
  definitions, guardrails, and synonyms so agents understand what the data means
  and what boundaries to respect.

- **Metric-centered analysis** — Every analysis session starts from a metric.
  Operators like observe, compare, decompose, correlate, and forecast chain
  through typed frames, giving agents a composable, predictable workflow instead
  of ad-hoc SQL.

- **Trustworthy evidence** — Every operator records findings, propositions, and
  assessments into a session-scoped evidence store. Agents can trace any
  conclusion back through the chain of evidence, and human reviewers can audit
  what the agent did and why.

- **Readiness gates** — Before a metric reaches analysis, `catalog.readiness()`
  checks that authoring is complete: business definitions present, source
  evidence collected, parity verified. Blocked objects cannot be used, so agents
  never reason over half-specified semantics.

## Requirements

Python >=3.12

## Installation

Install the base library from PyPI:

```bash
pip install marivo
```

Install the backend extra that matches the datasource you want to query:

| Backend    | Extra                        |
|------------|------------------------------|
| DuckDB     | `pip install "marivo[duckdb]"` |
| MySQL      | `pip install "marivo[mysql]"`  |
| Trino      | `pip install "marivo[trino]"`  |
| All        | `pip install "marivo[all]"`     |

The packaged extras above are the supported README-level install targets. Other
backend integrations may exist in code before they are exposed as packaged
extras.

For local development from this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,duckdb,trino]"
.venv/bin/python -c "import marivo; print(marivo.__name__)"
```

There is no separate server deployment step. Deploy a Marivo project by
installing the library in the runtime where the agent runs, checking in the
`models/datasources/` and `models/semantic/` declarations, and providing any
datasource secrets through environment variables referenced by `*_env` fields.

## Quick Start

### Create a Project

A minimal project has a `marivo.toml` manifest, datasource declarations under
`models/datasources/`, and semantic declarations under `models/semantic/`:

```text
your-project/
  marivo.toml
  models/
    datasources/
      warehouse.py
    semantic/
      sales/
        _domain.py
```

Declare a datasource in `models/datasources/warehouse.py`:

```python
import marivo.datasource as md

md.duckdb(
    name="warehouse",
    path="warehouse.duckdb",
    ai_context={
        "business_definition": "Local DuckDB warehouse for sales analysis.",
        "guardrails": ["Use only for development or approved local analysis."],
    },
)
```

Declare semantic objects in `models/semantic/sales/_domain.py`:

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(
    name="sales",
    default=True,
    ai_context={
        "business_definition": "Sales order analysis domain.",
        "guardrails": ["Revenue metrics should only use completed orders."],
    },
)

orders = ms.entity(
    name="orders",
    datasource=md.ref("warehouse"),
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per sales order.",
        "guardrails": ["Exclude cancelled test data in metric definitions."],
    },
)


@ms.time_dimension(
    entity=orders,
    name="order_date",
    data_type="date",
    granularity="day",
    is_default=True,
    ai_context={
        "business_definition": "Calendar date when the order was created.",
        "guardrails": ["Use for order-time analysis, not fulfillment-time analysis."],
    },
)
def order_date(orders):
    return orders.created_at.cast("date")


@ms.dimension(
    entity=orders,
    name="region",
    ai_context={
        "business_definition": "Sales region assigned to the order.",
        "guardrails": ["Do not treat missing region as a real region."],
    },
)
def region(orders):
    return orders.region


@ms.metric(
    entities=[orders],
    decomposition=ms.sum(),
    additivity="additive",
    name="revenue",
    verification_mode="python_native",
    ai_context={
        "business_definition": "Total completed order amount.",
        "guardrails": ["Use only where order amount is already net of cancellations."],
    },
)
def revenue(orders):
    return orders.amount.sum()
```

### Discover the Semantic Layer

Before running any analysis, an agent explores what the semantic catalog offers:

```python
import marivo.semantic as ms

catalog = ms.load()
catalog.list().show()                              # domains + datasources
catalog.list("sales").show()                       # entities + metrics in "sales"
catalog.list("sales.orders", kind="metric").show() # metrics on the orders entity

revenue = catalog.get("sales.revenue")
print(revenue.context.business_definition)         # human-authored context
print(revenue.context.guardrails)                  # boundaries for the agent

report = catalog.readiness()
if report.status == "blocked":
    report.show()                                  # cannot proceed until resolved
```

A new project can load successfully while readiness is still blocked. Use
`report.show()` to inspect missing source evidence, backend access, parity, or
enrichment work before running metric analysis.

### Start an Analysis Session

After the semantic catalog is ready and the runtime has datasource access, an
agent opens a session with a guiding question, then chains operators through
typed frames:

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="revenue-check", question="Why did Q4 drop?")
catalog = session.catalog
revenue = catalog.get("sales.revenue")
region = catalog.get("sales.orders.region").ref

current = session.observe(
    revenue,
    timescope={"start": "2026-10-01", "end": "2027-01-01"},
    grain="month",
)
baseline = session.observe(
    revenue,
    timescope={"start": "2025-10-01", "end": "2026-01-01"},
    grain="month",
)
delta = session.compare(current, baseline)
attribution = session.decompose(delta, axis=region)
attribution.show()
```

### Trace Evidence

Every operator records findings and propositions. The agent can review what it
has established so far, and human reviewers can audit the full chain of
reasoning:

```python
knowledge = session.knowledge()
print(knowledge.change_facts)          # delta observations
print(knowledge.driver_facts)          # decomposition results
print(knowledge.open_anomalies)        # unresolved anomalies
print(knowledge.next_steps_payload)    # suggested follow-ups

for prop in session.evidence.propositions():
    trace = session.evidence.trace(prop.proposition_id)
    print(trace)                       # proposition → findings → assessments
```

### Explore Outside the Semantic Layer

When the answer requires data not yet in a semantic model, the agent uses the
escape hatch — then promotes results back into typed frames:

```python
result = session.explore_ibis(
    lambda con: (
        con.table("returns")
        .filter(lambda t: t.region == "EMEA")
        .aggregate(value=lambda t: t.refund_amount.sum())
    ),
    datasource="warehouse",
    description="Ad-hoc EMEA returns query",
)
frame = session.promote_metric_frame(
    result,
    metric=session.catalog.get("sales.revenue"),
    semantic_kind="scalar",
    measure_column="value",
    semantic_model="sales",
)
```

## Semantic Authoring

The semantic layer is authored by agents in Python files under
`models/semantic/<domain>/`, then reviewed and refined by humans.
Marivo provides several capabilities to help agents author correctly:

- **`ms.help()`** — discover the full authoring surface; `ms.help("metric")`
  drills into a specific symbol's parameters, constraints, and examples.
- **Source evidence** — `SourceEvidencePack` and `ColumnProfile` give the agent
  bounded schema and sample statistics from each datasource, so it can decide
  entity structures, dimensions, and metrics from real data rather than
  guessing.
- **Authoring assessment** — `AuthoringAssessment` reports issues
  (`AssessmentIssue`) and raises open questions (`AuthoringQuestion`) that the
  agent must resolve before the semantic object passes the readiness gate.
- **Readiness gate** — `catalog.readiness()` checks that every object has
  required fields, `ai_context` with `business_definition` and `guardrails`,
  and no validator violations. Blocked objects cannot be used in analysis.
- **Structured errors** — `SemanticError` carries `constraint_id`, `hint`,
  `location`, and did-you-mean suggestions, giving the agent clear corrective
  actions on each failure.

### Datasources

```python
import marivo.datasource as md

md.duckdb(name="warehouse", path="warehouse.duckdb")

# Sensitive fields must use _env — resolved from environment variables at runtime
md.trino(
    name="trino_prod",
    host="trino.internal", user_env="TRINO_USER", password_env="TRINO_PASSWORD",
    catalog="analytics",
)
```

### Domains, Entities, and Metrics

The example below is abbreviated. Production-ready metrics should also carry
`ai_context` and source/parity evidence appropriate to the data contract.

```python
import marivo.semantic as ms
import marivo.datasource as md

ms.domain(name="sales", default=True)
warehouse = md.ref("warehouse")

orders = ms.entity(name="orders", datasource=warehouse, source=ms.table("orders"), primary_key=["order_id"])

@ms.time_dimension(entity=orders, name="order_date", data_type="date", granularity="day", is_default=True)
def order_date(orders):
    return orders.created_at.cast("date")

@ms.dimension(entity=orders, name="region")
def region(orders):
    return orders.region

@ms.metric(
    entities=[orders],
    decomposition=ms.sum(),
    additivity="additive",
    name="revenue",
    verification_mode="python_native",
)
def revenue(orders):
    return orders.amount.sum()

@ms.metric(
    entities=[orders],
    decomposition=ms.sum(),
    additivity="additive",
    name="order_count",
    verification_mode="python_native",
)
def order_count(orders):
    return orders.order_id.count()

ms.derived_metric(name="aov", decomposition=ms.ratio(numerator=revenue, denominator=order_count))
```

## Analysis Surface

All operators are methods on a `Session` instance.

### Core Operators

| Operator          | Input                        | Output                |
|-------------------|------------------------------|-----------------------|
| `observe`         | metric ref + timescope       | `MetricFrame`         |
| `compare`         | two `MetricFrame`            | `DeltaFrame`          |
| `decompose`       | `DeltaFrame` + axis          | `AttributionFrame`    |
| `correlate`       | two `MetricFrame`            | `AssociationResult`   |
| `hypothesis_test` | two `MetricFrame`            | `HypothesisTestResult`|
| `forecast`        | `MetricFrame` + horizon      | `ForecastFrame`       |
| `assess_quality`  | any frame                    | `QualityReport`       |

### Discover (`session.discover.*`)

| Method                    | Finds                      |
|---------------------------|----------------------------|
| `point_anomalies`         | Unusual time-series points |
| `period_shifts`           | Structural period changes  |
| `driver_axes`             | Dimensions that explain a delta |
| `interesting_slices`      | Notable dimension slices   |
| `interesting_windows`     | Notable time windows       |
| `cross_sectional_outliers`| Cross-segment outliers     |

### Transform (`session.transform.*`)

| Method       | Effect                        |
|--------------|-------------------------------|
| `filter`     | Row filter via predicate      |
| `slice`      | Filter by axis values         |
| `rollup`     | Aggregate to coarser grain    |
| `topk`       | Keep top N by measure         |
| `bottomk`    | Keep bottom N by measure      |
| `rank`       | Add rank column               |
| `normalize`  | Convert to share/index/z-score|
| `window`     | Re-bucket time axis           |

All transforms are family-preserving: output type matches input type.

## Key Concepts

**Frames** — Typed, persisted analysis outputs. `MetricFrame`, `DeltaFrame`,
`AttributionFrame`, `CandidateSet`, `ForecastFrame`, `QualityReport`,
`HypothesisTestResult`, `AssociationResult`, and `ExplorationResult`. Frames
chain between operators and are automatically persisted under `.marivo/`.

**Evidence** — Every operator records findings, propositions, and assessments
into a session-scoped evidence store. Access via
`session.evidence.findings()`, `.propositions()`, `.assessments()`,
`.trace()`, or `session.knowledge()` for a structured summary of all facts
and open items.

**Readiness** — `catalog.readiness()` returns a `ReadinessReport` with
blockers, warnings, parity and richness summaries. Blocked objects cannot be
used in analysis until authoring issues are resolved.

**ai_context** — Every datasource, entity, dimension, metric, and relationship
accepts an `ai_context` dict with `business_definition`, `guardrails`,
`synonyms`, `examples`, `instructions`, and `owner_notes`. This metadata is
the contract between human authors and AI agents.

**Escape hatch** — For data not yet in a semantic model, use
`session.from_pandas(df)` or `session.explore_ibis(query, datasource=...)`
to create an `ExplorationResult`, then promote it with
`session.promote_metric_frame()`, `.promote_delta_frame()`, or
`.promote_attribution_frame()`.

## In-Project Help

Every surface provides agent-facing introspection:

```python
import marivo.semantic as ms
import marivo.analysis as mv

ms.help()            # list all semantic symbols
ms.help("metric")    # metric authoring details
mv.help()            # list core analysis surface entries
mv.help("observe")   # observe intent details
```

The `marivo.analysis as mv` top level is the agent-facing core surface:
constructor inputs, sessions, frames, frame metadata, lineage, and namespace
entrypoints. Domain DTOs stay in their namespaces, such as `mv.evidence`,
`mv.datasources`, and `mv.errors`.

## Development

Set up a local editable install with the development dependencies you need:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,duckdb,trino]"
```

Repository commands use the local virtualenv explicitly through `make`:

```bash
make lint
make typecheck
make examples-check
make test
make check
```

Use the narrowest command that proves your change first, then broaden when the
change touches shared behavior:

| Change type | First check |
|-------------|-------------|
| Semantic declaration/runtime change | `make typecheck` plus targeted tests |
| Analysis operator/frame change | targeted tests, then `make test` |
| Agent examples or skills | `make examples-check` |
| Formatting-only cleanup | `make format` |
| Release artifact check | `make pypi-build` then `make pypi-check` |

Build and validate package artifacts:

```bash
make pypi-build
make pypi-check
```

Read [`agent-guide.md`](agent-guide.md) before contributing code in this
repository. The maintained design references start at [`docs/README.md`](docs/README.md):

- `docs/specs/semantic/python-semantic-layer.md` for semantic declarations.
- `docs/specs/analysis/python-analysis-design.md` for analysis frames,
  operators, evidence, judgment, and follow-up surfaces.

## Agent Skills

The maintained agent guidance lives in:

- `marivo/skills/marivo-semantic`
- `marivo/skills/marivo-analysis`

Examples under each skill are executable and checked by `make examples-check`.
