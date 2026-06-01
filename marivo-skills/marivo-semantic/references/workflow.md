# marivo-semantic workflow

This is the default workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## 1. Discover the project

```bash
<venv>/bin/python - <<'PY'
import marivo.semantic as ms

print(ms.help(format="json"))
print(ms.help("constraints", format="json"))
project = ms.find_project()
assert project is not None
print(project.load())
print(project.list_models())
print(project.list_datasets())
print(project.list_metrics())
PY
```

Reuse existing semantic refs when their definitions, guardrails, dependencies,
and provenance match the requested intent.

## 2. Inspect datasource metadata

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

candidates = project.propose_candidates(
    datasource="warehouse",
    sources=[ms.table("orders", database="sales_mart")],
    model="sales",
    inspect_source=mv.datasources.inspect_source,
)
for candidate in candidates:
    print(candidate.decision_kind, candidate.proposed_id, candidate.semantic_delta)
```

Candidates are not semantic objects. They are structural proposals with evidence.
They do not infer metric decomposition from metric names, column names, comments,
or other string matches. Metric decomposition must come from explicit formula or
source SQL evidence, existing component metrics, ledger/user confirmation, or an
open question during authoring.

## 4. Classify questions

```python
questions = project.open_questions(candidates=candidates)
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
`ms.help("decomposition", format="json")`; derived metrics also require
`ms.help("component", format="json")`. See `authoring-patterns.md`.

## 6. Record confirmations and decisions

```python
project.answer(question, "confirmed answer", evidence_fingerprint="sha256:...")
```

Use `project.record_decision(...)` only when a complete `DecisionRecord` can be
built from the real question, chosen value, evidence fingerprint, cited source,
and qualifying sources. Use `question.blast_radius` for the ledger record. Do not
invent internal fields.

## 7. Validate and close out

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)

print(project.reload())
print(project.audit(inspect_source=mv.datasources.inspect_source))
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
