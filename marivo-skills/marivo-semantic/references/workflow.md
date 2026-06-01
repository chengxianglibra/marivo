# marivo-semantic workflow

This is the default workflow for agents building reusable Marivo semantic
objects. It is evidence-first, ledger-aware, and readiness-gated.

## 1. Discover the project

```bash
<venv>/bin/python - <<'PY'
import marivo.semantic as ms

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

print(mv.datasources.all())
print(mv.datasources.describe("warehouse"))
print(mv.datasources.test("warehouse"))
metadata = mv.datasources.inspect_table("warehouse", table="orders")
print(metadata.to_dict())
PY
```

`table.schema()` returns types but not comments. Use
`mv.datasources.inspect_table(...)` for table comments, column comments,
nullable flags, partition hints, and metadata warnings.

For Trino without a default schema, pass the schema as `database`:

```python
metadata = mv.datasources.inspect_table("warehouse", table="orders", database="sales_mart")
```

## 3. Generate candidates

```python
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()

candidates = project.propose_candidates(
    datasource="warehouse",
    tables=["orders"],
    model="sales",
    inspect_table=mv.datasources.inspect_table,
)
for candidate in candidates:
    print(candidate.decision_kind, candidate.proposed_id, candidate.semantic_delta)
```

Candidates are not semantic objects. They are structural proposals with evidence.

## 4. Classify questions

```python
questions = project.open_questions(candidates=candidates)
for question in questions:
    print(question.severity, question.decision_kind, question.subject_refs)
```

Ask the user only for blocker questions or business decisions evidence cannot
settle. Optional questions may be recorded as assumptions only when the default
is explicit and low risk.

## 5. Author semantic Python

Default to a single `.marivo/semantic/<model>/_model.py`. Use ref variables
between semantic objects. See `authoring-patterns.md`.

## 6. Record confirmations and decisions

```python
project.answer(question, "confirmed answer", evidence_fingerprint="sha256:...")
```

Use `project.record_decision(...)` only when a complete `DecisionRecord` can be
built from the real question, chosen value, evidence fingerprint, cited table,
and qualifying sources. Do not invent internal fields.

## 7. Validate and close out

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)

print(project.reload())
print(project.audit(inspect_table=mv.datasources.inspect_table))
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
