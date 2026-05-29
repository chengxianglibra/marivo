# marivo-semantic authoring workflow

This workflow is the standard path for agents building reusable Marivo semantic
objects. It uses the preview, materialization, and parity APIs that are now
available.

## 1. Discover the project

Load the semantic project before adding or changing objects:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; result = project.load(); print(result); print(project.list_models()); print(project.list_datasources()); print(project.list_metrics())'
```

Search and describe existing objects first:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); print(project.search("revenue")); print(project.describe("sales.revenue").to_text() if project.get_metric("sales.revenue") else "sales.revenue not found")'
```

Reuse an existing semantic ref when its business definition, guardrails,
dependencies, and provenance match the requested intent.

## 2. Inspect datasource

Use the project datasource registry before writing semantic files:

```bash
.venv/bin/python -c 'import marivo.analysis as mv; print(mv.datasources.all()); print(mv.datasources.describe("warehouse")); print(mv.datasources.test("warehouse"))'
```

Use `marivo.datasource as md` in `.marivo/datasource/<name>.py` when writing a
datasource file directly. Use `mv.datasources.register(...)` when a script or
agent should create or replace that file through Marivo.

## 3. Collect table evidence

For every new dataset candidate, collect:

- Ibis schema from the live backend
- table and column comments from the datasource metadata catalog
- bounded raw preview rows
- time-like, enum/status, amount, and join-key samples
- supplied knowledge-base or source SQL definitions

`table.schema()` is not enough because it does not include comments.

## 4. Propose a semantic plan

Before editing Python files, state the intended model, datasets, fields,
time fields, metrics, relationships, decomposition, provenance, required
previews, and unresolved blockers.

## 5. Author Python objects

Write declarations under `.marivo/semantic/<model>/`. Keep Python files as the
source of truth. Inspect the live constraints before guessing valid shapes:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; print(ms.help("constraints", format="json"))'
```

## 6. Validate with preview and parity

After authoring, reload the project:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; result = project.reload(); print(result)'
```

4. Build a backend factory once:

```python
backend_factory = lambda name: mv.datasources.build_backend(name)
```

5. Preview every new or changed dataset, field, and metric with `project.preview_dataset(...)`, `project.preview_field(...)`, or `project.preview_metric(...)`.
6. Run `project.parity_check(...)` for metrics with SQL provenance.
7. Mark readiness blockers for any load, preview, materialization, or parity failure.

See `preview.md` for the full preview API reference.

## 7. Check, parity, and readiness

Reload the project after edits:

```bash
.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; result = project.reload(); print(result)'
```

For metrics with source SQL, run parity with a backend factory:

```bash
.venv/bin/python - <<'PY'
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()
backend_factory = lambda name: mv.datasources.build_backend(name)
print(project.parity_check("sales.revenue", backend_factory=backend_factory))
PY
```

Close with an agent-authored readiness report. Do not switch to
`marivo-analysis` while readiness is blocked. See `readiness.md`.
