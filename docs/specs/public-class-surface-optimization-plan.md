# Public class surface optimization plan

Status: implemented. Unified acceptance and retained-candidate decisions are
recorded in the [implementation progress](public-class-surface-implementation-progress.md).

## Goal and baseline

Reduce the number of class names agents must choose between when authoring and
running Marivo code. Keep complete typed results and discoverable field contracts.
Removing a root export reduces the execution namespace; it does not by itself
remove a type from the public result contract or reduce Help reading cost.

This proposal follows the grouped class audit. The refreshed baseline on
2026-10-20 is commit `fe674d76dc6684021d972e33dbd14fb529579722` plus existing local
changes. Runtime imports resolve to this checkout through `.venv/bin/python`.
Counts below use classes in each package's `__all__`, excluding every
`BaseException` subclass. Functions, modules and type aliases are not classes.

| Package | Current classes | Primary candidates | Additional conditional candidates |
| --- | ---: | ---: | ---: |
| `marivo.datasource` | 22 | 6 | 0 |
| `marivo.semantic` | 31 | 5 | 3 |
| `marivo.analysis` | 32 | 9 | 0 |
| `marivo.ontology` | 2 | 0 | 0 |
| Total | 87 | 20 | 3 |

Removing all primary candidates would leave 67 root-exported classes. Removing
the three conditional candidates would leave 64. These are planning estimates,
not quotas: retain a class when a supported typed workflow requires its root name.
The final report must give the actual count and a reason for each retained candidate.

Existing local edits include analysis designs and changes to definition, temporal
grid, data schema and frame structure modules. Preserve them and rebaseline before
implementation. This proposal does not supersede the existing
[optimization progress record](public-surface-optimization-progress.md), which
describes an earlier completed scope.

## Contract decisions

1. Keep directly authored input types, major operation results and navigable
   catalog types public. A class is not redundant merely because a factory can
   return it or because it contains data.
2. Prefer canonical factories for scope and authoring-value construction. Remove
   unnecessary root imports and constructor teaching together, after checking
   annotation and runtime discrimination consumers.
3. Nested result records keep their fields, immutability, exact types, useful
   inspection methods and type Help. Obtain them through their owning result.
   Root removal must remove the root binding as well as its `__all__` entry.
4. Do not replace concrete types with dictionaries, `object`, `Any`, broad casts,
   or a many-optional-fields record. Preserve closed variants and discriminants.
5. Do not add a new `types` namespace, compatibility aliases, deprecated forwarding
   attributes, constructor guards, or wrapper classes just to lower a count.
   Existing supported typing paths may be used where they already exist.
6. Keep runtime class identities, serialization names, persisted keys, fingerprints
   and storage versions stable. Changing an import exposure does not justify a
   representation migration or renaming the implementation class.

The previous audit's suggestion to remove source IR Help solely because of its
implementation terminology is narrowed here. The datasource contract explicitly
defines `TableSource` as a union of four concrete source records. Likewise, the
analysis contract explicitly exposes complete method facts. These types require
discoverable contracts even when their constructors are removed from task discovery.

## Candidate decisions by group

### Datasource: six root export candidates

| Classes | Intended change | Preserved contract |
| --- | --- | --- |
| `PhysicalExtent`, `Partitioning`, `ExecutionCapabilities` | Remove root exports; describe acquisition through `SourceInspection` and `PartitionInspection` | Complete inspection fields, field Help and exact value types |
| `DatasourceFailure` | Remove root export; acquire through `DatasourceTestResult.failure` | Stable failure codes, structured cause fields and repair behavior |
| `PartitionScope`, `UnprunedScope` | Remove root exports after migrating supported construction to `partition`, `time_range` and `unpruned` | Scope types, accepted consumers, bounds, timeout rules and half-open time semantics |

Current internal tests construct some records as fixtures. Migrate fixture imports
to their owning modules where appropriate; fixture construction alone is not a
reason to retain an agent-facing constructor. Test public workflows through the
canonical factories instead of teaching unsupported construction.

