# Loaded semantic definition reading

Status: implemented public contract, `marivo.semantic_definition/v1`.

## Entry point and identity

```python
import json
import marivo
import marivo.semantic as ms

catalog = ms.load()
entry = catalog.metrics.get("growth.retail_quarter_to_date_campaign_spend")
definition = entry.details().definition
definition.show()
payload = definition.to_dict()
json.dumps(payload, allow_nan=False)
marivo.help("semantic.SemanticDefinition")
```

Metric, Measure, Dimension and TimeDimension Details expose `definition`.
Other Details kinds have no computation definition. A definition is an immutable
returned result containing `ref`, `catalog_definition_fingerprint`, `node`,
`source_location`, and `temporal`. It follows the bounded repr/show/render family.
`to_dict()` returns a fresh JSON-safe mapping, never a generic Details/IR export.

The schema field is `marivo.semantic_definition/v1`. Ref objects use the existing
`{"schema": "marivo.semantic_ref/v1", "kind": "metric", "path": "growth.campaign_spend"}`
encoding. Nodes contain only direct dependencies: follow their typed Python Refs
with `catalog.require(ref)` on the same Catalog. Linear occurrences are not
coalesced. There is no public closure traversal or alternative object identity.
The existing fingerprint denotes the compiled calculation snapshot, not data
freshness, readiness, or business approval. Display-only declaration markers and
expression descriptions do not change calculation fingerprints.

## Node support matrix

| `node.kind` | Typed Python fields | JSON behavior |
| --- | --- | --- |
| `aggregate` | `operation`, `target`, `filter` | Operation kind and optional percentile `q`; exact target Ref and target kind; predicates below. |
| `weighted_mean` | `value`, `weight`, `filter` | Exact measure Refs with distinct roles. |
| `ratio` | `numerator`, `denominator` | Exact metric Refs. |
| `linear` | Ordered `(sign, metric Ref)` terms | Ordered objects with `sign` and `metric`; duplicates remain. |
| `cumulative` | `base`, `over`, `anchor` | Exact base Ref; explicit/default selection; tagged anchors. |
| `expression` | `status`; `expression` when supported, `reason` otherwise | Supported expression node or explicit unsupported reason. |

`filter` is an ordered tuple of `(field Ref, WhereValue)` pairs in Python and a
list of predicate objects in JSON. Each contains `dimension`, `operator=eq` with
`value`, or `operator=in` with `values`. Scalars retain string, boolean, integer,
or finite float type. Empty filter means no declared predicate. Exact field
identity is resolved from the existing target-entity rule, including time
dimensions.

Cumulative `over=None` means default selection in this description, even when
loading resolves a unique axis for execution. The JSON variant is
`{"selection": "default", "resolution": "context_required"}`; it does not publish
a final selected axis. Explicit selection includes its time-dimension Ref.
Candidate axes are discovery facts and are never substituted for `over`.

Python anchors retain the existing `CumulativeAnchor` values and `Grain` objects.
JSON anchors are `{"kind": "all_history"}`, `{"kind": "trailing", "count": 3,
"unit": "day"}`, or `{"kind": "grain_to_date", "grain": ...}`. Builtin grains
contain `kind`, `unit`, `count`; semantic grains contain `kind`, `calendar` as
RefPayloadV1 and `level`. No concrete date, current-day cutoff or result is
inferred. [Temporal semantics](../temporal-semantics.md) owns boundary behavior.

## Expression descriptions and disclosure

Descriptions are captured during existing AST compilation and stored with the
immutable expression sidecar. Reading does not inspect source or call the body.
This describes syntax; it does not prove execution or data correctness.

Serialized expression nodes independently expose `display` with `language="python"`,
`form="normalized_ibis"`, `text`, `bindings` (alias plus exact Ref), and
`redacted_literals`. Consumers display this text directly as code, without
reconstructing syntax from `expression`. Entity aliases such as `t1` qualify
columns; field aliases such as `f1` identify semantic field Refs. The text is
normalized Ibis syntax, not original source or a standalone runnable program.
Literal values remain hidden as `REDACTED_<TYPE>` identifiers. A structural `status=unsupported` does not prevent display of calls such as
`.count()` or `.sum()`. Display capture bounds are 1,024 AST nodes, depth 64, and
16,384 characters; unavailable or oversized syntax has no display text.
Display failure does not change structural status or discard a supported tree;
structural syntax and budget checks run independently.
Formatting uses only the captured AST and does not
change fingerprints or execute expressions.

