# Marivo's Agent-Friendly Public Surface

Status: synthesis design note. This document explains the design thinking behind
Marivo's public Python surface — why it is shaped the way it is, how it is
layered, and how each core API progressively discloses itself to a coding agent.
It is a conceptual overview, not an API reference. The generated API reference
lives under [`docs/api/`](../api/README.md) and in each surface's `help()`
output; the per-surface contracts live in
[`specs/semantic/overview.md`](semantic/overview.md)
and [`specs/analysis/python-analysis-design.md`](analysis/python-analysis-design.md).

It synthesizes several committed design documents rather than introducing new
behavior. Where a claim traces to a specific design, that design is linked
inline. When code and this note disagree, the linked design and the code are the
sources of truth.

## Who the reader is

Marivo's public surface is the Python library, exposed as exactly three modules:

```python
import marivo.datasource as md   # physical connections + datasource evidence
import marivo.semantic  as ms    # the business-object contract
import marivo.analysis  as mv    # typed, composable analysis operators
```

The primary consumer of this surface is not a human at a REPL. It is a coding
agent — Claude Code, Codex, and their kin — operating a **write → run → read →
decide** loop: the agent writes a short Python script, runs it, reads the
output, and decides the next step. Every design choice below follows from taking
that reader seriously.

Three consequences drive the whole design:

1. **Context is scarce and expensive.** An agent's working memory is its context
   window. Unbounded or unexpected terminal output is not a cosmetic problem; it
   is context pollution that can crowd out the task. The surface must never dump
   data the caller did not ask for.
2. **The agent cannot be trusted to remember a private DSL.** It reasons from
   what it can read at the call site — signatures, `repr()`, error messages,
   bounded result cards. So the surface must **teach itself from real state**,
   over and over, rather than assuming prior knowledge.
3. **The agent, not the library, owns business judgment.** Marivo is a
   deterministic analysis kernel: it exposes typed computation, evidence,
   lineage, contracts, and fail-closed errors. It never decides the user's next
   business step or acts as a planner. That boundary is a feature — it keeps the
   library's promises small and verifiable.

The single organizing rule that falls out of this is a division of labor:

> **The library owns the contract. The skill owns the boundaries. The agent owns
> the judgment.**

Everything else in this document is an elaboration of that sentence.

## The core principles

The public surface is governed by a small set of invariants (the "Agent-Facing
Surface Principles" in [`agent-guide.md`](../../agent-guide.md), plus the result
contract from the
[agent-friendly public API design](../superpowers/specs/2026-06-09-agent-friendly-public-api-design.md)).
They are review criteria and, increasingly, test-enforced contracts:

- **Errors teach.** Every typed error states what was expected, what was
  received, and the concrete next step — and its suggestions are built from real
  state (catalog contents, nearby ids), never hardcoded. There is no silent
  fallback: an operation that cannot prove its contract fails closed with a
  structured error rather than guessing.
- **One path per capability.** Each task has exactly one public entry point.
  Nothing described as "internal — use X instead" may appear in a module's
  `__all__`.
- **`__repr__` is the floor.** Every public result type has a bounded,
  single-line `repr()` that carries kind and identity and points to `.show()`.
  Default dataclass/pydantic reprs — which dump every field — are not acceptable
  on a public result type.
- **Terminal results share one protocol.** Every object an agent stops to read
  implements the same bounded inspection surface, so the agent learns it once
  and reuses it everywhere.
- **Surface growth is gated.** Public `__all__` sets are pinned by snapshot
  tests. Adding a public symbol is a deliberate, reviewed decision, not an
  incidental export.
- **Discovery is progressive and bounded.** `help()` is a short, family-grouped
  index; detail is reached by drilling in, never by dumping a full catalog.
- **Precise types over optional-field mega-classes.** The surface prefers one
  entry shape with closed, kind-dispatched variants (e.g. `MetricFrame[time_series]`)
  over a single class riddled with optional fields. Precise types fail loudly at
  the call site; optional-field unions fail silently downstream.