`CsvSourceIR`, `JsonSourceIR`, `ParquetSourceIR`, `TableSourceIR` and `SourceParamIR`
are already absent from root exports. Keep their typed-return contracts and
resolvable native type/field routes. Public examples start from `csv`, `json`,
`parquet`, `table` and `source_param`. Type pages explain how to obtain and inspect
the values, without suggesting direct IR construction. Do not hide these values
behind untyped containers or add replacement wrapper types.

Retain the five datasource specs, credential protocol classes, catalog, principal
management/inspection results, `DiscoverySnapshot` and the independent terminal
`RawSqlResult` contract. `DatasourceSpec` and `TableSource` remain concrete unions;
they are not included in the class count.

### Semantic: five primary and three conditional candidates

| Classes | Intended change | Preserved contract |
| --- | --- | --- |
| `ReadinessInputSummary`, `ReadinessIssue`, `SourceHealthCheckResult` | Remove root exports; acquire from the owning reports | All report fields, issue/check variants, repairs and inspection behavior |
| `PeriodCorrespondence` | Remove root export; teach only `period_correspondence(...)` for construction | Exact factory return type and `period_calendar(correspondences=...)` validation |
| `AiContextValue` | Remove duplicate root export; use `ai_context(...)` for authoring and existing `ms.typing.AiContextValue` for explicit annotations | Current context fields and concrete parameter/return types |
| `PeriodCalendarKind`, `TemporalSetKind`, `WorkScheduleKind` | Conditionally remove root exports after verifying supported generic annotations | Specific `Ref[K]` inference and kind/root checks |

The three marker types have a separate decision gate: demonstrate that documented
typed authoring workflows remain possible without private imports or loss of
specificity. Otherwise retain these root names and record that reason. Do not
invent another namespace or weaken `Ref[K]` to pass the gate.

Keep `SemanticCatalog`, the Catalog entry/detail/page family, `JoinKey`, `Ref`,
`SemanticKind`, `ExpressionContract`, `PreviewBatchResult`, `ReadinessReport` and
`SourceHealthReport`. Catalog values carry navigation and typed inspection
operations; they are not part of this constructor reduction. Keep the existing
Help-only `Entity`, `Relationship`, `FinalizedDeclarations`, `PreviewResult` and
`SourceCheckNamespace` contracts.

### Analysis: nine nested fact record candidates

Remove these root exports as one coherent candidate group:

- `FrameMethodValue`, `FrameMethodCoordinate`, `FrameMethodSupport`;
- `FrameAdditiveMethod`, `FrameRatioMethod`, `FrameLinearTerm`, `FrameLinearMethod`;
- `FrameMethodMetric`, `FrameMethodParent`.

Retain `FrameMethodFacts` and `AnalysisFrame.method_facts` as the aggregate read
contract. Its full nested records and their Help remain available from returned
facts. Teach variant inspection through the existing `kind` discriminants where
they apply, including `additive`, `ratio_component` and `linear`; do not invent
new discriminants for ordinary records.

Before removal, inspect public `isinstance` and explicit annotation usage. If an
existing supported use cannot migrate through the concrete result contract without
private imports or weakened types, retain the affected export. The nine names are
a removal candidate group, not evidence that typed result variants are unnecessary.

Keep `AttributeEndpoint`: users construct it as an actual input to
`session.attribute`. Keep the other Frame, Column, Population, Grouping, time,
Session, Artifact, Finding and Run contracts. Keep the existing Help-only output
evidence/coordinate and session history result types.

### Ontology

Keep `OntologyCatalog` and `SemanticEdgeRef`. The audit found no duplicate
construction path or incidental record export that justifies a change here.

## Delivery sequence and ownership

One implementation owner coordinates these stages in order. This plan does not
require parallel agents. Each implementation stage updates its native Help,
current API documentation, affected examples and export snapshots in the same
change. Do not leave intermediate commits with broken discovery links.

