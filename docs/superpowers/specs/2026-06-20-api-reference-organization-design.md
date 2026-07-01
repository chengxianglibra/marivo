# API Reference Organization Design

Date: 2026-06-20

Status: approved scope, pending written-spec review

> Historical note: this spec predates removal of the public semantic
> `prepare_*` authoring stage. Current agents must use
> `help -> discover -> settle/grill -> author -> verify`; remaining
> `prepare_*` text below is historical context only.

Supersedes: Component 1 (`docs/api/*.rst` structure) of
`2026-06-19-sphinx-python-api-docs-design.md`. All other parts of that spec —
build wiring, theme, dependencies, output location, navigation — are unchanged.

## Problem

The first cut of the Sphinx reference renders each public module as a single
flat `.. automodule:: marivo.<module> :members:` dump in source order. For the
sizes involved — `marivo.datasource` (~46 symbols), `marivo.semantic` (~120),
`marivo.analysis` (~60) — this produces three very long pages with no internal
structure: functions, result types, refs, policies, and errors are interleaved
with no grouping, and there is no per-symbol page to link to or browse.

We want the reference organized like the Polars Python API reference
(https://docs.pola.rs/api/python/stable/reference/index.html): a grouped
landing index, thematic sub-sections within each module page, and one
browsable page per symbol.

## Goals

- Group each module's `__all__` symbols into named thematic sections on the
  module page, each rendered as an `autosummary` table.
- Generate one stub page per public symbol (`autosummary` `:toctree:`), so each
  symbol is individually browsable and linkable.
- Keep the three module pages (`datasource.rst`, `semantic.rst`,
  `analysis.rst`) — no per-group file split — and keep the landing `index.rst`,
  enhanced with a one-line description per module.
- Document the complete public surface: every `__all__` symbol appears in
  exactly one group, including submodules and type aliases.

## Non-Goals

- No change to the file count beyond `autosummary`-generated stub pages. The
  tracked sources remain `index.rst` + three module `.rst` files (decision:
  "3 module pages, grouped inside").
- No change to build wiring, theme, dependencies, output path, or site
  navigation from the 2026-06-19 spec.
- No inline signatures in the group tables (decision: Polars default,
  `:nosignatures:`); full signatures and docstrings live on each stub page.
- No new prose/conceptual content; this is purely structural.

## Approach

Each module `.rst` replaces its single `automodule :members:` directive with a
series of section headings, each followed by:

```rst
.. autosummary::
   :toctree: api/
   :nosignatures:

   symbol_a
   symbol_b
```

`autosummary` with `:toctree: api/` generates a stub page per symbol under
`docs/api/api/` (gitignored alongside the rest of the build output; the
`:toctree:` target is created at build time, not tracked). Default
`autosummary` stub templates are sufficient — classes get an attributes/methods
page, functions get a signature + docstring page — so no custom templates are
added. `conf.py` already sets `autosummary_generate = True`; no config change is
required, though `add_module_names = False` may be set to keep table entries
unqualified for readability.

Symbols are referenced relative to each module via a `.. currentmodule::
marivo.<module>` at the top of the page so the short names in the tables resolve
and the stub pages document `marivo.<module>.<symbol>`. The module-landing
`.. automodule::` carries `:no-members:` so it renders only the module docstring
and does not re-document members (the default `autodoc_default_options` sets
`members: True`, which would otherwise duplicate every autosummary stub).

A custom autosummary class template (`docs/api/_templates/autosummary/class.rst`,
wired via `templates_path` in `conf.py`) renders each class with
`.. autoclass:: :members: :show-inheritance:` and omits the `__init__` rubric
the stock template emits. The stock template's bare `.. automethod:: __init__`
fails to resolve for classes whose `__init__` is not autodoc-addressable and
breaks the `-W` build; dropping it also yields cleaner, Polars-like class pages.

Submodules re-exported through `__all__` (`semantic.errors`, `semantic.typing`,
`analysis.errors`, `analysis.evidence`, `analysis.frames`, `analysis.publish`,
`analysis.session`) are **named only** — listed in a per-page "Submodules"
`list-table` with a one-line description each, without per-symbol stub pages.
These subpackages carry substantial unique API (e.g. `analysis.errors` has ~79
error classes), but per the agreed scope the reference does not expand into
them; recursing with `:toctree:` would also re-document the Frame/AiContext
classes already shown at the top level. Union/handoff type aliases that appear
in `__all__` are collected in a "Type aliases" section at the foot of the page
so the primary groups stay focused while the documented surface stays complete.

`analysis.rst` keeps its existing exclusion of `SemanticRef` and
`SemanticObject` (re-exports documented under `marivo.semantic`); they are
simply omitted from the analysis groups rather than listed.

Six undocumented public value objects (`DateParse`, `DatetimeParse`,
`TimestampParse`, `StrptimeParse`, `HourPrefixParse` in `marivo/semantic/ir.py`;
`FormatCandidate` in `marivo/semantic/dtos.py`) gain a one-line docstring so
their autosummary table summary is meaningful instead of an auto-generated
dataclass signature. This is the only library-source change.

## Group Taxonomy

Approved buckets. Each symbol appears in exactly one group.

### `marivo.datasource`

- Registration & lifecycle: `connect`, `register`, `load`, `list`, `remove`,
  `ref`, `test`
- Source constructors: `csv`, `parquet`, `duckdb`, `postgres`, `mysql`,
  `clickhouse`, `trino`, `table`
- Inspection & preview: `preview`, `inspect_source`, `inspect_table`,
  `inspect_columns`, `probe_join_keys`
- Discovery: `help`, `help_text`, `describe`
- Source IR: `DatasourceIR`, `CsvSourceIR`, `ParquetSourceIR`, `AiContextIR`,
  `DatasourceAiContextIR`
- Catalog & refs: `DatasourceCatalog`, `DatasourceRef`, `DatasourceSummary`,
  `DatasourceDescription`, `DatasourceSourceLocation`,
  `DatasourceConnectionService`
- Metadata: `TableMetadata`, `ColumnMetadata`, `ColumnProfile`,
  `PartitionMetadata`, `ScanScope`
- Results & reports: `DatasourceTestResult`, `PreviewResult`,
  `PreviewSamplePolicy`, `ScanReport`, `ColumnInspection`, `JoinKeyProbe`,
  `JoinSide`
- Warnings: `MetadataWarning`, `PreviewWarning`

### `marivo.semantic`

- Declaration decorators: `entity`, `dimension`, `measure`, `metric`,
  `relationship`, `time_dimension`, `domain`
- Aggregation & measure helpers: `aggregate`, `linear`, `ratio`,
  `weighted_average`, `semi_additive`, `snapshot`, `validity`, `join_on`
- Time parsing: `datetime`, `timestamp`, `strptime`, `hour_prefix`
- Source builders & provenance: `csv`, `parquet`, `table`, `from_sql`
- Authoring handoff: `prepare_entity`, `prepare_dimension`, `prepare_measure`,
  `prepare_metric`, `prepare_relationship`, `prepare_time_dimension`,
  `prepare_domain`, `prepare_cross_entity_metric`, `prepare_derived_metric`
- Readiness & verification: `readiness`, `richness`, `verify_object`,
  `parity_check`, `record_decision`
- Refs & loading: `ref`, `make_ref`, `load`
- Discovery: `help`, `help_text`
- Ref types: `EntityRef`, `DimensionRef`, `MeasureRef`, `MetricRef`,
  `RelationshipRef`, `TimeDimensionRef`, `DomainRef`, `SemanticRef`
- Brief types: `EntityBrief`, `DimensionBrief`, `MeasureBrief`, `MetricBrief`,
  `RelationshipBrief`, `TimeDimensionBrief`, `DomainBrief`, `DomainBriefSummary`,
  `CrossEntityMetricBrief`, `DerivedMetricBrief`, `BriefStatus`
- Details types: `EntityDetails`, `DimensionDetails`, `MeasureDetails`,
  `MetricDetails`, `RelationshipDetails`, `TimeDimensionDetails`,
  `DomainDetails`, `DatasourceDetails`, `DerivedMetricDetails`,
  `SimpleMetricDetails`, `SemanticObjectDetails`
- Catalog & objects: `SemanticCatalog`, `SemanticObject`, `SemanticObjectList`,
  `SemanticKind`, `RegisteredMatch`, `MeasureIR`
- Sources & versioning: `TableSource`, `FileSource`, `DatasetSource`,
  `SqlProvenance`, `SnapshotVersioning`, `ValidityVersioning`,
  `EntityVersioning`, `VersioningHints`
- Time-parse specs: `DateParse`, `DatetimeParse`, `TimestampParse`,
  `StrptimeParse`, `HourPrefixParse`, `FormatCandidate`
- Readiness & assessment: `ReadinessReport`, `ReadinessIssue`,
  `ReadinessInputSummary`, `RichnessReport`, `AuthoringAssessment`,
  `AssessmentIssue`, `AuthoringQuestion`, `ParityResult`, `VerifyResult`,
  `DecisionRecord`
- Facts & signals: `ComponentFact`, `DimensionValueFact`, `JoinPathFact`,
  `JoinKey`, `PrimaryKeyCandidate`, `DemandSignal`
- AI context: `AiContext`, `AiContextView`
- Errors: `LadderOrderError`
- Submodules: `errors`, `typing`
- Type aliases: `SemanticKindInput`, `SemanticRefInput`

### `marivo.analysis`

`session` and `publish` are submodules, not functions, so they appear only in
the Submodules list. The standalone callable surface is small.

- References: `make_ref`
- Alignment & window helpers: `dow_aligned`, `holiday_aligned`,
  `holiday_and_dow_aligned`, `window_bucket`
- Discovery: `help`, `help_text`
- Frames: `BaseFrame`, `BaseFrameMeta`, `MetricFrame`, `ComponentFrame`,
  `DeltaFrame`, `CoverageFrame`, `AttributionFrame`, `ForecastFrame`,
  `FramePreview`, `FrameSummary`, `FrameSummaryEntry`
- Analysis results: `AssociationResult`, `HypothesisTestResult`,
  `ExplorationResult`, `QualityReport`, `CandidateSet`
- Scopes & windows: `TimeScope`, `ConfidenceScope`, `AbsoluteWindow`
- Policies: `AlignmentPolicy`, `AlignmentKind`, `CalendarPolicy`,
  `SamplingPolicy`, `PromotionPolicy`, `PromotionSemanticAnchors`
- Refs & lineage: `ArtifactRef`, `CalendarRef`, `Lineage`, `LineageStep`
- Session & jobs: `Session`, `SessionSummary`, `JobSummary`, `FollowupAction`,
  `BlockingIssue`, `CandidateObjective`, `DiscoverSensitivity`
- Slices: `SlicePredicate`, `SlicePredicateOp`
- Submodules: `errors`, `evidence`, `frames`, `session`
- Type aliases: `SliceScalar`, `SliceValue`, `TimeScopeInput`

## Index Page

`index.rst` keeps its intro and `:ref:` indices, and gains a one-line
description per module in (or beside) the `toctree`:

- `datasource` — connect to, inspect, and register data sources.
- `semantic` — declare entities, dimensions, measures, and metrics.
- `analysis` — run metric-centered analysis over the semantic layer.

## Constraints

- `autosummary` writes per-symbol stub `.rst` files into the source tree at
  `docs/api/api/`. This directory is build output and is added to `.gitignore`;
  nothing new is committed beyond the edited `.rst` sources, the `conf.py`
  change, and the class template.
- Every `__all__` symbol must be placed in exactly one group. A coverage check
  (below) enforces this so the surface cannot silently drift from the docs.
- Repository Python rules apply: build via `make docs-api` only.

## Success Criteria

- `make docs-api` builds with zero warnings under `-W`, including no
  "autosummary: stub file not found" or "document isn't included in any
  toctree" warnings — i.e. every grouped symbol resolves and every stub page is
  reachable.
- For each module, the union of all group symbol lists equals that module's
  `__all__` (minus the documented `analysis` exclusions). Verified by a small
  script that parses the `.rst` autosummary blocks and diffs against `__all__`.
- Each module page renders its thematic sections in order; the landing page
  shows the three modules with descriptions.

## Testing And Verification

No new Python unit tests. Verification:

1. `make docs-api` is warning-free under `-W`.
2. A throwaway coverage diff (autosummary entries vs `__all__`) reports no
   missing and no extra symbols per module.
3. Spot-check the rendered `index.html`, one module page, and one generated
   stub page.
