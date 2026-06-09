# Semantic Terminology Rename Design

Date: 2026-06-10

Status: draft

## Problem

Four of the seven `SymbolKind` terms carry significant terminology risk for
agent consumers:

- `model` collides with ML model (language model, trained model) — agents
  familiar with ML tooling will misinterpret a Marivo domain namespace as a
  machine-learning artifact.
- `dataset` collides with ML training/evaluation datasets (Hugging Face
  Dataset, tf.data.Dataset, torch Dataset) — agents will not associate this
  with a semantically-defined, queryable business entity.
- `field` is generic (form field, struct field, Protobuf field) and does not
  communicate that the object is a grouping dimension used in analysis.
- `time_field` inherits the ambiguity of `field`; BI tools consistently use
  `time_dimension` for this concept.

The result is that an agent browsing `catalog.list(kind="model")` or reading
`kind="dataset"` in a render has to reason past ML connotations before it can
understand what the object is for.

## Goals

- Replace all four ambiguous terms with unambiguous, BI-standard vocabulary
  across every layer: public authoring API, `SymbolKind` enum, internal IR
  and Ref classes, file-naming convention, error messages, tests, docs, and
  skills.
- Keep `metric`, `relationship`, and `datasource` unchanged — they carry no
  meaningful ambiguity in an analytics context.
- Make this a clean breaking change with no compatibility shim or deprecation
  period.

## Non-Goals

- No aliasing or backward-compatible wrapper.
- No migration helper for external authored semantic files. Callers update
  their files manually.
- No changes to `metric`, `derived_metric`, `relationship`, or `datasource`
  authoring or catalog behavior.

## Term Mapping

### Public authoring API

| Before | After |
|---|---|
| `ms.model(...)` | `ms.domain(...)` |
| `ms.dataset(...)` | `ms.entity(...)` |
| `ms.field(...)` | `ms.dimension(...)` |
| `ms.time_field(...)` | `ms.time_dimension(...)` |
| `ms.derived_metric(...)` | unchanged |
| `ms.metric(...)` | unchanged |
| `ms.relationship(...)` | unchanged |
| `ms.datasource(...)` | unchanged |

### `SymbolKind` enum and catalog kind strings

| Before | After |
|---|---|
| `SymbolKind.MODEL = "model"` | `SymbolKind.DOMAIN = "domain"` |
| `SymbolKind.DATASET = "dataset"` | `SymbolKind.ENTITY = "entity"` |
| `SymbolKind.FIELD = "field"` | `SymbolKind.DIMENSION = "dimension"` |
| `SymbolKind.TIME_FIELD = "time_field"` | `SymbolKind.TIME_DIMENSION = "time_dimension"` |

All `catalog.list(kind=...)` filters, render output, and error messages that
produce or consume kind strings update to the new values.

### Internal IR and Ref classes (`marivo/semantic/ir.py`)

| Before | After |
|---|---|
| `ModelIR` | `DomainIR` |
| `ModelRef` | `DomainRef` |
| `DatasetIR` | `EntityIR` |
| `DatasetRef` | `EntityRef` |
| `DatasetProvenance` | `EntityProvenance` |
| `DatasetVersioningIR` (type alias) | `EntityVersioningIR` |
| `DatasetSourceIR` (type alias) | `EntitySourceIR` |
| `FieldIR` | `DimensionIR` |
| `FieldRef` | `DimensionRef` |
| `FieldKind` | `DimensionKind` |
| `FieldKind.DIMENSION` | `DimensionKind.CATEGORICAL` |
| `FieldKind.MEASURE` | `DimensionKind.MEASURE` (unchanged) |
| `FieldKind.TIME` | `DimensionKind.TIME` (unchanged) |
| `TimeFieldRef` | `TimeDimensionRef` |

`MetricIR`, `MetricRef`, `RelationshipIR`, `RelationshipRef`, `DatasourceIR`
are unchanged.

### File-naming convention

| Before | After |
|---|---|
| `.marivo/semantic/<domain>/_model.py` | `_domain.py` |

The loader hard-codes the sentinel filename. `loader.py` changes `"_model.py"`
to `"_domain.py"` in all path construction and detection logic. All error and
constraint messages that mention `_model.py` or `ms.model()` update
accordingly.

## Execution Plan

All changes land in a single PR. Commits are ordered to keep `make typecheck`
passing at each step:

1. **`marivo/semantic/ir.py`** — rename `SymbolKind` values, all IR and Ref
   classes, `FieldKind` → `DimensionKind` with `DIMENSION` → `CATEGORICAL`.
   Update `__all__`.

2. **`marivo/semantic/` internal modules** — update all internal call sites to
   use new class names and enum values:
   `authoring.py`, `loader.py` (including `_model.py` → `_domain.py`),
   `constraints.py`, `catalog.py`, `reader.py`, `ledger.py`,
   `classifier.py`, `validator.py`, `readiness.py`, `help.py`,
   `auto_record.py`, `dtos.py`.

3. **`marivo/semantic/__init__.py`** — update exports to new names; remove old
   names entirely.

4. **`tests/`** — batch-replace all `ms.model`, `ms.dataset`, `ms.field`,
   `ms.time_field` authoring calls; update kind-string assertions
   (`"model"` → `"domain"` etc.). Approximately 726 authoring call sites and
   42 enum references.

5. **`docs/` and `marivo-skills/`** — update spec documents (including
   `2026-06-09-semantic-catalog-public-api-design.md`), skill references, and
   help text.

## Verification

CI gates (must all pass before merge):

```bash
make typecheck   # no references to old class names survive
make test        # all kind-string assertions and authoring calls updated
make lint
```

Pre-merge manual scan to confirm no old terms survive in source:

```bash
grep -rn 'ms\.model\|ms\.dataset\|ms\.field\|ms\.time_field' marivo/ tests/
grep -rn '"model"\|"dataset"\|"field"\|"time_field"' marivo/semantic/
grep -rn '_model\.py' marivo/
grep -rn 'ModelIR\|DatasetIR\|FieldIR\|TimeFieldRef' marivo/ tests/
```

All four commands must return no output before the PR is merged.

## Acceptance Criteria

- `ms.domain(...)`, `ms.entity(...)`, `ms.dimension(...)`,
  `ms.time_dimension(...)` are the only public authoring names.
- `catalog.list(kind="domain")`, `kind="entity"`, `kind="dimension"`,
  `kind="time_dimension"` are the only catalog kind strings.
- No Python source file in `marivo/` or `tests/` references the old class
  names, enum values, or authoring function names.
- The loader requires `_domain.py`; a directory with only `_model.py`
  produces a clear error naming `_domain.py`.
- `make typecheck` and `make test` pass.