| Stage | Owner | Dependencies | Deliverable | Acceptance |
| --- | --- | --- | --- | --- |
| S0 | Implementation owner | None | Freeze current imports, type acquisition routes and consumer inventory; record keep/remove decisions | A01, A02 |
| S1 | Implementation owner | S0 | Datasource six-candidate reduction and factory-oriented source Help | A03, A04, A07 |
| S2 | Implementation owner | S0, S1 | Semantic five-candidate reduction and explicit marker decision | A03, A05, A07 |
| S3 | Implementation owner | S0, S2 | Analysis nested fact export reduction with retained complete fact reading | A03, A06, A07 |
| S4 | Implementation owner and independent reviewer | S1–S3 | Unified acceptance, final counts, retained-candidate rationale and review findings | A01–A08 |

S0 records actual call sites in library code, tests, current docs and packaged
skills. Distinguish fixture-only construction, supported user construction,
runtime class discrimination and static type annotation. Confirm Help type
identity through native bindings rather than comparing class names to snake_case
target strings. Previous collector diagnostics belong to their recorded source
snapshot and must not be treated as current product failures.

Formal review has at most two rounds. Fix P2+ findings and rerun affected
acceptance. Unresolved P2+ findings block completion; do not silently start a third
review cycle. Preserve failures and distinguish preexisting issues from regressions.

## Acceptance and validation

| ID | Required evidence |
| --- | --- |
| A01 | Reproducible non-error class counts for actual root exports; exact removed and retained names; no forwarding root bindings or new aliases |
| A02 | Every retained public type, callable and field has a resolvable native Help route; type acquisition points to an existing public producer or owning field |
| A03 | Supported workflows and focused typing require no private imports, new ambiguous types or constructor alternatives; removal decisions include annotation and discrimination consumers |
| A04 | Datasource inspection/sample and connectivity-result workflows retain field values, failure codes, scope validation, temporal bounds and credential boundaries |
| A05 | Authoring/finalization and report reading retain exact refs, kind checks, correspondence validation, context fields and report contents |
| A06 | Attribute facts retain complete ordered records, numeric/null precision, method variants, bounded show behavior and persisted/cold-read parity; no source replay |
| A07 | Current API docs, native registries, dynamic guidance, affected CLI/Python examples and English/Chinese latest pages agree; no unreachable or constructor-teaching links to removed root names |
| A08 | Focused checks and one unrestricted final `make check-agent` pass; review closes with no unresolved P2+ findings and evidence scoped to the final source |

Use existing behavior-owned tests rather than introducing a second class inventory
in renderers. Update `tests/surface/exports.py` and the existing export/import,
disclosure and domain tests. Representative scopes already exist under
`tests/surface/`, `tests/datasource/test_datasource_live_help.py`,
`tests/semantic/test_semantic_readiness.py`,
`tests/semantic/test_semantic_source_health.py` and
`tests/analysis/graph/test_frame_method_facts.py`.

During implementation, run narrow `make test TESTS='...'`,
`make typecheck TYPECHECK_TARGETS='...'` and `make lint-agent LINT_TARGETS='...'`
for each affected stage. Run the unrestricted `make check-agent` once after source
freeze; repeat only for changes or failures requiring it. Runtime/provider and
release acceptance are separate evidence; no provider qualification claim follows
from this surface cleanup.

Update owning contracts where the public import boundary changes:

- `docs/specs/semantic/datasource-layer.md`;
- relevant semantic authoring/loading contracts;
- `docs/specs/analysis/python-analysis-design.md`;
- affected files under `docs/api/`;
- affected examples under `site/src/content/docs/docs/latest/` and
  `site/src/content/docs/zh-cn/docs/latest/`.

Inspect packaged skill references. No candidate class names were found there in
the planning pass. Editing `AGENTS.md` or packaged skills still requires their
explicit authorization; report any newly discovered necessary alignment rather
than silently claiming it complete. Historical version pages and old release-note
Help references are a separate scope.

## Completion record

Record the frozen source revision and dirty-state scope, before/after root class
counts, supported constructor paths, retained Help type contracts, any exported
annotation types retained by decision, focused and broad check results, original
failures, and two-round review disposition. Report root namespace reduction and
Help reading changes separately. Do not call fewer root exports a reduction in
the total number of typed result variants or measured agent effort.

This document records the design and acceptance requirements. Actual source,
check and review evidence is maintained in the implementation progress record.