## The no-side-effect result contract

The most load-bearing principle deserves its own section, because it is what
makes the write-run-read loop *safe*. It comes from the
[agent-friendly public API result design](../superpowers/specs/2026-06-09-agent-friendly-public-api-design.md).

**Result-producing APIs compute and return; they never print.** Help APIs are
the sole, explicit exception: calling `help(...)` *is* the inspection action, so
it prints bounded text and returns `None`.

```python
frame = session.observe(revenue, time_scope={"start": "2026-06-01", "end": "2026-06-08"})
# stdout is unchanged — nothing printed
frame
# <MetricFrame ref=frame_ab12 metric=sales.revenue rows=7; call .show() to inspect>
frame.show()
# ...bounded result card printed here, on purpose...
```

Why this asymmetry is the right default:

- If an agent *forgets* to inspect a result, the script stays quiet and
  recoverable. The returned object still carries a one-line `repr()` that points
  to `.show()`, so a silent result is never a dead end.
- If an API printed *by default*, a single missed opt-out in a multi-step script
  could flood the agent's context with intermediate results — exactly the
  failure mode the surface exists to prevent.

Runtime safety therefore does not depend on every generated script remembering a
"quiet" flag. Quiet is the floor, not an option.

### The three-method floor, and the bounded card

Every terminal result — an object an agent stops to read — conforms to a shared
structural protocol (`AgentResult` in `marivo/render.py`), enforced by a
contract test rather than a base class
([agent result surface design](../superpowers/specs/2026-06-13-agent-result-surface-design.md)):

```python
repr(result)     # one line: kind + identity + "call .show() to inspect"; no IO
result.render()  # the same bounded plain-text card, returned as a string, no IO
result.show()    # prints render() + newline, returns None
```

`show()`/`render()` emit a **bounded result card**, not a data dump. The card has
a fixed section order — an identity line, status, columns and a few preview rows
when tabular, and an `available:` footer — routed through one shared renderer
(`format_bounded_card`). It targets a size budget (roughly: a few preview rows,
under ~80 lines total). When there is more data than the budget allows, the card
shows an explicit, actionable truncation hint; it never silently omits data.

The `available:` footer is a **static "next callable" list bound to the type**,
not a recommendation engine. It reminds the agent which bounded methods exist on
the object it already holds (e.g. `.to_pandas()` on a frame) — it does not
suggest what to do next for the user's task. State-derived suggestions ("unknown
metric → here are the available ids", "did you mean X") are a *separate*
mechanism that appears on **error objects** and on **empty/ambiguous results**,
always generated from a live query, never hardcoded.

This split — a quiet, typed return; a one-line `repr()` for cold-start
discovery; one bounded `show()` for deliberate inspection; state-derived help
only when something is wrong — is what lets an agent run a ten-step script
without drowning, and still recover the moment it inspects any object.

## Three surfaces, one pipeline

The public surface is deliberately just three modules, because they are the
three stages of one pipeline. Refs flow strictly forward; nothing downstream
depends on the file layout or internals of anything upstream.

| Surface | Alias | Answers | Produces |
|---|---|---|---|
| `marivo.datasource` | `md` | "What physical connections exist, and what do their tables actually look like?" | datasource refs, connections, and bounded **evidence** (`DatasourceResult`) |
| `marivo.semantic` | `ms` | "Which stable business objects can downstream analysis reference?" | typed semantic refs + a loadable `SemanticCatalog` |
| `marivo.analysis` | `mv` | "What did the metric do, and why?" | typed analysis frames/results over a `Session` |

> **Catalog object navigation:** The semantic catalog exposes typed collections
> (`catalog.domains`, `catalog.metrics`, etc.) and concrete catalog objects
> (`Domain`, `Entity`, `Metric`, …). See
> [`Catalog Object Navigation Design`](../superpowers/specs/2026-07-10-catalog-object-navigation-design.md).

