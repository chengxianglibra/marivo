# marivo-semantic workflow

This is the default workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## 1. Discover the project

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
and provenance match the requested intent.

## 2. Inspect datasource metadata

Choose the datasource backend from the physical source first. Use a native
Marivo datasource by default: Hive/Iceberg lakehouse tables through Trino,
ClickHouse tables through ClickHouse, MySQL tables through MySQL, and DuckDB
database files or supported local files through DuckDB. Do not route ClickHouse
or MySQL tables through a Trino catalog unless the user explicitly says the
project must use Trino federation.

```bash
<venv>/bin/python - <<'PY'
import marivo.analysis as mv
import marivo.semantic as ms

print(mv.datasources.all())
print(mv.datasources.describe("warehouse"))
print(mv.datasources.test("warehouse"))
metadata = mv.datasources.inspect_source("warehouse", source=ms.table("orders"))
print(metadata.to_dict())
PY
```

`table.schema()` returns types but not comments. Use
`mv.datasources.inspect_source(...)` for table comments, column comments,
nullable flags, partition hints, and metadata warnings.

For Trino without a default schema, pass the schema as `database`:

```python
metadata = mv.datasources.inspect_source(
    "warehouse",
    source=ms.table("orders", database="sales_mart"),
)
```

For DuckDB external files, use a file source:

```python
metadata = mv.datasources.inspect_source(
    "warehouse",
    source=ms.file("/data/orders/*.parquet", format="parquet"),
)
```

## 3. Generate candidates

```python
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None

result = project.propose_candidates(
    datasource="warehouse",
    sources=[ms.table("orders", database="sales_mart")],
    model="sales",
    inspect_source=mv.datasources.inspect_source,
)
for candidate in result.candidates:
    print(candidate.decision_kind, candidate.proposed_id, candidate.semantic_delta)
for residual in result.residual_columns:
    print("residual:", residual.dataset, residual.column, residual.data_type, residual.comment)
```

The result is a **non-exhaustive structural starting set**. `result.candidates` contains
dataset, time_field, field, and relationship proposals the heuristics matched.
`result.residual_columns` lists every column the heuristics did not match — these include
measures, primary keys, plain dimensions, and non-conventional foreign keys. Iterate
residuals and decide which are worth declaring; do not treat `result.candidates` as the
complete worklist.

Candidates are not semantic objects. They are structural proposals with evidence.
They do not infer metric decomposition from metric names, column names, comments,
or other string matches. Metric decomposition must come from explicit formula or
source SQL evidence, existing component metrics, ledger/user confirmation, or an
open question during authoring. Once a metric is declared, reload the project so
Marivo records the authored decomposition as an object-level decision; do not
expect `propose_candidates(...)` to generate a metric-decomposition candidate for
an already-authored metric.

## 4. Classify questions

```python
questions = project.open_questions(candidates=result.candidates)
for question in questions:
    print(question.severity, question.decision_kind, question.subject_refs)
```

`open_questions` can run before `_model.py` exists. In that cold-start state it
uses `blast_radius=0` because there is no loaded dependency graph yet. Reload
successfully before closeout so audit, readiness, and richness use the authored
registry. Treat `blast_radius` as a non-negative integer count of distinct
transitive dependents; do not pass ref tuples/lists or candidate lists.

Ask the user only for blocker questions or business decisions evidence cannot
settle. Optional questions may be recorded as assumptions only when the default
is explicit and low risk.

## 5. Author semantic Python

Default to a single `.marivo/semantic/<model>/_model.py`. Use ref variables
between semantic objects. Before declaring an object kind for the first time in
the authoring session, inspect its runtime help, for example
`ms.help("metric", format="json")`. Metrics also require
`ms.help("decomposition", format="json")`; for derived metrics, inspect
`ms.help("derived_metric", format="json")` and the decomposition contract. See
`authoring-patterns.md`.

## 6. Record confirmations and decisions

```python
project.answer(question, "confirmed answer", evidence_fingerprint="sha256:...")
```

Use `project.answer(...)` only for real `OpenQuestion` objects, and never pass
`None` as the answer. Confirmation log entries alone do not clear readiness;
readiness consumes object-level `DecisionRecord` entries.

Use `project.record_decision(semantic_id, record)` only when a complete
`DecisionRecord` can be built from the real question, chosen value, evidence
fingerprint, cited source, and qualifying sources. `semantic_id` is
`question.subject_refs[0]`. Use `question.blast_radius` for the ledger record.
Do not invent internal fields.

## 7. Validate and close out

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)

print(project.reload())
print(project.audit(inspect_source=mv.datasources.inspect_source))
project.collect_source_preview(
    datasource="warehouse",
    table="orders",
    backend_factory=backend_factory,
)
report = project.readiness(
    require_preview=True,
    require_evidence_ledger=True,
    strict_enrichment=True,
    backend_factory=backend_factory,
)
print(report.to_dict())
richness = project.richness()
print(richness.to_dict())
```

Do not hand off to `marivo-analysis` while readiness is blocked. Richness gaps
are advisory follow-up work.
