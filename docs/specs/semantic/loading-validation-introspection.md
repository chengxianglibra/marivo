# Loading, Validation, and Introspection

Status: draft design. This document describes the runtime side of
`marivo.semantic`: how authored Python files become a loaded registry, how agents
and analysis read that registry, how objects materialize to Ibis, and how the
multi-stage fail-closed validation model reports problems. It complements
[semantic-object-model.md](semantic-object-model.md) (object contracts) and
[authoring-workflow.md](authoring-workflow.md) (the write loop).

See also:

- [overview.md](overview.md) — the design goals these mechanics enforce.
- `../agent-friendly-public-surface.md` — the cross-module result protocol this
  layer implements.

## Registry and loader

A semantic project is one explicit boundary: a `models/semantic/` root with its
own registry and load lock. `ms.load(...)` executes the trusted local Python
files under that root, assembles the decorators' side effects into an in-memory
registry, and returns a `SemanticCatalog`.

```python
import marivo.semantic as ms

catalog = ms.load()                     # locate the nearest models/semantic/ upward
catalog = ms.load(workspace_dir="models/semantic", domains=["sales"])  # explicit root + filter
catalog.list("domain").show()
```

Loader rules:

- Each domain calls `ms.domain(name=..., owner=...)` once in
  `<root>/<domain>/_domain.py`, with `name` equal to the directory. The
  `_domain.py` is the domain entrypoint and may hold all of that domain's
  objects.
- Object identity comes from an explicit `domain=` or the domain's default
  domain (`default=True`), **not** from the file path. File paths are used only to
  discover candidate files and to run organization checks.
- Loading is **two-pass**: pass one collects all declarations, pass two resolves
  refs and validates dependencies. Filenames and sibling sort order do not affect
  whether a valid model loads.
- Model roots are **layered / multi-root**: a project can compose a shared base
  root with a local overlay.
- Python files are trusted local code and are not sandboxed. `find_project()`
  locates the nearest `models/semantic/` upward; an empty `models/semantic/` is a
  valid (empty) project. If the path exists but is not a directory, the loader
  fails closed.
- On success the registry is `ready`; on failure it becomes `errored` with
  structured `load_errors` retained for the fix loop.

## Reader and introspection

`ms.load()` returns a `SemanticCatalog` — the deterministic, agent-facing read
surface. It does not re-parse files or rely on process-global state, and it does
not use fuzzy or embedding-based recall.

```python
import marivo.semantic as ms

catalog = ms.load()
catalog.list("metric").show()                        # all metrics, every domain
catalog.list("metric", scope="domain.sales").show()  # metrics in one domain
catalog.list("dimension", scope="entity.sales.orders").show()
revenue = catalog.get("metric.sales.revenue")
revenue.details().show()                             # bounded details card
```

| API | Meaning |
|---|---|
| `ms.load(workspace_dir=None)` | Load the project and return a `SemanticCatalog`. |
| `catalog.get("<kind>.<semantic_id>")` | Resolve and validate one `SemanticObject`. |
| `catalog.list(kind, scope=None)` | Kind-first browse; `kind` is a string or `SemanticKind`. Top level searches every domain; `scope` narrows to a subtree. |
| `catalog.preview(ref, limit=..., context_columns=None)` | Bounded preview of an entity/dimension/time_dimension/measure/metric. |
| `catalog.readiness(refs=None)` | Structural readiness gate for handoff refs. |
| `ms.richness(demand=None)` | Advisory demand-ranked coverage/depth report. |

`catalog.get(...).details()` returns a structured details dataclass (not just
text). Every details type exposes `ref`, `kind`, `name`, `domain`, `context`,
`business_definition`, `guardrails`, `synonyms`, `examples`, `instructions`,
`owner_notes`, `python_symbol`, `source_location`, `parents`, `children`, and
`dependents`, plus type-specific facts (datasource `backend_type`/`fields`/
`env_refs`; entity `datasource`/`source`/`primary_key`/`versioning`; measure
`additivity`/`unit`; time dimension parse/granularity/timezone; metric
entity/composition/additivity/provenance/parity/unit; relationship join keys).
Secrets appear only as env-var *names* — a resolved secret value is never
rendered.

`ms.help(symbol=None)` is the module-level static contract helper, usable without
an active project. `ms.help("constraints")` is the single entry to the authoring
/ validation constraint catalog. Help describes what parameters must satisfy; it
carries no runtime data.

## Result contract

Every semantic result object follows the shared no-side-effect contract — the
methods **do not write stdout**; inspection is explicit and silent by default:

- `result.show()` — print a bounded result card and return `None`.
- `result.render()` — return the same bounded text without writing stdout.
- `repr(result)` — a one-line cold-start hint pointing to `.show()`.

Catalog browsing returns a `SemanticObjectList` (not a raw list); use `.objects`,
`.refs()`, `.render()`, and `.show()`. This is the semantic-layer instance of the
cross-module agent result protocol described in
`../agent-friendly-public-surface.md`.

## Materialization

Materialization recombines registered Python functions into Ibis objects. It is
an implementation detail of semantic internals and the analysis runtime — it is
**not** a public `SemanticProject` method. Agent-facing reads and previews go
through the catalog:

```python
catalog = ms.load()
revenue = catalog.get("metric.sales.revenue")
catalog.preview(revenue.ref).show()
```

Backend resolution rules:

- The compile target defaults to the `backend_type` of the metric's datasource.
- The backend is obtained through the internal datasource connection service; the
  live backend dialect must match the declared `backend_type` or the operation
  fails closed.