The hand-offs are typed and one-directional:

```text
md (physical)  ──evidence──▶  ms (business contract)  ──semantic refs──▶  mv (typed analysis)
```

- `md` supplies the *physical facts* an agent needs to author semantics, but it
  authors nothing and infers no business meaning.
- `ms` turns those facts into an explicit, statically-readable business contract.
  Python files are the source of truth; business meaning is never guessed from
  column names, table names, or natural language.
- `mv` consumes only stable semantic refs and materialized expressions. It never
  reaches into a user project's Python file layout.

Because there are exactly three surfaces and each is snapshot-gated, an agent can
hold the whole public vocabulary in mind. Surface growth is a reviewed event:
`tests/test_public_surface.py` pins each `__all__`, and `help()` folds
supporting types (refs, detail shapes, IR) into families so the top-level index
stays short even as the surface grows.

## Guidance layering: contract vs. process vs. judgment

Agent guidance lives in three places, and the boundaries between them are
enforced, not aspirational
([skill/library surface coordination](../superpowers/specs/2026-06-13-skill-library-surface-coordination-design.md)).

- **The library owns the contract.** Anything derivable from real state at the
  call site is the library's to emit: signatures, field lists, valid next
  actions, constraints, runnable examples, and the meaning-plus-fix of an error.
- **The skill owns the boundaries.** Hard boundaries, handoffs, evidence
  continuity, and closeout obligations.
- **The agent owns planning and judgment.** Which capability to reach for and
  in what order, when to stop and read versus bundle a chain, session
  discipline, and final-report synthesis.

The test that keeps these separate is the **eviction test**: for any line in a
skill file, ask *"could the library teach this from real state at the call
site?"* If yes, it is contract — delete it from the skill and repoint to
`help('<x>')` or the structured error. If no (it requires cross-object or
cross-step judgment), it is process and it stays. Applied at review time — and
backed by deterministic skill-shape, live-help, and API-drift tests — the test
keeps field tables and error catalogs from re-accumulating as a second,
drift-prone source of truth. The goal is not zero redundancy — orienting prose
and decision trees are valuable — but zero *drift-prone* duplication of a
contract the code already emits.

This layering is instantiated once per domain:

- **Authoring** ([authoring guidance layering](../superpowers/specs/2026-06-26-authoring-guidance-layering-design.md)):
  `ms.help(...)` owns the static authoring contract (constructor, required and
  optional parameters, types, defaults, omit rules, parse shapes). `md.discover_*`
  owns runtime datasource **evidence** only — profiles, detected formats,
  issues — never parameter tables or semantic-selection judgments. Discovery
  deliberately dropped names like `candidates` and `judgment_targets` precisely
  because they implied a selection the library must not make.
- **Analysis**: environment-verified live surfaces own capabilities and runtime
  guidance. `mv.help(...)` and the CLI route `python -m marivo help analysis`
  own the static analysis contract. Frames and results own *dynamic* guidance:
  `show()` describes the current state, `contract()` describes the mechanically
  valid next actions, and structured errors own repair guidance. The
  `marivo-analysis` skill owns hard boundaries, handoffs, evidence continuity,
  and closeout obligations. The agent owns planning and judgment.

## Progressive disclosure: how a core API reveals itself

"Progressive disclosure" is the concrete mechanism behind "teach yourself from
real state." No surface hands the agent everything at once; each layer reveals
exactly enough to take the next step, and the next layer is always one obvious
call away.

There are three disclosure ladders, and they compose:

1. **Static contract, on demand.** `help()` opens as a compact, typed directory
   (~2–3 KB), not a 70 KB manual
   ([help progressive disclosure](../superpowers/plans/2026-06-02-help-progressive-disclosure.md)).
   The agent drills from the index into a symbol, and only then sees full
   parameters, constraints, and a runnable example.

   ```python
   ms.help()                     # compact top-level index, grouped by family
   ms.help("time_dimension_column")  # full authoring contract for one constructor
   mv.help()                     # the analysis capability index, then mv.help("observe") for one operator
   ```

   `help()` prints bounded text and returns `None`. It has exactly one output
   shape — it does not accept `format=` or emit JSON-as-a-payload; structured
   data comes from result-producing APIs, never from help.

