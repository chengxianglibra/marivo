# Lazy Analysis Dataset Core Design

Date: 2026-09-01

Revised: 2026-09-07

Status: accepted

Filter-selector amendment status: accepted

## Outcome

Define the one public value model shared by every lazy `marivo.analysis`
capability.

A reviewer can determine, without knowing the planner or storage
implementation:

- whether a public analysis value is a Dataset;
- which nominal Dataset family it belongs to;
- what one row means and which fields identify a row;
- which facts are available before execution;
- whether it is a Logical Dataset or its paired Materialized Dataset;
- how an operator names one exact current field without reading a column;
- which operations remain lazy and which calls execute;
- why the state-specific actions are `execute()` on Logical Datasets and
  `show()` plus `to_pandas()` on Materialized Datasets, while downstream
  operators remain available on both states;
- how materialization preserves analytical meaning while replacing the private
  upstream computation with an immutable scan leaf.

This document is the Module 1 authority named by
[`2026-09-01-lazy-analysis-design-decomposition-plan.md`](2026-09-01-lazy-analysis-design-decomposition-plan.md).
It refines the Dataset invariants in
[`2026-09-01-lazy-analysis-dataset-dsl-design.md`](2026-09-01-lazy-analysis-dataset-dsl-design.md).

## Ownership Boundary

This document owns:

- the public `Dataset` base abstraction;
- nominal Dataset families and their closed registration mechanism;
- the common immutable Dataset value contract and paired logical/materialized
  state-type surface;
- the closed public field, type-state, shape, row-contract, row-set,
  row-bound, cardinality, ordering, coordinate, and ordered-schema descriptors;
- the selector-only `DatasetFields` and `DatasetFieldRef` seam over those
  descriptors;
- execution-Session context, original Artifact ownership, and explicit
  cross-Session Materialized inputs;
- logical definition identity and deterministic lineage boundaries;
- the logical/materialized public state types;
- the generic operator construction protocol;
- the public `LogicalDataset.execute()` action and Materialized Dataset reads;
- bounded `repr`, `show`, and `contract` behavior;
- the public/private seam through which a materialized Dataset becomes a scan
  leaf.

It does not own:

- Entity Population inference, Metric binding, aggregation, or coordinate
  transitions;
- family-specific analytical row meanings beyond the common row-contract
  protocol;
- family-specific operator inventory such as `where`, `limit`,
  `with_dimensions`, or `aggregate`;
- predicate vocabulary, boolean composition, filter effects, or family-specific
  filter admission;
- statistical operator admission or algorithms;
- private logical and physical node schemas;
- datasource capability negotiation, generated SQL, or placement;
- Run transaction order, Artifact schemas, Evidence production, storage
  receipts, or recovery algorithms.

Those details belong to later modules. This document defines only the seams
they must consume.

## Upstream Invariants

The Dataset core accepts these north-star decisions as fixed:

1. every public analysis operator returns a Dataset;
2. operators are lazy, `LogicalDataset.execute()` executes, and Materialized
   Dataset reads never replay a logical origin;
3. plans, expressions, SQL, tasks, futures, and receipts remain private;
4. `execute()` is the only public logical execution action;
5. execution returns the paired Materialized Dataset while preserving family,
   shape, row contract, row-set contract, and public schema;
6. arbitrary pandas, SQL, or Ibis values cannot re-enter typed analysis;
7. every Dataset is owned by exactly one Session;
8. no eager aliases, lazy/eager overloads, or duplicate Session and Dataset
   operator paths survive the cutover.

## Decision Summary

### Use `Dataset`, not `AnalysisDataset`

The common public abstraction is named `Dataset` and is exported as
`marivo.analysis.Dataset`.

`AnalysisDataset` is rejected because the containing namespace already supplies
the analysis qualifier. The longer name would propagate into annotations,
errors, Help, and every concrete family without disambiguating a real public
collision.

`Dataset` is:

- public for `isinstance` checks, annotations, Help, and shared operator plus
  state-specific action contracts;
- abstract and not directly constructible;
- sealed to the Marivo-owned family registry;
- never used as an untyped catch-all return annotation when a concrete family
  is known.

Public APIs return `MetricDataset`, `DeltaDataset`, `AssociationDataset`, and
other nominal families. They return `Dataset` only when a genuinely heterogeneous
closed family is the contract, such as an Artifact recovery read whose exact
family is discovered from committed metadata.

### Use nominal Python families and runtime row contracts

Notation such as `MetricDataset[entity]` and `EventDataset[journey]` is analysis
type algebra in architecture documents. It is not a requirement that public
Python values implement subscripted generics.

The Python surface uses:

```python
features: mv.MetricDataset
association: mv.AssociationDataset
journeys: mv.EventDataset
```

The exact family-specific shape is the closed `DatasetShapeId` value in
`dataset.row_contract.shape_id`. This is deliberate:

- semantic Entity and Dimension identities are catalog values, not Python
  classes suitable for generic parameters;
- Python cannot soundly narrow a value from a catalog-dependent runtime shape;
- false static precision would still require the same runtime admission checks;
- nominal families keep public annotations specific without creating dozens of
  marker types used only to satisfy type syntax.

Static overloads may distinguish nominal input and output families. They must
not pretend to prove catalog-dependent shape compatibility. Shape-specific
admission remains closed, deterministic runtime validation with typed errors.

### Use paired logical and materialized state types in one family

Logical and materialized remain states of one analytical family, but their
public method sets must differ. Every family registration therefore owns a
paired logical and materialized class:

```python
logical = session.observe(metrics=[revenue])
checkpoint = logical.execute()

assert isinstance(logical, mv.LogicalMetricDataset)
assert isinstance(checkpoint, mv.MaterializedMetricDataset)
assert logical.row_contract == checkpoint.row_contract
assert logical.row_set_contract == checkpoint.row_set_contract
assert logical.kind == checkpoint.kind == "metric"
```

`LogicalDataset` and `MaterializedDataset` are public abstract state bases.
`LogicalMetricDataset` and `MaterializedMetricDataset`, for example, are the
paired concrete classes for the `metric` family. State does not create a second
analytical family: both classes use the same `family_id`, shapes, row-contract
validator, selectors, and family-owned operators.

An operator invoked on either state always returns a Logical Dataset for its
output family. Only `execute()` crosses from logical to materialized. This
makes invalid reads absent from the public type surface rather than late
runtime errors.

### Every Dataset has complete logical row and row-set contracts before execution

Dataset construction must determine, without datasource execution:

- nominal family;
- family-qualified shape;
- ordered public column identities and names;
- semantic role of every public column;
- the coordinate fields that define one row;
- row identity, cardinality, and ordering promises;
- semantic type constraints needed for later operator admission.

A physical dtype may remain deferred only when the semantic contract narrows it
to an admitted type class and no construction-time decision depends on the
exact backend representation. For example, a governed numeric Metric value may
be logically numeric before the backend chooses `DECIMAL(38, 6)` or `FLOAT64`.

Execution must validate the realized physical schema against this prior
contract. It may refine a deferred physical dtype; it may not add, remove,
rename, reorder, or change the semantic role of public columns.

If an operator cannot determine its public columns, row identity, or required
semantic type class before execution, it cannot return a Dataset and is not
admitted to the public lazy algebra.

### Expose state through paired types and discriminated descriptors

Every Dataset exposes one state-matched descriptor:

```python
LogicalDataset.state: LogicalDatasetState
MaterializedDataset.state: MaterializedDatasetState
```

Only `MaterializedDatasetState` contains an `artifact_ref`. The Dataset itself
does not expose `ref: ArtifactRef | None`, `row_count: int | None`, or a flat
collection of fields whose validity depends on state.

This makes state-dependent authority explicit without exposing unusable
methods:

```python
logical.execute()
materialized.state.artifact_ref
```

The state objects are immutable factual descriptors. They are not Dataset
families, execution handles, plans, or operator inputs.

### Separate execution from terminal reads

The state-specific signatures are:

```python
class LogicalDataset(Dataset):
    def execute(self) -> MaterializedDataset: ...

class MaterializedDataset(Dataset):
    @property
    def evidence_digest(self) -> ArtifactDigest: ...
    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage: ...
    def finding(self, finding_id: str) -> Finding: ...
    def show(self, *, max_output_bytes: int | None = None) -> None: ...
    def to_pandas(self) -> pandas.DataFrame: ...

class Dataset:
    def contract(self) -> DatasetContract: ...
```

`show()` renders and returns `None`. `to_pandas()` returns an isolated terminal
DataFrame. `execute()` returns the paired Materialized Dataset. `contract()` is
a non-executing audit read available in both states. `evidence_digest`,
`findings(...)`, and `finding(...)` are the only Materialized Dataset Evidence
reads. They validate the exact committed Artifact/Evidence authority owned by
the runtime-read contract without executing analysis or copying Findings into
Dataset state.

No action returns a public task, future, plan, receipt, or preview Dataset.

### Keep executable Datasets outside the terminal `AgentResult` protocol