| Supported structure | Description |
| --- | --- |
| `rows.column`, `rows["column"]`, column constructors | `column` with Entity Ref and column name. |
| `ms.bind(field, rows)` | `field` with exact field and Entity Refs. |
| `.cast("float64")` | `cast` with operand and fixed data type token. |
| `+`, `-`, `*`, `/`; unary `+`, `-`, `~` | Ordered binary/unary syntax with the authored operator token. |
| `==`, `!=`, `<`, `<=`, `>`, `>=`; `&`, `|`, `^` | Comparisons and authored operator tokens. |
| `condition.ifelse(a, b)` | Condition and ordered true/false branches. |
| Literal string, integer, float, bool or None | `redacted` with `value_type`, never its value. |

Cast tokens are int8/16/32/64, uint8/16/32/64, float32/64, string, boolean,
date and timestamp. Other calls, arbitrary type strings, external names, Python
boolean control flow, and unsupported nested forms return
`status=unsupported, reason=unsupported_syntax`. Missing captured descriptions use
`description_unavailable`; trees exceeding 256 nodes or depth 32 use
`limit_exceeded`. No partial tree is presented as a complete formula.
Source location remains available on the owning definition.

Binary `operator` values preserve source tokens (`+`, `-`, `*`, `/`, `&`, `|`,
`^`) rather than claiming a resolved Ibis operation. The same token can resolve
differently by operand dtype, such as numeric addition versus string concatenation
or boolean versus integer bitwise operations. Definition reading does not infer
those operand types.

Literal disclosure is separate from declared filter values. There is no disclosure
switch. SQL provenance, source text, callable repr, credentials, and datasource
configuration are never exported. Identifiers and business descriptions remain
untrusted text for consumers; this protocol performs no HTML execution. Original
business context and guardrails remain on their owning Details, not merged into
other objects. TimeDimension source expressions remain distinct from its parse,
granularity, and timezone fields.

## Temporal rules and failures

`temporal` distinguishes measure or body-metric declared rules, aggregate metric
fold overrides, and effective rules resolved by the existing temporal resolver.
Python rules contain exact `over` Ref and the existing typed `fold` value.
An absent declaration/override serializes as `status=not_declared`; a resolved
rule identifies `source=declared|measure|metric_override`. Percentile folds retain
`q`. Effective classification follows the loaded composition and dependencies:

- `not_applicable`: no independent status-time fold applies. This includes
  ordinary aggregates and linear compositions whose complete recursive inputs
  are additive and have no independent fold or component-defined time behavior.
  An amount sum/difference therefore does not require observation to explain it.
- `component_defined`: the calculation follows the existing node and component
  rules, without claiming one equivalent top-level fold. This includes ratios,
  cumulative nodes, and linear compositions containing folded, non-additive, or
  component-defined inputs. Follow the direct Refs in `node` through the same
  Catalog; no duplicate dependency tree is serialized.

`component_defined` describes an available definition, not missing context or a
requirement to execute observe. Neither status licenses arbitrary summation of
non-additive metrics or cumulative values along time. No fold is inferred from
metric names, additivity alone, or matching component folds. Cumulative `over`
and `anchor` retain their separate node contract, including default axis selection
marked `context_required`; no effective fold currently has a context-required
variant because no concrete missing fold context is identified by this reader.

Classification reads only loaded IR, within the existing metric graph limits of
10 levels and 256 occurrences. Missing dependencies, cycles, unresolved declared
status axes, and inapplicable overrides fail with `SemanticDefinitionReadError`
instead of becoming `not_applicable`. Loading and execution retain their existing
ambiguity/conflict checks. Classification does not alter the calculation graph
or its fingerprint.

`SemanticDefinitionReadError` identifies the object, declaration location,
expected/received facts and a typed reauthor repair when projection cannot
faithfully represent loaded state. Unsupported expression syntax is a returned
availability state, distinct from an internal read failure. `ms.where` rejects
non-finite numeric parameters during authoring; projection also fails closed
instead of emitting non-standard JSON if malformed loaded state is encountered.

## Read boundary and acceptance

After Catalog loading, definition acquisition, serialization, and display do not
read source, resolve credentials, connect, query, materialize expressions, compile
SQL, create Sessions, run readiness, certify calendars, or write files. Existing
trusted Python model loading retains its own side-effect contract.

Independent tests cover the retail-quarter chain, anchor variants, roles and
repeated terms, structured filtering, expression disclosure, inherited/overridden
folds, strict JSON, immutable results and forbidden IO/execution entrypoints.
Existing Catalog and calculation regressions remain part of the broad gate.
The real ecommerce demo and wheel-installed consumer smoke provide package-level
acceptance. DSH Runtime/browser integration is a separate downstream acceptance.