2. **Dynamic state, from the object in hand.** A returned result discloses in
   three steps of increasing commitment: `repr()` (free, one line) →
   `show()` (bounded card) → typed escape hatch (`to_pandas()`, `.refs`)
   when the agent genuinely needs the full data.

3. **Repair, only on failure.** Errors escalate detail exactly when something
   breaks: a structured exception carries the expected/received/next-step and
   suggestions drawn from live state, so the agent never pays for a repair
   catalog it did not need.

These ladders show up as two end-to-end loops.

### The authoring loop (semantic)

```text
help → inspect → explicit scope → sample once → project evidence →
settle/grill → author → load typed object → verify → preview → readiness
```

Each step discloses just enough for the next:

```python
# 1. help — learn the static contract for the object you're about to author
ms.help("time_dimension_column")

# 2. inspect and sample once under an explicit scope
inspection = md.inspect(warehouse, md.table("orders"))
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=1000, timeout_seconds=30),
    columns=("dt",),
)

# 3. project query-free physical evidence (no authoring, no judgment)
evidence = snapshot.time_dimensions(columns=("dt",))
evidence.show()

# 4. settle / grill — reconcile help + evidence + catalog + project docs + user
#    answers into concrete parameter values; ask the user only for policy the
#    evidence cannot decide (e.g. failure/ratio-denominator/time-axis calls)

# 5. author — one object, in a Python _domain.py file (the source of truth)
dt = ms.time_dimension_column(
    name="order_date", entity=orders, column="dt",
    granularity="day", parse=ms.strptime("%Y%m%d"),
)

# 6. reload the typed object, verify statically, then preview against the snapshot
catalog = ms.load()
dt_ref = catalog.require(ms.Ref.time_dimension("sales.orders.order_date")).ref
catalog.verify(dt_ref).show()
catalog.preview(dt_ref, using=snapshot).show()

# 7. readiness — zero-query certification for the authored change
catalog.readiness(refs=[dt_ref]).show()
```

The disclosure discipline here is what makes authoring safe for an agent: the
static contract (`help`) and the physical evidence (`snapshot` projections) are *different*
surfaces with *different* jobs, so the agent never confuses "what parameters
exist" with "what the data looks like," and never mistakes evidence for a
recommendation to author.

### The analysis loop

```text
help() → load catalog → observe → show → contract → compose → closeout
```

Analysis deliberately narrows the agent's mental model to **two exits per
artifact** ([frame/result interface simplification](../superpowers/specs/2026-06-28-frame-result-interface-simplification-design.md)):

```python
session    = mv.session.get_or_create(name="revenue_drop")
catalog    = session.catalog
revenue    = catalog.require(ms.Ref.metric("sales.revenue")).ref
created_at = catalog.require(ms.Ref.time_dimension("sales.orders.created_at")).ref

cur  = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-10-01"}, grain="month")
base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-10-01"}, grain="month")

cur.show()                                              # observe the current bounded result
delta = session.compare(cur, base, alignment=mv.window_bucket())
delta.contract()                                        # machine-readable compatibility before the next operator
attribution = session.attribute(delta, axes=[created_at])
attribution.show()
```

Short rule: **after each analysis step, read `show()`; before composing another
operator, read `contract()`; use `to_pandas()` only for terminal custom work.**
Everything else that used to be a near-peer exit — `summary()`, `schema()`,
`preview()`, `next_intents()` — was removed from the public frame surface so the
agent never has to choose a reading order before doing real work.