The lazy cutover makes one deliberate amendment to the existing
[`AgentResult` contract](../../specs/agent-friendly-public-surface.md#the-three-method-floor-and-the-bounded-card):

- Logical and Materialized Datasets are composable analysis values, not terminal
  `AgentResult`;
- only Materialized Datasets expose `show()`, and Dataset deliberately has no
  `render()` method that could be confused with a row-bearing read;
- `DatasetContract` is a non-executing terminal audit value and therefore does
  implement the full `AgentResult` floor: bounded `repr`, `render() -> str`, and
  `show() -> None`, with `show()` printing exactly `render()` plus a newline;
- `DatasetShapeId`, `DatasetRowContract`, `DatasetRowSetContract`,
  `DatasetSchema`, Dataset state values, field descriptors, and selector values
  are inspectable descriptors with bounded `repr`, not terminal result cards.

The Public Cutover Plan must update the shared `AgentResult` documentation and
contract tests atomically with the Dataset replacement. Until that cutover, the
current eager Frame/Result protocol remains live; this proposal does not claim
the lazy exception is already implemented.

## Public Type Model

### Closed nominal family registry

Every Dataset family is registered once with an internal family registry. One
registration binds:

- a stable family id and closed shape ids with semantic versions;
- one public logical class and one paired public materialized class;
- the closed set of family-qualified shapes;
- its row-contract and row-set-contract validators;
- its common operators and state-specific action/read surfaces;
- its exact immutable logical-node payload variants and pure consumer-admission
  and bounded contract-fact callbacks, when the family needs them;
- its bounded representation renderer;
- its materialized-state decoder;
- the module that owns its family-specific row semantics.

The registry is implementation-owned. It is not a public plugin API, and users
cannot add a Dataset family by subclassing `Dataset`.

The first-cutover family pairs are:

```text
LogicalDataset                     MaterializedDataset
|- LogicalPopulationDataset        |- MaterializedPopulationDataset
|- LogicalMetricDataset            |- MaterializedMetricDataset
|- LogicalDeltaDataset             |- MaterializedDeltaDataset
|- LogicalAttributionDataset       |- MaterializedAttributionDataset
|- LogicalAssociationDataset       |- MaterializedAssociationDataset
|- LogicalForecastDataset          |- MaterializedForecastDataset
|- LogicalCandidateDataset         |- MaterializedCandidateDataset
|- LogicalEventDataset             |- MaterializedEventDataset
`- LogicalLifecycleDataset         `- MaterializedLifecycleDataset
```

The tree shows common Dataset inheritance only. Source-input admission follows
closed family/shape registrations plus exact row and row-set proofs, never
accidental Python substitutability. Module 2 owns the sole Population family;
Module 6's subject-selection operators produce that family after proving their
source-owned selection and completeness requirements.

All eighteen concrete class names in the tree are exact public exports. Nominal
phrases such as `PopulationDataset`, `MetricDataset`, `DeltaDataset`,
`AttributionDataset`, `AssociationDataset`, `ForecastDataset`,
`CandidateDataset`, `EventDataset`, and `LifecycleDataset` are family shorthand
only; they are not Python symbols, aliases, exports, or Help leaves. Public
signatures spell admitted `Logical*` and `Materialized*` classes directly.
The old eager `SubjectSet` class has no paired successor family or compatibility
alias; its domain selection producers return Population Datasets.

Names ending in `Frame`, `Result`, `Artifact`, or `Set` do not define alternate
value categories.

### Common Dataset surface

Every Dataset exposes this common read-only surface:

| Surface | Meaning | Executes |
| --- | --- | --- |
| `kind` | stable nominal family id | no |
| `row_contract` | exact logical meaning of one row | no |
| `row_set_contract` | cardinality and deterministic logical ordering promises | no |
| `schema` | direct projection of `row_contract.schema` | no |
| `fields` | selector-only access to exact current row-contract fields | no |
| `state` | state-matched backing facts | no |
| `definition_fingerprint` | deterministic analytical definition identity | no |
| `contract()` | bounded legal-continuation and authority read | no |

Logical Datasets additionally expose:

| Surface | Meaning | Executes |
| --- | --- | --- |
| `execute()` | produce or recover the Session-bound Materialized Dataset | on binding miss |

Materialized Datasets additionally expose:

| Surface | Meaning | Executes datasource SQL |
| --- | --- | --- |
| `show()` | bounded deterministic projection from the Artifact | no |
| `to_pandas()` | guarded complete transfer from the Artifact | no |

Family-specific properties and operators extend this surface. They may not
weaken its immutability, collection bounds, ownership, or authority rules.
`dataset.kind` is the direct projection of
`dataset.row_contract.shape_id.family_id`, and `dataset.schema` is the direct
projection of `dataset.row_contract.schema`; neither is independently authored
or persisted as a second authority.

Dataset does not imitate a pandas DataFrame. The following current Frame
conveniences are removed from the common public contract:

- `__getitem__` column extraction;
- `__iter__` over column names;
- `__len__` as an implicit row-count action;
- `.shape` as realized `(rows, columns)`;
- arithmetic operators;
- mutation methods;
- implicit conversion through `__array__` or dataframe interchange protocols.

Their behavior would either execute invisibly, confuse analytical shape with
physical dimensions, or produce values outside the typed Dataset algebra.

Callers use Dataset operators while inside Marivo and cross the terminal
boundary explicitly with `to_pandas()`.

### Selector-only field references

Dataset operators such as filtering need to name exact fields without turning
the Dataset into a DataFrame or exposing a column expression API.

Every Dataset therefore exposes one bounded, non-executing selector resolver:

```python
dataset.fields: DatasetFields
```

`DatasetFields` resolves only bindings already present in the Dataset's current
row-contract schema:

```python
metric_field = features.fields.metric(cpu_seconds)
dimension_field = features.fields.dimension(region)
item_field = candidates.fields.get("item_id")
recovered_metric_field = recovered.fields.get(retained_field_id)
```

The focused methods are:

```python
class DatasetFields:
    def metric(
        self,
        metric: Ref[MetricKind] | MetricEntry | RuntimeMetricExpr,
    ) -> DatasetFieldRef: ...

    def dimension(
        self,
        dimension: (
            Ref[DimensionKind]
            | DimensionEntry
            | Ref[TimeDimensionKind]
            | TimeDimensionEntry
        ),
    ) -> DatasetFieldRef: ...

    def get(self, key: DatasetFieldId | str) -> DatasetFieldRef: ...
```

`DatasetFields.get(...)` follows the scoped collection lookup style established
by `CatalogCollection.get(...)`: it resolves one member already visible in the
owning object and returns that owner's bound result. It accepts either an exact
`DatasetFieldId` or one exact public field name visible in the current
row-contract schema. The string form does not parse semantic paths or displayed
typed keys;
callers starting from semantic identity use the focused `metric(...)` or
`dimension(...)` method. `DatasetFields` does not reuse `catalog.require(ref)`:
that verb remains the strict, cross-kind global semantic-membership operation
and stays ref-only.

`DatasetFields` is not a second browseable field catalog. It does not expose
`items`, `refs`, `render()`, `show()`, length, or iteration.
`dataset.row_contract.schema.columns` is the one public field-inventory
authority; `dataset.schema` is a direct projection of that same immutable value.

These are the concrete public semantic input types, not an analysis-only alias.
`Ref`, its closed kind markers, `MetricEntry`, `DimensionEntry`,
`TimeDimensionEntry`, and `RuntimeMetricExpr` remain owned by the semantic
surface and must be present in its canonical export and focused Help contracts
at the cutover before these Dataset methods are published. An exact ref resolves
against the retained row-contract identity and does not require current catalog
membership. A catalog entry must still belong to the current Dataset-owning
Session catalog before its ref is extracted. A runtime Metric expression can
resolve only while its exact in-process identity remains available; recovered
fields use their retained ref or stable `get(...)` path.

`metric(...)` and `dimension(...)` resolve exact retained semantic identity,
not display name. `get(...)` resolves any exact field id or exact current public
field name, including family-owned generated fields such as Candidate `item_id`,
score, reason code, Event step, or statistical output fields that have no
catalog ref. The id form also reacquires a recovered runtime-Metric binding whose
original `RuntimeMetricExpr` object no longer exists. Field ids and public names
come from `dataset.row_contract.schema.columns`; an id is stable
across family-preserving transforms only while that exact binding is retained.
Neither form searches semantic paths, lineage, hidden components, source
columns, or a materialized Artifact's original physical schema. Missing or
ambiguous keys fail with bounded present-field repairs.

`DatasetFieldRef` is an immutable selector value containing:

```text
owning_session_id
field_id: DatasetFieldId
field_binding_fingerprint: str
```

It identifies a current row-contract binding in one exact handle execution Session,
which may differ from a materialized Artifact's original producing Session.
`owning_session_id` is copied from the Dataset that produced the selector; it is
not inferred from a later consumer and cannot be rewritten. The selector does
not contain rows, a Series, an Ibis expression, SQL, a callable, or a private
root handle. Reading or constructing one performs no datasource work and does
not create a Run.

`field_binding_fingerprint` is the canonical digest of the one `DatasetField`
binding resolved from the producing Dataset's row-contract schema. It is a
comparison guard, not a copied field inventory or an independent source of
name, role, identity, type, derivation, or nullability facts.

An operator accepts a `DatasetFieldRef` only when the corresponding input Dataset
has the selector's exact execution Session, resolves `field_id` in its current row-contract schema,
and obtains the same canonical binding fingerprint. The fingerprint comparison
covers field id, role, field identity, public name, derivation identity, logical
type, physical type-state constraint, and nullability. Session ownership is
checked before predicate or policy admission and before any datasource work or
Run creation. A selector may therefore survive a family-preserving transform in
the same Session that retains the exact binding, but cannot be applied across
Sessions or when only a display name or deterministic semantic identity matches.

`owning_session_id` is an admission guard, not a second semantic field identity.
After the ownership check succeeds, predicate and policy normalization bind the
matching field identity from the consuming Dataset. The Session id is not copied
into the analytical predicate payload or used as an independent cache key; the
downstream Dataset's normal input authority token continues to own execution
scope.

`DatasetFieldRef` deliberately does not implement comparison, arithmetic,
boolean, iteration, collection, or conversion protocols. Callers pass it to a
closed typed policy or predicate constructor owned by the consuming module.
Expressions such as these are invalid:

```python
dataset.fields.get("value") > 100
dataset.fields.get("value") == other_field
list(dataset.fields.get("value"))
```

This seam is narrower than DataFrame-like column access: it selects an existing
contract field for another Marivo operator but cannot read or calculate a
value. `__getitem__`, attribute-per-column access, and arbitrary expression
composition remain absent.

## Row And Row-Set Contracts

### Purpose

`DatasetRowContract` is the public, immutable description of what one Dataset
row means. `DatasetRowSetContract` separately describes promises about the
collection of those rows. Both are available before execution and are identical
across materialization.

It is not inferred from pandas columns and not reconstructed from generated
SQL. The source or operator that creates the Dataset constructs both contracts
at the same time.

Conceptually it contains:

```text
DatasetRowContract
  schema_version
  shape_id
  schema
  coordinate_field_ids[]
  key_field_ids[]
  family_semantics

DatasetRowSetContract
  schema_version
  cardinality
  ordering
```

`family_semantics` is one closed `DatasetFamilyRowSemantics` variant registered
by the specific family module. Dataset Core validates that the variant's
registration admits `shape_id`, but does not interpret Population, Metric, Event,
Lifecycle, or statistical semantics.

The payload contains only family-specific facts required to interpret a row or
admit a row-consuming operator. It references public fields only by
`DatasetFieldId`. It does not repeat names, roles, identities, logical or
physical types, nullability, coordinates, row keys, cardinality, or ordering.
Normalized predicates, sampling requests, source/input lineage, definition
fingerprints, and lineage remain owned by the Dataset definition and are not row
semantics.

`complete_from_schema` is admitted only when the owning family registration
proves that `shape_id`, the canonical field bindings, coordinates, and row key
fully describe one-row meaning. It carries no family or shape field of its own.

### Closed public descriptor vocabulary

The common descriptor graph uses the following public, immutable,
helper-produced values. Each is a sealed kind-dispatched value with no public
constructor and no nullable field whose meaning changes by kind.

```text
DatasetShapeId
  family_id: str
  local_shape_id: str
  semantic_version: int

DatasetFieldId
  value: str

DatasetFieldIdentity
  catalog_ref(identity_id)
  entity_identity(entity_ref, identity_signature)
  runtime_metric(expression_fingerprint)
  generated(producer_field_id: DatasetFieldId)

DatasetPhysicalTypeState
  resolved(physical_type_id)
  deferred(admitted_type_class_id)

DatasetField
  field_id: DatasetFieldId
  name: str
  role_id: str
  identity: DatasetFieldIdentity
  derivation_identity: str
  logical_type_id: str
  physical_type_state: DatasetPhysicalTypeState
  nullable: bool

DatasetRowBound
  unknown
  static(max_rows: int)
  runtime_policy(policy_id: str)

DatasetCardinality
  singleton
  keyed(row_bound: DatasetRowBound)

DatasetOrderTerm
  field_id: DatasetFieldId
  direction: ascending | descending
  nulls: first | last
  value_order_contract_id: str

DatasetOrdering
  unordered
  ordered(terms: tuple[DatasetOrderTerm, ...])

DatasetFamilyRowSemantics
  complete_from_schema
  sealed family-registered variants

DatasetByteCount
  exact(byte_count: int)
  unavailable(reason_id: str)
```

The accepted Slice 2a amendment adds `entity_identity` to the closed field
identity vocabulary. It carries an exact Entity ref and a non-empty ordered
`identity_signature: tuple[tuple[str, str], ...]` of primary-key field names and
registered component logical-type ids. Names are unique; every component is
non-null. A one-column key still denotes a one-element tuple. Version fields
are not included unless they actually belong to the authored stable identity.
The signature participates in field binding and row-contract fingerprints and
is readable from the canonical schema without consulting a catalog or graph.
The variant introduces no additional top-level export. Raw identity values are
never descriptor facts.

`DatasetShapeId` is the one shape discriminator. Its validated fields replace
independent family, shape, and semantic-version strings that could disagree.
`DatasetFieldId` validates one bounded canonical field-id value and prevents
display names or numeric positions from entering exact-id APIs. `family_id`,
`local_shape_id`, `identity_id`, `producer_field_id`, `role_id`, `logical_type_id`,
`physical_type_id`, `admitted_type_class_id`, `policy_id`,
`value_order_contract_id`, and `reason_id` are validated stable registry ids,
not display labels or caller-authored policy strings. The discriminant and
payload shown for each variant are its complete public fields; implementations
must not flatten the variants into optional-field records.

`DatasetRowContract` has the exact public field graph:

```text
schema_version: int
shape_id: DatasetShapeId
schema: DatasetSchema
coordinate_field_ids: tuple[DatasetFieldId, ...]
key_field_ids: tuple[DatasetFieldId, ...]
family_semantics: DatasetFamilyRowSemantics

DatasetRowSetContract
  schema_version: int
  cardinality: DatasetCardinality
  ordering: DatasetOrdering
```

`DatasetSchema.columns` is the one complete, ordered tuple of logical
`DatasetField` bindings. `dataset.schema` returns this exact immutable value; it
is not separately constructed. A materialized state's realized schema uses the
same type with every physical type state resolved; it does not replace or mutate
the Dataset's logical schema.

Field ids are unique within one row-contract schema. Every coordinate id, key
id, and order term must resolve exactly one current field id. Coordinate and key
ids are duplicate-free, and key ids are an ordered subset of coordinate ids.
Duplicated, missing, or stale ids are construction errors.

The row-set contract is validated against that exact row contract in the same
construction step. `singleton` requires empty coordinate and key tuples;
`keyed(...)` requires a non-empty key tuple; every ordering term resolves
through the canonical schema; and an ordered variant proves a total order using
the key or another family-registered unique tie-breaker. Neither contract may be
paired later with a different counterpart.

The canonical `row_contract_fingerprint` binds exactly `schema_version`,
`shape_id`, the ordered schema bindings, coordinate and key field-id tuples, and
the complete family-semantics variant. The canonical
`row_set_contract_fingerprint` separately binds the row-set `schema_version`,
cardinality variant, and ordering terms. Neither fingerprint includes Dataset
definition, source/input lineage, predicates, sampling, realized rows, or
storage facts.

The descriptor values are public reads, not authoring inputs. Operators accept
only the specific typed semantic inputs,
`DatasetFieldRef` selectors, policies, and literals declared by their owning
contracts.

### Family-qualified shape

`DatasetShapeId` always binds a family-local shape to one semantic version. Bare
strings such as `"entity"`, `"scalar"`, or `"history"` do not form a globally
shared enum. Its canonical rendering is, for example:

```text
metric/entity@v1
metric/dimension-time@v1
event/journey@v1
lifecycle/history@v1
candidate/entity-outlier@v1
```

This prevents unrelated families from acquiring accidental compatibility merely
because they use the same short word. Help may render a short label, while
admission compares the exact `DatasetShapeId`. `schema_version` independently
versions the common `DatasetRowContract` serialization graph; it is not a family
or shape semantic version.

### Coordinates and values

Coordinates identify the analytical position of a row. Values are facts
measured, calculated, scored, or classified at that position.

Entity coordinates bind the Semantic Object Model's identity primary key `K`.
Version coordinates identify historical representations and never silently join
that identity signature. An analytical row may be keyed by `(K, time)` while a
Population row is keyed by `K` alone; each family's owner supplies its exact
temporal resolution and uniqueness checks. The compiler cannot manufacture a
second Entity identity by subtracting version fields from an authored key.

Every coordinate needed to distinguish published rows must remain in the public
row contract. For example, Attribution retains its comparison scope alongside
decomposition axes; an unexposed private scope cannot repair a duplicate public
row key. Identity-bearing scope coordinates follow the same row-only privacy
boundary as other Entity coordinates and do not enter Evidence subjects.

Every public binding appears exactly once in `DatasetSchema.columns`. Coordinate
membership is the ordered `coordinate_field_ids` projection. Value bindings are
derived deterministically as the schema columns not named by that projection;
they are not stored as a second field inventory. Every field's identity variant
is selected exactly once:

- `catalog_ref` for an exact governed catalog identity;
- `runtime_metric` for a governed runtime Metric expression fingerprint;
- `generated` for a family/operator-owned field id.

The row key is an ordered non-empty subset of coordinate field ids for every
non-singleton shape. An exact singleton instead has
`coordinate_field_ids=()`, `key_field_ids=()`, and
`DatasetCardinality.singleton`. A scalar Metric Dataset therefore adds no
synthetic public coordinate column while its row identity and cardinality remain
exact.

### Ordered public schema

`DatasetSchema.columns` is the canonical field inventory and contains one
`DatasetField` per public column in exact output order.
Logical Dataset schemas may contain either physical type-state variant:

```text
resolved(physical_type_id)
deferred(admitted_type_class_id)
```

The schema must never guess roles from column names. A column named `value`,
`time`, or `status` has no authority without its typed binding.

Column names are unique after public export normalization. Duplicate display
names are resolved deterministically during Dataset construction or cause a
typed construction error; they are not repaired after execution.

### Row-set cardinality and ordering

Cardinality before execution is a row-set promise, not a realized count. Its
closed variants make invalid presence/uniqueness products unrepresentable:

- `singleton` means exactly one row, an empty row key, and an implicit static
  bound of one;
- `keyed(row_bound)` means zero or more rows unique by the non-empty
  `key_field_ids`; its bound is unknown, a positive static maximum, or one exact
  registered runtime-policy id.

For example, `limit(100)` preserves the row contract and replaces only the
row-set contract's keyed bound with `static(100)`. Cardinality informs action
admission but does not claim an exact realized row count except for `singleton`.

Ordering is either `unordered` or one non-empty ordered tuple of
`DatasetOrderTerm` values. An order term may name any exact current public field
-- coordinate, value, or generated -- because accepted orders include generated
`rank`, Candidate `score`, and Candidate `item_id`. Every term fixes direction,
null placement, and a registered value-order contract covering applicable NaN,
collation, timestamp, binary, and composite-value rules.

A declared ordered Dataset must supply a total deterministic order. Its terms
therefore include the row key or another owner-proven unique tie-breaker after
the business ordering terms. Partial orders such as score alone are invalid
row-set contracts rather than datasource-natural tie behavior.

Datasource-natural ordering is never a Dataset promise. `show()` and
`to_pandas()` must add a deterministic presentation order where required or
reject a request whose contract cannot be rendered deterministically.

## Immutable Dataset Value

### Construction

Each source or operator constructs a Dataset from:

```text
execution Session identity
+ nominal family registration
+ exact row contract
+ exact row-set contract
+ definition fingerprint
+ deterministic bounded lineage summary
+ state-matched private root handle
+ logical or materialized state
```

The root/state pairing is a closed internal invariant:

```text
LogicalRootHandle          + LogicalDatasetState
MaterializedScanLeafHandle + MaterializedDatasetState
```

No other pairing can be constructed or decoded. Materialization replaces the
logical root with the scan leaf in the returned Dataset; it does not retain the
logical root as a second executable path. Cold recovery constructs only the
materialized pairing and cannot reconstruct or attach the original logical
root.

The private root handle is opaque. It cannot be serialized, inspected, or
passed as a public API argument. A user cannot extract Ibis, SQL, a planner
node, or a backend relation from a Dataset.

### Immutability

Every operator returns a new Dataset value. It never mutates:

- the input row contract;
- the input row-set contract;
- logical state;
- materialized backing;
- Session ownership;
- lineage;
- cached previews;
- Evidence or quality state.

`show()` and `to_pandas()` create no persisted Runtime record or analysis Run
and do not attach preview rows or a pandas object to the Dataset as new
analytical authority.

`execute()` returns a new paired Materialized Dataset. The logical input remains
logical and can still be composed. Reconstructing that same exact logical
definition in the same Session resolves the persisted execution binding and
returns the same Artifact-backed result without datasource execution.

### Equality and hashing

Dataset values do not overload `==` to mean equal rows, equal plans, equal
logical definitions, or equal Artifacts. Equality retains Python object
identity, and Dataset values are intentionally unhashable rather than public
mapping keys.

Callers that need deterministic definition identity read
`definition_fingerprint`. Callers that need exact committed identity inspect a
`MaterializedDatasetState.artifact_ref`.

This avoids collapsing three different questions:

1. were these values constructed from the same normalized definition;
2. do they refer to the same committed Artifact;
3. do their realized rows happen to be equal.

## Session Ownership

A Dataset handle has one execution Session, fixed at construction. That Session
supplies planning services, current semantic definitions when required, execution
bounds, policy, and ownership of any new Run/output. An Artifact separately keeps
its immutable original producing Session and Run. Reading it in a different
Session does not transfer that ownership.

### Explicit Materialized inputs

Within the same project Store, `target_session.artifact(ref)` reads an exact
committed Artifact from any Session. It constructs a new Materialized Dataset
handle in the target execution context with unchanged Artifact ref, descriptor,
producer, storage, and Findings. `state.artifact_session_ref` identifies the
original owning Session. The read writes no state, creates no local alias or
execution binding, and never asks whether the result is fresh or reusable.

A receiver-style operator produces its new Logical Dataset in the receiver's
execution Session. A Session source constructor uses its explicit Session.
Other Materialized operands from the same Store may be supplied directly, even
if their handles were read through another Session. Input validation uses exact
Artifact identity and the operator's structural contracts; it does not use
matching names, result age, or source-state equivalence to approve reuse.

Only the consuming Session admits/locks its Run and owns its new Artifact.
Materialized input tokens and persisted input edges keep original refs; their
owning Sessions remain factual provenance. No originating Session lock,
activation, recovery, or new registration is needed to read an immutable input.
The shared current pointer never selects the execution Session of a handle.

### Logical and selector boundaries

Foreign Logical inputs remain invalid: they carry another Session's live graph
and execution context. Materialize in the originating Session first, then choose
the exact Artifact. Cross-project/Store inputs require a separate import contract
and remain unsupported; project paths, names, or equal rows cannot substitute
for a resolved identity in the same Store.

Field selectors retain their input execution Session and exact field binding;
validation uses the corresponding operand, not the output's execution Session.
Reacquiring an
Artifact through the target Session also allows reacquiring its selectors there;
old selectors are never silently rewritten or accepted because names match.
Policy objects and catalog refs retain their existing Session-neutral rules.
Every requested operator still enforces types, identities, units, row contracts,
and alignment; none emits a general suitability or freshness verdict.

```python
prior = session_a.observe(metrics=[revenue]).execute()
selected = session_b.artifact(prior.state.artifact_ref)
next_result = selected.limit(100).execute()
```

`selected` reads A's original Artifact; only `next_result` is produced in B.
The Agent explicitly chose that result and remains responsible for whether it
fits B's question. No data copy or reuse certification occurs.

### Logical lifetime and recovery

A logical Dataset is an in-process value owned by its live Session. It may be
passed between functions and composed for as long as that Session remains live,
but it has no durable public locator.

Logical Dataset construction does not:

- register an Artifact;
- persist a recoverable Dataset record;
- allocate a user-addressable ref;
- publish or mutate an execution binding;
- make the Dataset pickleable or generally serializable;
- add a recoverable Dataset node to the Session graph.

Action records may retain a bounded safe projection of a logical definition for
audit. That projection cannot reconstruct the Python Dataset or become an
operator input.

After process loss, a caller may reconstruct the exact logical Dataset from its
authoring code and call `execute()`. The runtime derives the same
`DatasetExecutionKeyV1`, resolves its write-once Session binding, and recovers
the exact Artifact. A caller that already has the opaque ref may instead recover
it directly through `session.artifact(ref)`.

There is no pickle handoff, mutable logical cache, call-site identity, script
registry, or inference from a Python variable name. The script reconstructs
the definition; the Session Store owns the durable binding.

Only `execute()` can publish new durable rows. Same-Session execution-key
lookup and explicit `session.artifact(ref)` reads reuse existing rows without
new publication or a suitability guarantee.

## Logical Definition Identity and Lineage

### Private owner payload boundary

Core admits only exact family-registered, frozen, slotted logical-node payload
variants. The family owns and validates the nested semantic arguments and any
private captures. Core consumes one safe canonical identity projection from the
payload to compute the sole definition fingerprint; a node cannot also supply
a competing parameter projection. Payloads are process-local, redacted in
representations, and not serializable. Raw captured values never enter the
projection or lineage.

The payload supplements the one Dataset input graph. A Materialized input still
terminates at its exact Artifact scan leaf, which carries no replayable source
payload. Family admission callbacks are pure and are shared by construction and
contract disclosure, so a rejected state-specific continuation is not advertised.

### Definition fingerprint

`definition_fingerprint` is a deterministic digest of the normalized analytical
definition. It binds:

- exact row-contract and row-set-contract fingerprints; the row-contract
  fingerprint already binds the typed shape id;
- normalized source and operator identities;
- exact bound semantic dependency digests, refs, and governed policy values;
- normalized literal parameters and captured source-parameter value digests;
- ordered private input authority tokens;
- the canonical sharing relation of semantically significant realization
  occurrences in the complete logical definition, as specified below;
- producer implementation, quality, Evidence, Finding, and retained-state
  contract versions that affect the committed result contract.

It excludes:

- Python object addresses and raw graph-local node or realization handles;
- generated SQL text;
- secrets and credentials;
- executor timing and resource usage;
- preview rows;
- mutable display labels;
- non-semantic Help text.

For a logical Dataset, the fingerprint identifies a definition, not realized
membership, rows, or datasource snapshot. It is not an Artifact ref. The
runtime combines only this canonical fingerprint with the common materialization
protocol version to derive the execution-key digest; the Store scopes lookup by
Session. It does not normalize those dependencies again in a parallel key schema.

Executing a Dataset preserves the fingerprint recorded as the Dataset's
origin definition. The definition fingerprint therefore answers what was
defined, not which backing a later operator must consume.

### Sharing is part of the analytical definition

An owning semantic contract identifies calculations whose within-action
realization affects meaning, including Population sampling and selected
contributions that require single evaluation. It creates and propagates opaque
graph-local realization handles under that contract's sharing rule. Reusing an
upstream definition preserves those handles through filters, projections and
other dependent branches. A separately authored sampling call creates a distinct
realization handle even when its parameters and standalone fingerprint are
equal. An owner-required shared dependency, such as repeated uses of one Event
occurrence stream, retains one realization handle under its own contract.

Dataset Core incorporates this sharing relation into the sole definition
fingerprint during construction:

1. Traverse the normalized logical root in deterministic depth-first order,
   following each owner's ordered input roles and semantic dependency roles.
   Visit every use occurrence, including repeated references to a shared node;
   any requirements on one node have an owner-defined stable role order.
2. For each semantically significant realization handle, assign the next
   ordinal on its first encounter and reuse that ordinal at every subsequent
   occurrence of that handle. Bind the ordered semantic occurrence positions
   and their ordinals into the normalized definition.
3. Stop at each Materialized input. Its exact Artifact token supplies immutable
   authority; producer graph structure and realized sampling rows do not enter
   this traversal.

For two otherwise equal operands, the sharing labels are therefore `[0, 0]`
for one shared realization and `[0, 1]` for two separately authored realizations.
The raw handles, allocation order, Python addresses, variable names and source
locations are excluded. Reconstructing the same normalized graph and sharing
relation in another process produces the same fingerprint. Sharing a pure
subgraph without a significant realization does not itself change identity;
this rule does not add general common-subexpression elimination or make SQL
object reuse proof of single evaluation.

For example, let `d` be a scalar Metric observation over an unseeded sampled
Population, and let `make_d()` construct that same definition with a new sampling
call. On a fixed source, `d.compare(d)` consumes one sample and has zero delta
for a finite non-null Metric. `make_d().compare(make_d())` consumes two separate
samples and may have a nonzero delta. The comparisons have different definition
fingerprints even though their ordered upstream fingerprint lists are equal.
Separate samples need not produce different rows; their sharing contracts,
rather than the observed outcome, determine definition identity.

This normalization is part of Dataset Core's existing definition authority.
There is no second graph fingerprint, persisted occurrence table, public
realization id or recoverable logical graph. The compiler consumes the bound
sharing requirements and preserves their realization partition; it cannot merge
distinct sample realizations or split a required shared realization. Runtime
uses only the resulting Core fingerprint for execution-key construction.

### Input authority token

Every Dataset input contributes one private, closed authority token to a
downstream operator definition:

```text
LogicalInputToken
  definition_fingerprint

MaterializedInputToken
  artifact_ref
```

A logical input token requires the later action to evaluate the definition
using the consuming Session's admitted semantic/execution contracts. A materialized input token fixes the
immutable committed rows and forbids transparent reach-through to the original
semantic source graph.

Tokens select input authority; their ordered list alone is not a complete
downstream definition. Core also consumes the live logical roots to normalize
the sharing relation across and within operands. Equal `LogicalInputToken`
values do not merge their roots or prove equal realizations. No realization
handle is added to the token or persisted as a second reuse key.

The downstream Dataset's `definition_fingerprint` binds the ordered input
authority tokens, not only each input's public definition fingerprint. As a
result, these two definitions intentionally have different fingerprints:

```python
logical_association = features.correlate(method="spearman")
checkpoint_based_association = features.execute().correlate(method="spearman")
```

They describe the same analytical operator and may eventually contain equal
rows, but the first consumes current semantic execution while the second
consumes one exact Artifact snapshot. They are not interchangeable cache keys.

The token is private planner input, not a public plan or state variant. Public
`contract()` renders only a bounded authority summary.

### Public lineage boundary

Before materialization, lineage is a deterministic, bounded projection of:

- source Dataset definition fingerprints;
- semantic dependency identities;
- public operator ids;
- materialized input Artifact refs;
- row-contract and row-set-contract transitions.

It does not expose private nodes, Ibis expressions, SQL, physical stages,
secrets, or unbounded parameter payloads.

The Dataset card and `contract()` may summarize this lineage. Full persisted
Run and Artifact dependency reads remain Session audit surfaces owned by the
runtime module.

## Dataset State

### Logical state

`LogicalDatasetState` proves only facts available without datasource work:

```text
kind: Literal["logical"]
```

The logical state is only a discriminator. Input requirements belong to the
operator contract and bound graph; the state carries no duplicated requirement ids.

The common Dataset, row contract, and row-set contract already carry the
definition fingerprint, shape, schema, coordinates, cardinality, ordering,
semantic dependencies, and lineage; state does not duplicate them.

Logical state must not contain:

- Artifact ref or content hash;
- realized row or byte count;
- realized Population membership;
- datasource snapshot identity;
- execution timings, scanned bytes, or query ids;
- realized sampling receipt;
- row-dependent quality conclusions;
- Evidence, Findings, or Evidence digest;
- preview data.

### Materialized state

`MaterializedDatasetState` proves that one durable Dataset Artifact was
committed and can be used as the current immutable backing:

```text
kind: Literal["materialized"]
artifact_ref: ArtifactRef
artifact_session_ref: str
content_authority_digest: str
storage_kind_id: str
realized_schema: DatasetSchema
realized_row_count: int
realized_byte_count: DatasetByteCount
producing_run_ref: str
quality_authority_digest: str
evidence_authority_digest: str
```

`artifact_session_ref` is the original owner read from the Artifact row and
never changes with execution context. The stable ids and digests are bounded
committed projections. Storage receipts,
Evidence payloads, Findings, and Run records remain on their runtime-owned audit
surfaces rather than becoming nested public state descriptors.

No field in this state is optional. The only first-cutover unavailable fact is
realized byte count, represented by the exact
`DatasetByteCount.unavailable(reason_id)` variant. Adding another unavailable
fact requires its own typed variant; a bare `None` must not ambiguously mean not
computed, unavailable, inapplicable, or corrupted.

`realized_row_count` is not optional. It is an exact non-negative integer for
every materialized Dataset, regardless of storage kind. The materialization
runtime must obtain it from the producing execution, committed manifest, or an
exact count against the immutable backing before publication succeeds. A
backend's inconvenient or expensive count operation does not weaken this
contract.

Exact row count is required for:

- Artifact integrity and recovery validation;
- materialized `repr` and bounded reads;
- terminal collection preflight;
- Evidence and quality denominators;
- detecting incomplete or mismatched storage publication.

Realized byte count may use a typed unavailable state when the immutable
storage contract cannot define it exactly. Row count may not.

This document does not define storage receipts or publication ordering. The
materialization runtime must construct this state only after its commit contract
has succeeded.

### No hidden third state

A public Dataset is never `executing`, `failed`, `partially materialized`, or
`stale`.

- execution belongs to a Run, not to the immutable input Dataset;
- an action failure returns a typed error and leaves the Dataset unchanged;
- incomplete publication is recoverable runtime state, not a public Dataset;
- a concrete input or integrity failure is reported by the operation that
  observes it; no current-catalog drift or freshness state is introduced.

### Materialized read authority

A Materialized handle refers to one committed Artifact. Recovery and Logical
`execute()` binding hits validate only selected metadata, original ownership,
execution context, and supported contract/receipt structure. They do not open
backing, scan Findings, or verify unused private parts.

`show()` checks accessed primary storage identity/access and schema under its
preview bound. `to_pandas()` checks and collects complete primary rows under its
memory guard. Operators additionally read only the retained roles they require.
Complete content/count/Finding-set checks belong to explicit full inspection;
partial reads never claim that full integrity has been checked.

`contract()` remains non-executing. It projects the committed integrity,
storage, schema, quality, and Evidence facts already attached to the Dataset; it
does not probe current storage readability or authorization.

They do not require the current semantic catalog still to contain or approve
the original Metrics, Dimensions, Entities, Relationships, or source bindings.
Catalog drift or removal cannot make committed snapshot rows unreadable.

Each operator owns its concrete semantic dependencies, retained-field checks,
and any explicitly admitted enrichment. Observation Model owns filtering;
Typed Operators and Subject/Event/Lifecycle own their calculations. The compiler
binds source, scan, join, and calculation nodes from those contracts, and Runtime
executes their checks. There is no shared authority-mode vocabulary or separate
requirement record. Ordinary materialized reads need no current-catalog approval.

`session.revalidate(...)` performs explicit full integrity inspection of metadata,
primary data, retained parts, and Findings. It does not compare current semantic
definitions, mutate Dataset state, or run implicitly during ordinary reads.

A Materialized handle is a reference to committed data, not an eagerly verified
copy. Handle recovery validates selected metadata only. Row reads check primary
backing; operators check only the private parts their contracts consume; Finding
reads check their selected records. Full content/Finding scans belong to explicit
integrity inspection. Public row/byte counts and schemas describe primary data;
private parts belong to the same Artifact without entering public columns.

## Generic Operator Protocol

An analysis operator has the public form:

```text
Dataset[A] x Typed Inputs -> Dataset[B]
```

Calling one performs only deterministic local work:

1. validate that Logical inputs belong to the consuming Session, Materialized
   inputs resolve exact committed Artifacts in the same Store, and every
   `DatasetFieldRef` matches its corresponding input's execution Session and binding;
2. normalize public semantic refs, policies, and literals;
3. validate nominal family and exact `DatasetShapeId` admission;
4. derive the complete output row contract and row-set contract; the Dataset's
   logical schema is the row contract's exact schema;
5. derive the deterministic definition fingerprint and bounded lineage;
6. create a new immutable logical Dataset with a private root handle.

It does not:

- open a datasource connection;
- query metadata whose authority was not already admitted at construction;
- estimate or count rows through backend work;
- collect a pandas DataFrame;
- create a Run;
- create or reuse an Artifact;
- publish Evidence or Findings;
- choose an undocumented local fallback.

Construction-time errors state the accepted family, shape, semantic type,
ownership, or parameter contract; the received value; and a mechanically valid
repair.

Row-dependent preconditions that cannot be proven locally are retained as
typed action-time requirements. They are visible in `contract()` and fail
closed during an action. They are never guessed from a preview.

## Reads and Actions

### Synchronous execution and read contract

All first-cutover Dataset execution and reads are synchronous and blocking:

```python
materialized = logical.execute()
materialized.show(...)
frame = materialized.to_pandas()
```

A call returns only after its durable execution, bounded inspection, or
complete collection has succeeded or failed. There is no public async
variant, future, task, polling handle, background Dataset state, or cancel
handle.

Process interruption and backend cancellation are runtime concerns. A
cooperatively handled cancellation records the appropriate failed action; a
process loss may leave an incomplete Run for recovery. In both cases the input
Dataset remains unchanged and no partial action result is public.

Introducing async execution later would require a separate design because it
must not turn tasks or futures into analysis values or create a second
materialization authority.

### `repr(dataset)`

`repr` is pure, bounded, single-line, and never executes.

Logical example:

```text
<LogicalMetricDataset shape=entity fp=ds_7d82c1; use .execute()>
```

Materialized example:

```text
<MaterializedMetricDataset shape=entity ref=art_41fa2c rows=98214; use .show()>
```

The renderer includes only deterministic identity facts that fit the shared
representation budget. It does not render a preview, SQL, a conclusion, or a
ranked next step.

### `contract()`

`dataset.contract()` is a bounded, non-executing read returning a closed
`DatasetContract` value.

For every Dataset it reports:

- nominal family and qualified shape;
- logical row contract, row-set contract, and ordered schema;
- logical or materialized state;
- Session ownership identity;
- bounded semantic dependency and lineage facts;
- mechanically admitted operators and their exact input roles;
- action bounds and row-dependent preconditions;
- terminal boundary facts.

For materialized state it may additionally project committed Artifact, quality,
Evidence, and storage facts supplied by the runtime. It never recomputes them or
consults the current semantic catalog as a substitute for committed authority.

`DatasetContract` is a terminal audit/read value, not a Dataset and not an
operator input. It implements the shared terminal result protocol:

```python
class DatasetContract:
    def render(self, *, max_output_bytes: int | None = 8192) -> str: ...
    def show(self, *, max_output_bytes: int | None = 8192) -> None: ...
```

Its bounded deterministic single-line `repr` points to `.show()`. `render()` is
pure and `show()` prints exactly that rendered value plus a newline; neither
executes or revalidates the Dataset. Passing `None` retains the shared meaning of
disabling only the additional render-byte truncation over the already bounded
contract value.

### `MaterializedDataset.show()`

`materialized.show()` is the bounded inspection action and returns `None`.
Logical Datasets do not expose this method. It reads committed metadata and a
bounded projection of the immutable backing, validates Artifact integrity and
readability, and never runs datasource SQL or the origin semantic definition.

The preview order is exact:

1. if the row-set contract has ordered `DatasetOrdering`, preserve its complete
   ordered terms, including its registered business order and unique tie-breaker;
2. otherwise, for a non-singleton Dataset, order by the declared row-key fields
   in contract order using their registered ascending value-order contracts;
3. for an exact singleton, add no synthetic order term;
4. apply the preview row bound only after this total order is established.

The materialized scan adapter may implement this order through a registered
storage-native equivalent or order-preserving canonical key encoding. A hash or
digest order is not equivalent to the declared value order. If the runtime
cannot establish equivalent ordering within the read bounds, `show()` fails
with a typed repair. It never substitutes storage-natural order, random rows,
or a nondeterministic `LIMIT`.

This rule may make previewing an unordered high-cardinality Artifact expensive,
but it never causes origin recomputation. That read cost is accepted in
exchange for deterministic agent-visible output.

Preview bounds are runtime policy, not user hints. `show()` accepts no row
`limit` that could be confused with changing Dataset semantics. The optional
`max_output_bytes` may tighten only the rendered output budget; it cannot raise
the registered hard maximum or change which Dataset the preview represents. A
caller who needs a different analytical row set constructs it with an explicit
lazy Dataset operator before calling `show()`.

Preview truncation is allowed because `show()` labels the configured preview
bound and uses the Artifact's exact realized row count for omitted-row facts.
Sampling is never implicit: approximation must already be part of the executed
Dataset definition and committed authority.

### `MaterializedDataset.to_pandas()`

`materialized.to_pandas()` is a terminal collection action returning one
isolated `pandas.DataFrame` containing all rows of the committed Dataset.
Logical Datasets do not expose this method.

It:

- validates static row-set cardinality and configured collection limits before work;
- enforces row, byte, and timeout limits during execution;
- applies the same canonical row ordering used by `show()` when the Dataset has
  no declared order;
- preserves the Dataset's public column order and names;
- validates the committed realized schema against the logical row contract;
- returns a defensive value with no live Marivo authority;
- records bounded collection execution through the Session runtime;
- never creates another Artifact or reruns datasource SQL;
- never silently truncates, samples, spills, or changes storage strategy to
  force collection to succeed.

There is no `limit=` parameter on `to_pandas()`. Callers must make any row-limit,
aggregation, Population narrowing, or sampling semantics explicit in the lazy
Dataset expression.

The returned DataFrame cannot be passed back into an analysis operator. Any
mutation affects only that DataFrame.

Exact `realized_row_count` is used for preflight and the action reads only the
committed immutable backing. It does not consult the current semantic catalog.
Exceeding a collection limit fails the read and returns no partial DataFrame;
the existing Materialized Dataset remains valid and reusable.

### `execute()`

`logical.execute()` is the only public logical execution action. It returns the
paired materialized class for the same family, with:

- the same nominal analytical family id;
- the same row contract, row-set contract, and logical public schema;
- the same origin `definition_fingerprint`;
- `MaterializedDatasetState`;
- a private immutable materialized scan-leaf root.

The materialization runtime owns execution, storage selection, publication,
Evidence, exact reuse, and failure recovery. Dataset Core requires the following
observable behavior:

1. the input Dataset is never mutated;
2. success returns only after durable materialized authority is committed;
3. failure returns no partially materialized Dataset;
4. private source stages, Arrow transfers, pandas DataFrames, numerical outputs,
   and Runtime staging never construct a public Dataset or Materialized state;
5. realized root schema must satisfy the pre-execution row contract and its
   realized row count must satisfy the row-set contract;
6. downstream operators read the materialized backing as a leaf;
7. downstream planning cannot transparently reach through the leaf to rewrite
   its original semantic sources;
8. recovery of the Artifact reconstructs the paired Materialized Dataset class
   with the same row contract and row-set contract;
9. a matching Session execution binding resolves before origin-source work or
   Run admission; reading its backing may require storage access/credentials.

The first-cutover signature is deliberately zero-argument:

```python
checkpoint = dataset.execute()
```

It accepts no Artifact name, storage location, engine relation, retention,
format, partitioning, or placement parameter. The owning Session's typed runtime
policy supplies one configured storage target and retention policy. Runtime
validates that target's exact writer and bounds; it does not rank alternative
sinks. An unsupported target fails without moving the calculation to another
engine or making the Dataset action a storage-management API.

The returned `MaterializedDatasetState.artifact_ref` is the recovery identity.
Changing Session policy before a later action does not relocate an already
committed Artifact or make execution-binding recovery a copy operation.

Materialized Datasets do not expose `execute()`. Reconstructing the same exact
Logical Dataset in the same Session and calling `execute()` resolves its
persisted execution binding, validates the exact Artifact, and returns a new
Materialized Dataset object backed by that Artifact. This recovery creates no
new execution Run, receipt, copy, or authority refresh.

## Authority Produced by Each Boundary

| Boundary | Executes | Durable reusable rows | Row-dependent authority | Public return |
| --- | --- | --- | --- | --- |
| operator call | no | no | none | new logical Dataset |
| private physical stage or exchange | possibly | no | action-private only | none |
| `repr` | no | no | none | string |
| `contract()` | no | no | logical or already committed facts | `DatasetContract` |
| `LogicalDataset.execute()` binding miss | yes | yes | committed Artifact authority | paired Materialized Dataset |
| `LogicalDataset.execute()` binding hit | no; exact recovery | existing backing | committed Artifact authority | paired Materialized Dataset |
| `MaterializedDataset.show()` | no datasource execution | existing backing | committed facts plus bounded preview | `None` |
| `MaterializedDataset.to_pandas()` | no datasource execution | existing backing | committed facts plus isolated collection | isolated DataFrame |

All row-bearing reads begin from committed Dataset authority. Preview and
collection projections cannot become new Dataset inputs; the Materialized
Dataset remains the reusable input.

## Materialized Scan-Leaf Seam

The public Dataset does not expose a plan, but the planner needs a closed seam
after materialization.

Dataset Core therefore requires one private root protocol with two variants:

```text
LogicalRootHandle
MaterializedScanLeafHandle
```

This is architectural notation, not a public class contract.

The materialized variant supplies the planner only:

- consuming execution Session and original Artifact owning Session;
- exact same-Store Artifact/content identity;
- exact row-contract and row-set-contract fingerprints;
- immutable storage admission handle;
- realized schema validation facts;
- bounded reader and source-lowering capability facts for its immutable backing.

Semantic acceptance of Logical/Materialized operands does not promise they can
execute together. Eligible same-domain source expressions compose through Ibis;
engine Artifacts contribute immutable scans in their owning source domain.
Local/object Parquet readers use authorized PyArrow reads and begin a bounded
pandas continuation. There is no internal DuckDB executor; DuckDB remains an
ordinary datasource when explicitly declared as one.

Before data work, Module 3 traverses registered method/adapter support to retain
contiguous eligible source work and determine the first local step. Independent
source outputs may feed only explicitly admitted pandas input roles. Once an
input enters local computation, all dependent successors remain local; nothing
uploads it into a source expression. Missing required source support fails when
no exact, identity-safe local method is registered. Source compilation or
execution failure never changes this decision. Reader/binding checks and
complete local input, retained-part, intermediate-memory and deadline guards
remain mandatory. An explicit `.execute()` repair is advertised only when the
configured backing and consuming method can actually satisfy those requirements;
it does not authorize origin replay or make an oversized local input admissible.

It does not supply the original source graph as a rewrite target. Origin lineage
may remain available for audit, but audit lineage is not executable lineage.

The root protocol and Dataset state are decoded as one discriminated pair.
`LogicalRootHandle + MaterializedDatasetState`,
`MaterializedScanLeafHandle + LogicalDatasetState`, and any Dataset carrying
both root variants are integrity errors rather than recoverable or coercible
states.

The planner module defines how the leaf lowers. The materialization runtime
defines how storage authority is decoded. Neither may widen the public Dataset
surface with `plan`, `sql`, `ibis`, `relation`, `future`, or `receipt`
properties.

## Common-Dependency Reuse

Same-Session exact execution-key hits choose the existing committed Artifact.
Across Sessions the Agent explicitly selects an Artifact ref, using producer
admission/finish times, source/input lineage, schema, and quality as context.
Marivo supplies no freshness, source-equivalence, expiry, or reusable verdict.
These facts do not relax the structural input contract of an operator.

Downstream operators are legal on both states and always return a new Logical
Dataset, so a complete lazy DAG may be built before any execution:

```python
features = session.observe(metrics=[scanned_bytes, cpu_seconds])
association = features.correlate(method="spearman")
outliers = features.metric(cpu_seconds).discover.entity_outliers(limit=25)
```

Executing either terminal logical DAG materializes exactly that Dataset. The
planner may share equal nodes inside the action, but separate terminal Dataset
definitions have separate Session execution bindings.

To make a common upstream result the stable input of several later Datasets,
execute the common Dataset first:

```python
features = session.observe(
    metrics=[scanned_bytes, cpu_seconds],
).execute()

association = features.correlate(method="spearman").execute()
outliers = (
    features
    .metric(cpu_seconds)
    .discover.entity_outliers(limit=25)
    .execute()
)

association.show()
outliers.show()
```

The common Dataset now enters both downstream definitions through the same
immutable scan leaf. There is no public multi-sink object, execution DAG,
`persist`, `cache`, or `compute_all` API.

Within one action the private planner may share compatible subexpressions. That
optimization is not durable authority and is not observable as Dataset state.

## Failure Boundaries

Dataset Core defines four failure phases:

| Phase | Examples | Dataset effect |
| --- | --- | --- |
| construction | wrong Session, family, shape, type, or unknown schema | no output Dataset |
| admission at action | unbounded collection, missing authority, unsupported required check | input unchanged |
| execution | backend failure, timeout, limit exceeded, realized schema mismatch | input unchanged |
| materialization commit | storage, Evidence, or publication failure | no materialized Dataset returned |

Every failure is a structured `AnalysisError` with expected, received,
location, and concrete next action. No failure mutates a logical Dataset into a
failed state and no action silently falls back to pandas.

The later runtime and planner designs own exact subclasses and transaction
recovery, but must preserve these user-visible phases.

## Help and Disclosure

The disclosure ownership is:

```text
marivo.help("analysis.datasets")
  common value model, family navigation, state, and terminal boundaries

marivo.help("analysis.datasets.dataset")
marivo.help("analysis.datasets.logical")
marivo.help("analysis.datasets.materialized")
marivo.help("analysis.datasets.shape_id")
marivo.help("analysis.datasets.field_id")
marivo.help("analysis.datasets.field_identity")
marivo.help("analysis.datasets.physical_type_state")
marivo.help("analysis.datasets.field")
marivo.help("analysis.datasets.row_bound")
marivo.help("analysis.datasets.cardinality")
marivo.help("analysis.datasets.order_term")
marivo.help("analysis.datasets.ordering")
marivo.help("analysis.datasets.byte_count")
marivo.help("analysis.datasets.family_row_semantics")
marivo.help("analysis.datasets.row_contract")
marivo.help("analysis.datasets.row_set_contract")
marivo.help("analysis.datasets.schema")
marivo.help("analysis.datasets.logical_state")
marivo.help("analysis.datasets.materialized_state")
marivo.help("analysis.datasets.contract")
  exact public type contracts for common Dataset values and descriptors

marivo.help("analysis.<family>")
  family row meanings, shapes, properties, and operator navigation

marivo.help("analysis.actions.show")
marivo.help("analysis.actions.to_pandas")
marivo.help("analysis.actions.execute")
  exact action signatures, effects, bounds, and failures

marivo.help("analysis.datasets.fields")
  semantic-ref, stable-field-id, generated-name selector resolution, and
  non-expression boundary

marivo.help("analysis.datasets.field_ref")
  exact selector value fields, ownership, consumers, and rejection rules

dataset.contract()
  mechanically valid continuations and current Dataset authority

structured error
  repair for the exact failed construction or action
```

Help does not list generated SQL, private node kinds, planner phases, or storage
receipt fields. `show()` does not duplicate the full family capability matrix.

The accepted common, descriptor, and selector types have the following exact
contract:

| Public type | Status | Canonical Help leaf | Production and consumption contract |
| --- | --- | --- | --- |
| `Dataset` | accepted | `analysis.datasets.dataset` | abstract sealed base; annotations and `isinstance` only |
| `LogicalDataset` | accepted | `analysis.datasets.logical` | abstract logical-state base; downstream operators return this state; owns `execute()` |
| `MaterializedDataset` | accepted | `analysis.datasets.materialized` | abstract Artifact-backed state base; owns `show()` and `to_pandas()`; downstream operators return Logical state |
| paired concrete Dataset family classes | accepted when its owning family design freezes them | one registry-owned `analysis.<family>` leaf | sealed Logical/Materialized pair with one family id and identical operator admission |
| `DatasetShapeId` | accepted | `analysis.datasets.shape_id` | immutable validated family-local shape and semantic-version identity; bounded `repr` |
| `DatasetFieldId` | accepted | `analysis.datasets.field_id` | immutable validated stable field identity used by schemas, selectors, keys, and ordering; bounded `repr` |
| `DatasetFieldIdentity` | accepted | `analysis.datasets.field_identity` | sealed catalog-ref, typed Entity identity, runtime-Metric, or generated identity value; bounded `repr` |
| `DatasetPhysicalTypeState` | accepted | `analysis.datasets.physical_type_state` | sealed resolved or deferred type-state value; bounded `repr` |
| `DatasetField` | accepted | `analysis.datasets.field` | immutable logical field binding stored once in the row-contract schema and referenced by ids from coordinates, keys, ordering, family semantics, and selectors; bounded `repr` |
| `DatasetRowBound` | accepted | `analysis.datasets.row_bound` | sealed unknown, static, or runtime-policy row-bound value; bounded `repr` |
| `DatasetCardinality` | accepted | `analysis.datasets.cardinality` | sealed singleton or keyed row-set cardinality; bounded `repr` |
| `DatasetOrderTerm` | accepted | `analysis.datasets.order_term` | exact field, direction, null, and value-order contract; bounded `repr` |
| `DatasetOrdering` | accepted | `analysis.datasets.ordering` | sealed unordered or total ordered-term value; bounded `repr` |
| `DatasetByteCount` | accepted | `analysis.datasets.byte_count` | sealed exact or typed-unavailable byte-count value; bounded `repr` |
| `DatasetFamilyRowSemantics` | accepted | `analysis.datasets.family_row_semantics` | sealed kind-dispatched value whose complete variants are registered and documented by the owning family; bounded `repr` |
| `DatasetRowContract` | accepted | `analysis.datasets.row_contract` | immutable value returned by `dataset.row_contract`; bounded `repr` |
| `DatasetRowSetContract` | accepted | `analysis.datasets.row_set_contract` | immutable cardinality and ordering value returned by `dataset.row_set_contract`; bounded `repr` |
| `DatasetSchema` | accepted | `analysis.datasets.schema` | immutable ordered value returned by `dataset.schema`; bounded `repr` |
| `LogicalDatasetState` | accepted | `analysis.datasets.logical_state` | helper-produced logical-state descriptor; bounded `repr` |
| `MaterializedDatasetState` | accepted | `analysis.datasets.materialized_state` | helper-produced materialized-state descriptor; bounded `repr` |
| `DatasetContract` | accepted | `analysis.datasets.contract` | terminal value returned by `dataset.contract()`; bounded `repr`, pure `render() -> str`, and `show() -> None` |
| `DatasetFields` | accepted amendment | `analysis.datasets.fields` | helper-produced by `dataset.fields`; focused selector methods and bounded `repr`; no public constructor |
| `DatasetFieldRef` | accepted amendment | `analysis.datasets.field_ref` | helper-produced sealed selector with bounded `repr`, accepted by typed predicates and policies |

Every accepted named type in this table joins `mv.__all__` and the independent
public export snapshot. The two selector types join atomically with the accepted
amendment. A type object and its canonical string resolve to the same static
Help contract; a concrete value may add only bounded deterministic facts. The
grouping target `analysis.datasets` owns discovery only and does not replace
these exact type leaves.

No private descriptor type may appear in a public property or callable
annotation. Nested fields of these descriptors use closed built-in values,
public enums and refs, or another explicitly exported public type with its own
focused Help leaf. A family-specific `DatasetFamilyRowSemantics` variant remains
owned by its family module, but that module must freeze the variant's complete
fields and family Help contract before registration. Concrete variant
implementation classes are not additional public types or exports. Field
references are `DatasetFieldId` values, and a variant must not duplicate common
field bindings or Dataset definition and lineage facts.

Every concrete Dataset class and common action must likewise be reachable from
one canonical Help target. Public construction remains closed: export and Help
reachability do not imply that callers can instantiate state, contract, schema,
row-contract, fields, or field-ref values directly.

## Rejected Alternatives

### `AnalysisDataset` as the common name

Rejected because the module namespace already disambiguates it and the longer
name adds no semantic information.

### One public class with state-invalid methods

Rejected because a Logical Dataset that advertises `show()` or `to_pandas()`
teaches an invalid read path and delays a deterministic state error until
runtime. Paired state classes share one family registration while making the
valid method set structurally visible.

### A public `Plan` returned by operators

Rejected because it violates the all-operator-outputs-are-Datasets invariant
and moves row meaning away from the value being composed.

### Python generics for catalog-defined shapes

Rejected because they cannot soundly encode runtime semantic identities and
would create false static guarantees.

### Optional `ref`, `row_count`, and Evidence fields on every Dataset

Rejected because it creates a mega-object whose fields silently change meaning
by state. The discriminated state union keeps authority explicit.

### Implicit `len(dataset)` and DataFrame-like column access

Rejected because they either execute invisibly or escape the typed algebra
without an explicit terminal boundary.

### Python object identity as the execution binding

Rejected because it cannot survive a process or script rerun. The Session-owned
`DatasetExecutionKeyV1` and its write-once Artifact binding own reuse.

### `show()` returning a preview Dataset

Rejected because `show()` reads a Materialized Dataset and its preview rows do
not form a second Dataset. A Dataset-returning row restriction must be an
explicit lazy operator.

### `Dataset.render()` as a non-executing twin of `show()`

Rejected because a Materialized Dataset preview requires an Artifact read,
while a pure `render()` cannot contain rows without hidden I/O. Dataset is
therefore outside `AgentResult`; the non-executing `DatasetContract` retains the
shared `render()`/`show()` terminal protocol.

### `to_pandas(limit=...)`

Rejected because the result would no longer represent the Dataset while hiding
row-selection semantics inside a terminal action.

### Pickling logical Datasets or inferring identity from scripts

Rejected because arbitrary Python serialization and call-site identity create a
second recovery mechanism without committed authority. A rerun reconstructs
the logical definition and resolves its Session execution binding.

### `execute(storage=..., retention=..., name=...)`

Rejected because it turns the analytical checkpoint boundary into a storage
management surface and makes downstream authoring depend on physical placement.
Typed Session runtime policy owns those choices.

### Requiring current catalog authority for common materialized reads

Rejected because it makes an immutable committed snapshot unreadable after
semantic evolution. Current-authority checks belong to explicitly declared
operators and `session.revalidate(...)`.

### Datasource-natural unordered previews

Rejected because repeated agent reads could show different rows without any
Dataset definition change. Canonical ordering either succeeds within action
bounds or fails explicitly.

### Async Dataset actions in the first cutover

Rejected because futures, task handles, cancellation states, and detached
completion would introduce another public lifecycle beside immutable Dataset
state and Session Runs.

## Cross-Module Seams

### Observation Model consumes

- nominal `PopulationDataset` and `MetricDataset` families;
- the row-contract and row-set-contract construction protocols;
- selector-only `DatasetFieldRef` values resolved from current row contracts;
- complete pre-execution schema requirements;
- immutable operator construction;
- Session ownership, definition fingerprints, and private input authority
  tokens.

It supplies family-specific Population and Metric row semantics, row contracts,
row-set contracts, and coordinate transitions.

### Planner and Pushdown consumes

- opaque logical and materialized root handles;
- private logical and materialized input authority tokens;
- exact row contracts and row-set contracts;
- action-time requirements;
- canonical presentation-order requirements;
- materialized scan-leaf non-rewriteability.

It must not add public plan inspection.

### Materialization Runtime consumes

- paired `LogicalDataset.execute() -> MaterializedDataset` classes;
- the zero-argument action and Session-owned storage-policy boundary;
- the write-once Session execution-binding seam;
- schema compatibility requirements;
- mandatory exact realized row count;
- retained-authority rules for common materialized reads;
- the private scan-leaf publication seam;
- action authority distinctions.

It supplies committed materialized state, execution-binding recovery, and
producer action records.

### Typed Operators consumes

- nominal family ids;
- family-qualified shapes;
- runtime row-contract and row-set-contract admission;
- output Dataset construction;
- logical versus materialized backing facts without transparent reach-through.

It supplies operator-specific input/output, row-contract, and row-set-contract
rules.

### Subject, Event, and Lifecycle consumes

- `EventDataset` and `LifecycleDataset` nominal families, plus source-owned
  selection producers of Module 2's Population family;
- the common state and action contracts;
- registry-declared semantic refinement rather than accidental Python
  inheritance.

It supplies family-specific subject identity and row meanings.

## Vertical Acceptance Journeys

Journey fixtures must choose an explicit compatible execution/storage setup.
A retained Population or identity selection later joined to current sources uses
an engine target and reader in that same datasource domain when its registered
contract requires source-private identity work. Local/object Artifact-only
continuations use authorized PyArrow reads and bounded pandas steps. Include
both explicitly admitted independent-source pandas inputs and a source-required
domain conflict that cannot be repaired locally. `.execute()` is a repair only
when its configured writer, reader and consuming method can satisfy the exact
authority and resource requirements. No fixture may replay an Artifact origin,
upload local output or create an internal DuckDB executor.

### Logical DAG construction and governed execution

```python
features = session.observe(metrics=[scanned_bytes, cpu_seconds])

assert features.state.kind == "logical"
association = features.correlate(method="spearman")
assert association.state.kind == "logical"

materialized = association.execute()
materialized.show()
```

Acceptance evidence must prove that construction performs no datasource work,
operators compose an arbitrarily deep logical DAG, `execute()` publishes one
Artifact, and `show()` reads only that immutable backing.

### Terminal collection without re-entry

```python
ranked = features.rank(
    by=features.fields.metric(cpu_seconds),
    order="descending",
)
small = ranked.limit(100).execute()
frame = small.to_pandas()
frame["cpu_seconds"] = 0

assert small.state.kind == "materialized"
```

Acceptance evidence must prove exact schema/order, defensive isolation, action
bounds, no second Artifact publication, and rejection if `frame` is passed to a
Dataset operator.

### Same-family materialization and reuse

```python
checkpoint = features.execute()

assert isinstance(features, mv.LogicalMetricDataset)
assert isinstance(checkpoint, mv.MaterializedMetricDataset)
assert checkpoint.row_contract == features.row_contract
assert checkpoint.row_set_contract == features.row_set_contract
assert checkpoint.state.kind == "materialized"

association = checkpoint.correlate(method="spearman").execute()
outliers = checkpoint.metric(cpu_seconds).discover.entity_outliers(limit=25).execute()
association.show()
outliers.show()
```

Acceptance evidence must prove one committed Artifact backing, no mutation of
`features`, two downstream scan-leaf reads, and no transparent re-execution of
the original observation graph.

### Definition identity versus input authority

```python
logical_association = features.correlate(method="spearman")
checkpoint = features.execute()
snapshot_association = checkpoint.correlate(method="spearman")

assert checkpoint.definition_fingerprint == features.definition_fingerprint
assert snapshot_association.definition_fingerprint != logical_association.definition_fingerprint
```

Acceptance evidence must prove that materialization preserves the origin
definition, while a downstream operator binds the logical or materialized input
authority token and therefore cannot reuse the wrong cache entry.

### Deterministic unordered preview

```python
checkpoint.show()
checkpoint.show()
```

Acceptance evidence must execute against a backend whose natural scan order is
deliberately varied and prove identical preview rows and order. A backend that
cannot implement the canonical order within bounds must return the registered
typed action error rather than arbitrary rows.

### Generated-value logical ordering

Acceptance evidence must construct one ranked Dataset and one Candidate Dataset
without execution and prove that their row-set `DatasetOrdering` values name generated
`rank`, `score`, and `item_id` fields with exact direction, null placement,
value-order contracts, and unique tie-breakers. Materialization and cold
recovery must preserve the identical logical ordering value, and a following
`limit(...)` must consume that order rather than backend or storage order.

### Materialized reads after catalog drift

```python
state = checkpoint.state
assert isinstance(state, mv.MaterializedDatasetState)
recovered = session.artifact(state.artifact_ref)
recovered.show()
frame = recovered.to_pandas()
```

Acceptance evidence must remove or revise an originating semantic entry after
materialization and prove that common reads still use retained Artifact
authority. A separate explicit revalidation read may report drift; it must not
mutate or make the recovered Dataset unreadable.

### Cross-script execution-binding recovery

Acceptance evidence must execute a logical Dataset in one process, reconstruct
the same definition under the same Session in a second process, and prove that
`execute()` validates and recovers the bound Artifact without datasource
resolution, SQL execution, or a new Run. Changing a row-affecting definition or
implementation contract must derive another execution key. A different Session
must not see the binding. Exact ref-based `session.artifact(...)` recovery
remains available when the logical definition is not reconstructed.

### Cross-Session Materialized reuse and Logical rejection

```python
prior = session_a.observe(metrics=[revenue]).execute()
selected = session_b.artifact(prior.state.artifact_ref)
next_result = selected.limit(100).execute()
```

Prove unchanged original owner/ref/producer/storage/Findings; the read creates
no Run, Artifact, local binding, copy, or graph edge. `next_result` belongs to B
and records A's Artifact as an input. No age, origin-state, or suitability check
occurs. A busy or recovery-blocked A does not block reading its committed result.
Also pass A's Materialized operand directly to a B-owned operator whose concrete
row contract permits it and prove B remains the consuming Session.

Foreign Logical inputs still fail before datasource work or Run admission:

```python
left = session_a.observe(metrics=[revenue])
right = session_b.observe(metrics=[revenue])
left.compare(right)
```

Selector identity is independently enforced. A foreign selector is not accepted
because its display name matches; the caller reacquires fields from the actual
input handle, including a handle read with `session_b.artifact(ref)`. A
cross-Store Artifact fails without implicit import or owner rewriting.

### Recovered runtime-Metric selection

Acceptance evidence must materialize a `MetricDataset` containing a
`RuntimeMetricExpr`, recover it in a fresh process where the original expression
object does not exist, read its exact `DatasetFieldId` from the retained schema,
and reacquire the selector through `recovered.fields.get(field_id)`. A
selector-only operator such as `rank(by=...)` must then construct without
catalog lookup, datasource work, or Run creation. The same test must prove that
`recovered.fields.get(runtime_metric_public_name)` reacquires the same binding
only when the name is an exact current public schema member.

### Executable value versus terminal result protocol

Acceptance evidence must prove that a Dataset has no public `render()` member
and does not satisfy the runtime `AgentResult` protocol, while
`dataset.contract()` does satisfy it. Calling `contract.render()` and
`contract.show()` must produce identical bounded text, modulo the newline
printed by `show()`, and must not create a Run or perform authority
revalidation. Logical Datasets must expose neither `show()` nor `to_pandas()`;
Materialized Dataset reads must never execute datasource SQL.

## Acceptance Criteria

This design is complete when all of the following are reviewable and later
testable:

1. `Dataset` is the one public abstract common type;
2. every public analysis result belongs to one registered nominal family;
3. family-specific shape is a runtime `DatasetShapeId`, not a fake Python generic
   promise or an independently authored family/version tuple;
4. every Dataset has complete public columns, row identity, and semantic type
   constraints before execution;
5. every family owns paired logical and materialized public classes;
6. state-dependent facts live in the matching immutable state descriptor;
7. operators accept both state types, perform no datasource work, and always
   return a Logical Dataset;
8. `repr` and `contract()` never execute;
9. only Materialized Datasets expose `show()`, which returns `None` and reads no
   logical origin;
10. only Materialized Datasets expose `to_pandas()`, which returns all rows or
    fails, never silently truncates or
    samples;
11. `execute()` returns the paired Materialized Dataset only after durable
    authority is committed or an exact Session binding is recovered;
12. a materialized Dataset is a private immutable scan leaf for downstream
    planning;
13. raw Python identity, script path, line number, and variable name do not
    define execution reuse; Core preserves semantic sharing through canonical
    occurrence labels, so reconstructed equal sharing graphs have equal keys
    while shared and separately authored sampling branches have different keys;
14. DataFrame-like implicit reads and inbound pandas re-entry are absent;
15. explicit same-Store Materialized inputs may cross Sessions without copying
    or ownership changes; foreign Logical inputs and selectors fail locally;
16. no public plan, SQL, Ibis, task, future, or receipt abstraction is exposed;
17. logical definition, committed execution, preview, and collection authority
    are never conflated;
18. every public common type, concrete family, and action is exported as
    specified and reachable through one canonical focused Help target;
19. `definition_fingerprint` is distinct from the private authority token used
    when a Dataset becomes an operator input;
20. logical Datasets have no pickle or Artifact ref, while reconstructing an
    exact definition may resolve its write-once Session execution binding;
21. `execute()` is zero-argument and storage policy remains Session-owned;
22. common materialized reads depend on retained Artifact authority rather than
    current catalog authority;
23. every materialized Dataset owns an exact realized row count;
24. the row-set contract owns cardinality and a typed total order over admitted
    current fields, while
    unordered inspection and collection derive canonical row-key order or fail;
25. every first-cutover execution and read is synchronous and returns no task
    or future;
26. logical and materialized Dataset states can be paired only with their
    matching private root variant, and materialization or recovery retains no
    second executable logical root;
27. the public descriptor graph uses only the accepted closed types and contains
    no private or nullable state-dependent annotation;
28. executable Dataset values deliberately do not implement `AgentResult`, while
    `DatasetContract` implements its exact `repr`/`render`/`show` floor;
29. private Arrow transfers, pandas DataFrames, numerical outputs and Runtime
    staging remain action-internal and cannot construct Materialized Dataset state;
30. execution retains contiguous eligible Ibis source work, determines a bounded
    pandas suffix before data work, and never retries a failed source step locally;
31. local/object Artifact continuations obey complete local input and retained-part
    guards; storage capacity alone does not promise local computational capacity.

## Filter-Selector Amendment Acceptance

The accepted amendment is complete because all of the following are
independently reviewable and later testable:

1. `dataset.fields` resolves only exact fields in the current row-contract schema;
2. semantic Metric and Dimension selectors resolve by exact semantic identity;
3. `get(field_id)` reacquires any exact current binding, including a recovered
   runtime-Metric field;
4. `get(name)` resolves one exact current public field, including family-owned
   generated fields without semantic identity;
5. missing, ambiguous, wrong-role, stale, or unrelated selectors fail locally;
6. a selector retains its exact owning Session and fails locally when consumed
   by a Dataset from any other Session, even when field and semantic identities
   otherwise match;
7. `DatasetFieldRef` is selector-only and cannot read, calculate, compare, or
   iterate values;
8. field selection never executes, creates a Run, or changes Dataset state;
9. no `__getitem__`, dynamic column attribute, Series, expression, SQL, or Ibis
   surface is introduced;
10. `DatasetFields` and `DatasetFieldRef` join the public export and exact Help
   contract while remaining helper-produced;
11. predicate and filter semantics remain owned by Module 2 rather than Dataset
   Core.

## Accepted Filter-Selector Amendment Decisions

The amendment accepts these additional choices without changing the core
decisions frozen below:

1. the selector resolver is named `dataset.fields`;
2. exact semantic fields use `fields.metric(...)` or
   `fields.dimension(...)`;
3. every exact retained field can be reacquired through `fields.get(field_id)`;
4. every exact public field name, including a family-generated name, can be
   resolved through `fields.get(name)` against the current public schema only;
5. the selector value is the public sealed, Session-owned `DatasetFieldRef`;
6. field refs are valid operator inputs only for Datasets owned by the same
   exact Session;
7. field refs are never public column expressions or
   terminal values;
8. the later filtering contract owns all predicate construction, composition,
   effects, and ordering.

## Frozen Module Decisions

The following choices are accepted:

1. the common state-base names are `LogicalDataset` and
   `MaterializedDataset`, under the shared Dataset family contract;
2. bracketed shape notation remains architectural notation rather than public
   Python generics;
3. public columns and row identity are complete before execution, while exact
   physical dtypes may be deferred within a closed semantic type class;
4. state-dependent Artifact facts live only on
   `MaterializedDatasetState`, not nullable Dataset properties;
5. `show()` prints and returns `None`;
6. Materialized Datasets do not expose `execute()`; reconstructing an exact
   Logical Dataset resolves the write-once Session binding without a new Run;
7. the public property is `definition_fingerprint`, not
   `logical_fingerprint`;
8. logical Python objects are in-process only and cannot be pickled, while their
   exact reconstructed definitions can recover Session-bound execution results;
9. `execute()` accepts no public storage, retention, placement, or naming
   arguments;
10. common reads of a materialized Dataset validate retained Artifact authority
    without requiring the current catalog;
11. every materialized Dataset has an exact `realized_row_count`;
12. unordered previews use canonical row ordering and fail if it cannot be
    established safely;
13. first-cutover actions are synchronous and expose no async or cancellation
    handle;
14. every common public type has one exact top-level export and focused Help
    leaf, and no private descriptor appears in a public annotation;
15. logical and materialized classes pair only with their matching private root
    variant, and a materialized Dataset retains no executable logical root;
16. shape, field, type-state, row-contract, row-set, row-bound, cardinality,
    ordering, and byte-count facts use the closed public descriptors frozen by
    this module;
17. declared logical ordering is a total sequence of typed current-field terms,
    not a coordinate-only or backend-natural order;
18. executable Dataset values are outside `AgentResult`, while the non-executing
    `DatasetContract` retains the full terminal result protocol.
19. every registered downstream operator is available on both state classes and
    always returns the Logical class of its output family, preserving arbitrary
    lazy Dataset DAG construction.
20. the eighteen paired concrete class names are the only family-class exports;
    unqualified nominal family names remain prose shorthand and never become
    public aliases.
21. `MaterializedDataset.evidence_digest` is the canonical producer of the
    retained public `ArtifactDigest`, and `findings(...)` / `finding(...)` are
    the canonical Finding reads; Logical Datasets expose no Evidence read.

Decision 6 avoids repeated-Run audit noise during script reruns because a
binding hit is recovery, not execution. Decision 12 intentionally accepts
Artifact-read sort cost in exchange for deterministic agent-visible output.
Changing any of these decisions requires an explicit amendment to this module
before a downstream design relies on the replacement contract.

## Final Boundary

A Dataset is an immutable, Session-owned analytical value whose family and row
meaning are known before execution.

Laziness changes when rows are computed, not what kind of public value flows
through the DSL. Materialization changes reusable backing authority, not the
Dataset's analytical family. The closed row contract owns one-row meaning, the
closed row-set contract owns cardinality and ordering, and the normalized
Dataset definition owns predicates, sampling, source/input lineage, fingerprints,
and lineage. Everything related to SQL, execution, storage, and publication
stays behind the Dataset boundary.
