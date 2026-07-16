# Loading, Validation, and Introspection

Status: design. This document describes the runtime side of
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
catalog.domains.show()
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
catalog.metrics.show()

sales = catalog.domains.get("sales")
orders = sales.entities.get("orders")
orders.dimensions.show()

revenue = catalog.get("metric.sales.revenue")
revenue.details().show()
```

`SemanticCatalog` exposes one global collection per object type:
`catalog.domains`, `catalog.datasources`, `catalog.entities`,
`catalog.dimensions`, `catalog.time_dimensions`, `catalog.measures`,
`catalog.metrics`, and `catalog.relationships`. Each is a
`CatalogCollection[T]` with `.items`, `.ids()`, `.refs()`, `.get(key)`,
`.render()`, `.show()`, `len()`, and iteration. `catalog.get(typed_id)` is the
exact lookup entry point for IDs obtained from errors, logs, or persisted state.

| API | Meaning |
|---|---|
| `ms.load(workspace_dir=None)` | Load the project and return a `SemanticCatalog`. |
| `catalog.get("<typed_id>")` | Resolve and validate one `CatalogObject` by typed ID. |
| `catalog.domains`, `catalog.metrics`, … | Typed global collections; each supports `.items`, `.ids()`, `.refs()`, `.get(key)`, `.show()`. |
| `catalog.verify_object(obj)` | Static, zero-query validation of one typed catalog object. |
| `catalog.preview(obj, using=snapshot_or_mapping)` | Scoped runtime preview bound to matching snapshot evidence. |
| `catalog.readiness(refs=[obj])` | Zero-query readiness gate scoped to typed handoff objects. |
| `ms.richness(demand=None)` | Advisory demand-ranked coverage/depth report. |

### Navigation matrix

Navigation is limited to explicit ownership or applicability relationships. Each
container object exposes typed collection properties:

| Object | Navigation properties |
|---|---|
| `Domain` | `entities`, `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships` |
| `Datasource` | `entities` |
| `Entity` | `dimensions`, `time_dimensions`, `measures`, `metrics`, `relationships` |
| `Relationship` | `from_entity`, `to_entity` |
| `Dimension` / `TimeDimension` / `Measure` / `Metric` | leaf objects — use `details()` for dependency information |

Scoped collections are the normal way to remove ambiguity:
`catalog.domains.get("sales").entities.get("orders").dimensions.get("region")`.

### Self-teaching object cards

Every container object's bounded `render()` / `show()` card advertises its live
navigation properties and counts. A domain card includes a `navigation:` section
listing each valid child collection with its count, so the agent discovers
`.entities`, `.metrics`, etc. from real state rather than memorizing a matrix.
Leaf object cards advertise `details()`, `render()`, and `show()`.

### Lookup rules

`CatalogCollection.get(key)` accepts an exact typed ID or a local name that is
unique within that collection view. It rejects bare semantic IDs. If a short
name is ambiguous, lookup raises a structured error listing bounded typed-ID
candidates. `catalog.get(...)` accepts only typed IDs; rejected short names are
still searched for teaching-error suggestions but never resolved implicitly.

### Structured lookup errors

Catalog lookup errors follow the shared semantic error model. They state the
expected input, received input, relevant scope, and a concrete next call derived
from the loaded index:

- **Ambiguous short name:** list bounded typed-ID candidates and show
  `collection.get("<typed-id>")`.
- **Wrong object type:** identify the typed ID's real type and point to the
  corresponding global collection.
- **Outside current scope:** state that the object exists globally, identify its
  owning path, and show the valid scoped or global lookup.
- **Not found:** show bounded close matches from the current collection.

`catalog.get(...).details()` returns a structured details dataclass (not just
text). Every details type exposes `ref`, `kind`, `name`, `domain`, `context`,
`business_definition`, `guardrails`, `python_symbol`, `source_location`,
`parents`, `children`, and
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

Catalog browsing returns a `CatalogCollection` (not a raw list); use `.items`,
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
catalog.verify_object(revenue).show()
catalog.preview(revenue, using=snapshot).show()
catalog.readiness(refs=[revenue]).show()
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

To inspect a metric's caliber without executing analysis, use typed details and
static verification. Use `catalog.preview(..., using=...)` for a scoped runtime
check. Parity is a separate potentially unbounded provenance SQL diagnostic.

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
| `outside_loader_context` | Move the definition into `<root>/models/semantic/<domain>/<file>.py`; use `md.raw_sql(...)` for ad-hoc queries outside the semantic model. |
| `unverified_provenance` | Add `provenance=ms.from_sql(...)`, or stop and confirm the business caliber. |
| `sql_escape_hatch` | Use `md.raw_sql(...)` for terminal raw SQL execution; raw SQL in semantic expression bodies is still rejected by the validator. |

## Readiness and richness

Two checks sit at the end of the write loop:

- **`catalog.readiness(refs=[obj])`** runs pure in-memory checks over the
  dependency closure of typed catalog objects selected for certification. It is
  the explicit certification and diagnostic at the end of an authoring change,
  never writes stdout, and never queries. Analysis APIs do not invoke it
  automatically.
  `catalog.preview(..., using=...)` persists scoped runtime metadata that
  readiness consumes; readiness enforces structurally matching preview evidence for
  executable families (`static_only`, `single_snapshot`, `snapshot_mapping`).
  Snapshot and preview age are retained as reference metadata and never block
  readiness or trigger implicit reacquisition.
  A native `ms.datetime()` or `ms.timestamp()` axis without `timezone=` is a
  blocker (`undeclared_naive_time_axis`): runtime would otherwise fall back to
  the datasource read timezone while report windows use the analysis-session
  timezone. Its structured repair requires declaring the source timezone; the
  zero-query gate does not guess or probe either runtime timezone.
- **`ms.richness(demand=None)`** returns a demand-ranked `RichnessReport`. It is
  purely advisory — it never blocks and never mutates readiness — and seeds
  ranking from example questions, analysis intents, run-history refs, and the
  build purpose.

`catalog.verify_object(obj)` completes the static per-object surface. A current
`VerifyResult` proves that one explicit check passed, but verification is
**result-local**: it is not persisted as a workflow checkpoint and is not a
runtime prerequisite for preview. The `marivo-semantic` skill enforces
verify-before-preview as a policy edge; the runtime does not consume a
`VerifyResult` in `catalog.preview(...)` or `catalog.readiness(...)`.
`ms.parity_check(name)` is an optional potentially unbounded diagnostic and never
a readiness requirement. All three return silent result objects with `.show()` /
`.render()`.

### Typed analysis handoff

`ReadinessReport` exposes
`analysis_handoff: SemanticToAnalysisHandoff | None`. It is `None` when no
requested ref is analysis-ready or when a blocker applies to the requested
handoff set. The field type is a module-internal handoff value, not a top-level
constructor or public `__all__` entry; agents consume it from the result field,
they do not construct or import it as an authoring API.

When present, the handoff carries the exact analysis-boundary help target, ready
refs, readiness status, project/catalog/environment fingerprints, warning ids,
preview-evidence ids, and caveats. It records identifiers and row-free evidence
metadata only — it never embeds preview rows, credentials, or plaintext sampled
values. The in-memory environment fingerprint retains exact paths for validation;
`ReadinessReport.show()` and `.to_dict()` mask them under the shared privacy rule
(only root help and explicit environment-mismatch diagnostics render raw paths).

The agent routes the handoff to the registered analysis
`boundary.semantic_handoff` target. Its sole public receiver,
`Session.validate_semantic_handoff(handoff)`, validates the payload against a
newly created, current, or recovered analysis session — checking environment
identity, project/catalog fingerprints, ref existence and kind, current
readiness, warning-id consistency, and preview-evidence existence and ownership — without
querying a datasource, opening a connection, or mutating state. Success returns
a `SemanticHandoffReceipt`; a stale environment, project, catalog, ref,
readiness, or preview-evidence fact emits typed semantic repair instead. The
receipt is in-memory only and is not persisted. Analysis consumes the handed-off
refs only from a successful receipt, whether this is first entry or re-entry.

## Relationship to analysis

The boundary is firm: `semantic` owns *what an object is, what its caliber is, and
how it materializes*; `analysis` owns *observe / compare / attribute / correlate
over those objects, with session persistence and lineage*. Analysis reads objects
through semantic refs and never re-defines a caliber, guesses an entity or time
dimension, or bypasses the registry to read a table directly. When an analysis
needs a new business object, extend `semantic` first, then let `analysis` consume
it — business definitions do not hide inside one-off analysis scripts.

The crossing is a typed handoff, not a bare ready-ref or a conceptual hand-wave.
Semantic readiness produces a `SemanticToAnalysisHandoff` (see
[Readiness and richness](#typed-analysis-handoff)); analysis consumes the
handed-off refs only after `Session.validate_semantic_handoff(...)` returns a
`SemanticHandoffReceipt`. A missing required semantic object activates
`marivo-semantic` and returns to the same semantic entry, requiring matching scoped
readiness before resuming.