Crucially, `contract().affordances` are **neutral mechanical compatibility
facts**, not ranked recommendations. They say "this operator *can* be wired to
this frame," never "this is what you *should* do." The choice of which valid
action matters for the user's question stays with the agent — the boundary from
[the first section](#who-the-reader-is) holds all the way down to the last
method call.

## The result vocabulary

Because every operator returns a typed artifact from a fixed family, the agent
learns a small, stable vocabulary rather than a menu of ad-hoc shapes
([python analysis design](analysis/python-analysis-design.md)):

| Producer | Family |
|---|---|
| `observe` | `MetricFrame` |
| `compare` | `DeltaFrame` |
| `attribute` | `AttributionFrame` |
| `discover` | `CandidateSet` |
| `correlate` | `AssociationResult` |
| `hypothesis_test` | `HypothesisTestResult` |
| `forecast` | `ForecastFrame` |
| `assess_quality` | `QualityReport` |

Two rules keep this vocabulary trustworthy:

- **A public operator's output family is fixed.** Parameters may change the
  algorithm, grain, scope, or policy — never the output family. A capability that
  would return different families under different parameters is split into
  several operators rather than hidden behind one ambiguous entry point.
- **Within a family, shapes are closed, kind-dispatched variants** —
  `MetricFrame[scalar]`, `MetricFrame[time_series]`, `MetricFrame[panel]`,
  `CandidateSet[driver_axis]`, and so on. A closed enum of shapes fails loudly at
  the boundary; an optional-field mega-class would fail silently three steps
  later. On the semantic side the same idea appears as `CatalogCollection` /
  `CatalogEntry` for discovery, with one sealed generic `Ref[kind]` value
  flowing into analysis.

## What this buys, and what keeps it true

Taken together, the design delivers three properties an agent depends on:

- **Context safety** — quiet-by-default results plus bounded cards mean a
  multi-step script never floods the context window.
- **A self-teaching surface** — `help()`, `repr()`, `show()`/`contract()`, and
  structured errors mean the agent re-derives the contract from real state at
  every call, instead of relying on stale memory.
- **Low drift** — the library is the single source of every contract fact, and
  skills route to it by pointer, so guidance and code cannot silently diverge.

These are not left to good intentions. They are pinned by tests, in the same
snapshot-with-allowlist spirit throughout:

- `tests/test_public_surface.py` — each `__all__` is an explicit, reviewed
  allowlist.
- `tests/test_agent_result_protocol.py` — every terminal result conforms to the
  bounded `repr`/`render`/`show` protocol, and every `available:` entry names a
  real method.
- `tests/test_introspection_help_folding.py` — the top-level `help()` family
  partition is pinned, so new symbols are classified deliberately.
- `tests/test_marivo_analysis_skill_contract.py` — packaged skills remain
  bounded one-file routing kernels with no deleted attachment paths.
- Focused live-help and API-drift tests — runnable help examples and mechanical
  contracts stay aligned with the real surface.

## Where to go next

- Result and inspection contract:
  [agent-friendly public API result design](../superpowers/specs/2026-06-09-agent-friendly-public-api-design.md)
- Shared result protocol and surface gating:
  [agent result surface design](../superpowers/specs/2026-06-13-agent-result-surface-design.md)
- The two-exit frame model:
  [frame/result interface simplification](../superpowers/specs/2026-06-28-frame-result-interface-simplification-design.md)
- Authoring layering (help vs. discover vs. verify):
  [authoring guidance layering](../superpowers/specs/2026-06-26-authoring-guidance-layering-design.md)
- Skill vs. library division of labor:
  [skill/library surface coordination](../superpowers/specs/2026-06-13-skill-library-surface-coordination-design.md)
- Per-surface contracts:
  [semantic and datasource overview](semantic/overview.md),
  [python analysis design](analysis/python-analysis-design.md)
- The committed invariants: the "Agent-Facing Surface Principles",
  "Authoring Guidance Layering", and "Analysis Guidance Layering" sections of
  [`agent-guide.md`](../../agent-guide.md).