- With no live backend, a dry compiler for that `backend_type` is used when
  available; otherwise a structured `compile_error` is returned rather than
  executing a query.
- Multi-datasource metrics fail closed in compile and parity (federation is a
  separate design).

To inspect a metric's caliber without executing analysis, use
`catalog.preview(...)`, `catalog.get(...).details()`, and `ms.parity_check(...)`.

## Validation and failure semantics

The semantic layer validates in fail-closed stages. Each stage proves a
different class of contract; a stage that cannot prove its contract raises a
structured error instead of degrading.

### Decorator-time

Checks that a single declaration is locally self-consistent: duplicate
domain/datasource/entity/dimension/metric names; wrong ref types; illegal
cross-domain/cross-entity refs; an expression-bearing decorator with no explicit
`domain=` and no default domain in context; a base metric missing `entities=[...]`;
a derived metric that carries entity parameters, lacks composition components, or
reads an entity table in its body; a decorator/metadata call executed outside a
loader context; a metric body that violates the single-`return`-expression rule
or calls a decorated metric function / an Ibis SQL escape hatch.

### Load / assembly-time

After the loader executes project files, assembly validation checks cross-object
relationships: a missing or mismatched `_domain.py`; `ms.domain(...)` in the wrong
file or a `_domain.py` declaring multiple domains; an entity referencing an
unknown datasource; a metric referencing an unknown entity or component; a
cross-domain `ms.ref(...)` that is missing, type-mismatched, or cyclic; an
`entities=[...]` count that disagrees with the function arity; an hour time
dimension missing its required prefix; invalid relationship endpoints, join
dimension refs, entity membership, or arity. On failure the registry is `errored`
and retains `load_errors`.

### Runtime / materialization-time

Materialization executes user functions and composes Ibis objects. Failures come
from backend factories, missing Ibis tables/columns, user-function exceptions, or
incompatible expressions. A registered-but-failing object raises a runtime error —
never a "metric not found" error, which is reserved for genuinely absent objects.

### Parity-time

Parity compares SQL provenance against the Ibis expression. It can fail on
missing source SQL or dialect; a metric still `unverified` under a strict policy;
a missing datasource profile, unsupported backend type, or live/profile mismatch;
an inexecutable SQL or metric expression; a non-scalar side; or unequal scalars.
On a parity failure, locate the semantic difference first — do not simply widen
the tolerance.

### Static policy-time

Data-free policy checks: optional sample-uniqueness checks on entity primary keys
(non-blocking by default; unverified keys surface as warnings); a ban on
`backend.sql(...)` / raw-SQL escape hatches / dialect-specific SQL in metric
bodies (vendor differences belong in datasource compilation and parity, not in a
body). The SQL-escape-hatch check scans the materialized Ibis expression tree;
decorator-time only rejects obvious method names to avoid false positives on
ordinary column access.

## Error model

Errors are structured and teach: every typed error states what was expected, what
was received, and the concrete next step, with a stable `kind`, the `refs`
involved, a `source location`, and a human-readable hint. New exceptions subclass
`SemanticError`, carry structured fields, and render through the shared template
style. The mapping from error kind to agent action is mechanical:

| Error kind | Agent action |
|---|---|
| `duplicate_name` | Remove the duplicate declaration or change `name=`, then reload. |
| `missing_domain` | Add `ms.domain(...)` in `<root>/<domain>/_domain.py`, or pass an explicit `domain=`. |
| `missing_entity_ref` | Ensure the entity is declared; for forward references use a decorated ref or `ms.ref(...)`. |
| `invalid_decomposition` | Check that `ms.ratio(...)` / `ms.weighted_average(...)` components point to registered metrics. |
| `invalid_component_body` | Remove component calls from the metric body; use `ms.ratio`/`ms.weighted_average`/`ms.linear`. |
| `outside_loader_context` | Move the definition into `<root>/models/semantic/<domain>/<file>.py`; use scratch Ibis in notebooks. |
| `unverified_provenance` | Add `provenance=ms.from_sql(...)`, or stop and confirm the business caliber. |
| `sql_escape_hatch` | Move raw SQL to a persisted backend view exposed via `ms.table(...)`; keep the body Ibis. |

## Readiness and richness

Two gates sit at the end of the write loop:

- **`ms.readiness(refs=None)`** runs pure in-memory structural checks over the
  dependency closure of the given refs (or all objects). It is the required
  semantic gate before handing refs to analysis, and it never writes stdout.
  Runtime validation (connectivity) is separate: `catalog.preview(...)`,
  `ms.parity_check(...)`, and `ms.richness()`.
- **`ms.richness(demand=None)`** returns a demand-ranked `RichnessReport`. It is
  purely advisory — it never blocks and never mutates readiness — and seeds
  ranking from example questions, analysis intents, run-history refs, and the
  build purpose.

`ms.verify_object(ref)` (per-object) and `ms.parity_check(name)` (per-metric)
complete the validation surface; both return silent result objects with `.show()`
/ `.render()`.

## Relationship to analysis

The boundary is firm: `semantic` owns *what an object is, what its caliber is, and
how it materializes*; `analysis` owns *observe / compare / attribute / correlate
over those objects, with session persistence and lineage*. Analysis reads objects
through semantic refs and never re-defines a caliber, guesses an entity or time
dimension, or bypasses the registry to read a table directly. When an analysis
needs a new business object, extend `semantic` first, then let `analysis` consume
it — business definitions do not hide inside one-off analysis scripts.
