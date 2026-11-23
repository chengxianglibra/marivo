"""Native contracts for captured structure and bounded fixed-data reads."""

from inspect import cleandoc, signature

from marivo._kernel.render import RenderableResult
from marivo.analysis.dsl._dataframe_disclosure import provider as result_provider
from marivo.analysis.dsl._read_disclosure_types import READ_TYPES
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.carriers.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FramePage,
    FramePairedPeriod,
    FramePeriodPage,
    FrameReadStats,
)
from marivo.analysis.model import time_facts
from marivo.analysis.model.disclosure import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.model.disclosure import ParameterInput as P
from marivo.analysis.model.frame_schema import (
    FrameAxisSchema,
    FrameField,
    FrameSchema,
)
from marivo.analysis.model.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
)
from marivo.analysis.model.layout_facts import (
    FrameAlignment,
    FrameAlignmentAxis,
    FrameAlignmentSide,
    FrameAxisGroup,
    FrameCalendarComparisonBinding,
    FrameChangeTarget,
    FrameComparison,
    FrameComparisonGroup,
    FrameComparisonPresence,
    FrameComparisonValue,
    FrameDecomposition,
    FrameDecompositionField,
    FrameDerivedValueInput,
    FrameFixedGroup,
    FrameIntervalComparisonBinding,
    FrameLevelTarget,
    FrameNamedValueInput,
    FrameOrdinaryComparisonBinding,
    FramePairStatisticContext,
    FrameShareContext,
    FrameStatisticContext,
)

_EXPORTED_TYPES = {
    time_facts.FrameInstantSourceTime,
    time_facts.FrameWallClockSourceTime,
    time_facts.FrameCivilDateSourceTime,
    FrameSourceRead,
    FrameSourceReadScope,
    FrameSourceReadPeriod,
    FrameSchema,
    FrameField,
    FrameAxisSchema,
    time_facts.FrameOrdinaryAxisSchema,
    time_facts.FrameRowAttributeTime,
    time_facts.FrameCalendarAxisSchema,
    time_facts.FrameIntervalAxisSchema,
    time_facts.FramePairedAxisSchema,
    time_facts.FrameBuiltinGrain,
    time_facts.FrameCalendarEndpoint,
    time_facts.FrameIntervalEndpoint,
    time_facts.FrameFixedAxis,
    time_facts.FrameAxisTime,
    time_facts.FrameComparedTime,
    time_facts.FrameFixedTime,
    time_facts.FrameAllHistoryWindow,
    time_facts.FrameSinceWindow,
    time_facts.FrameToDateWindow,
    time_facts.FrameTrailingDurationWindow,
    time_facts.FrameTrailingPeriodsWindow,
    time_facts.FrameObservationContext,
    time_facts.FrameReportWindowEndpoint,
    time_facts.FrameContributionSampleWindowEndpoint,
    time_facts.FrameFieldSampleWindowEndpoint,
    time_facts.FrameClockedContribution,
    time_facts.FrameIndependentContribution,
    time_facts.FrameObservedTimeSemantics,
    time_facts.FrameDerivedTimeSemantics,
    FrameAxisGroup,
    FrameFixedGroup,
    FrameStatisticContext,
    FrameNamedValueInput,
    FrameDerivedValueInput,
    FrameShareContext,
    FramePairStatisticContext,
    FrameAlignment,
    FrameAlignmentAxis,
    FrameAlignmentSide,
    FrameComparisonValue,
    FrameComparisonGroup,
    FrameComparisonPresence,
    FrameOrdinaryComparisonBinding,
    FrameCalendarComparisonBinding,
    FrameIntervalComparisonBinding,
    FrameComparison,
    FrameLevelTarget,
    FrameChangeTarget,
    FrameDecompositionField,
    FrameDecomposition,
    FramePage,
    FrameReadStats,
    FramePeriodPage,
    FrameCalendarPeriod,
    FrameIntervalPeriod,
    FramePairedPeriod,
    FrameComparisonPeriod,
}


def _page_operations(descriptors: list[Descriptor]) -> None:
    descriptors.append(
        operation(
            "analysis_frame.read_page",
            "frame.read_page",
            AnalysisFrame.read_page,
            bindings=(bind(AnalysisFrame.read_page, AnalysisFrame),),
            summary="Read a projected ordered page of fixed data with complete coordinate keys.",
            parameters=(
                P(
                    "columns",
                    "Tuple of complete value/presence names; None selects all, () coordinates only.",
                    ("analysis_frame.schema",),
                ),
                P("limit", "Integer 1..10000 excluding bool; default 1000."),
                P(
                    "cursor",
                    "Previous page cursor; repeat the same limit, columns, axis aliases and time_details.",
                ),
                P(
                    "axis_names",
                    "Optional full axis name or token to export name mapping; defaults to final path components.",
                    ("analysis_frame.schema",),
                ),
                P(
                    "time_details",
                    "Include calendar labels; calendar exports one actual key, interval exports .start/.end; default False.",
                ),
            ),
            output="FramePage",
            constraints=(
                "Requires complete memory or stored data; compute/persist first. No execution, automatic persistence, Catalog or source reads.",
                "Calendar/interval coordinates export actual .start/.end; ordinary TimeFields export one value. axis_names renames prefixes only.",
                "Complete keys and coordinates precede requested fields. Selection preserves row order and total_count. No attrs on page exports.",
                "Stored reads check consumed file receipts, selected fields and required coordinates. They do not certify unread semantic data or business completeness.",
                "Cursors bind exact input, collection version, limit, projection, aliases and time_details. Stored reopen in the same Runtime version may continue; memory cursors belong to the same process/input.",
                "Statistics count inspected data/coordinate records and receipt hashing bytes; they are not total I/O, latency or peak memory guarantees.",
            ),
            effects="Decode only requested data range/projection and necessary coordinates; receipt hashing and coordinate lookup may scan complete files.",
            failures=(
                "AnalysisError: use the concrete expected/received facts and repair; invalid requests fail before payload reads.",
            ),
            example=ExampleInput(
                "fixed = frame.compute()\nresult = fixed.read_page(limit=1000)",
                ("frame",),
                "result",
                "FramePage",
            ),
            discovery_group="results.inspect",
        )
    )
    descriptors.append(
        operation(
            "analysis_frame.read_periods",
            "frame.read_periods",
            AnalysisFrame.read_periods,
            bindings=(bind(AnalysisFrame.read_periods, AnalysisFrame),),
            summary="Read a bounded page of saved calendar or interval periods and their consumed endpoint mappings.",
            parameters=(
                P(
                    "target",
                    "A time FrameAxis or a current time FrameComparison from this exact Frame.",
                    ("analysis_frame.axes", "frame_schema.comparisons"),
                ),
                P("limit", "Integer 1..10000 excluding bool; default 1000."),
                P(
                    "cursor",
                    "Previous period cursor; repeat the same target and limit, or use None to start.",
                ),
            ),
            output="FramePeriodPage",
            constraints=(
                "Requires complete memory or stored data; compute/persist first. No execution, automatic persistence, Catalog or source reads.",
                "Accepts current calendar/interval/paired axes and current calendar/interval comparisons; ordinary axes and comparisons reject.",
                "Returns saved period definitions and consumed correspondences, not the current row domain. Join complete keys to read_page coordinates.",
                "Named labels retain their scalar types; interval records do not infer unsaved civil bounds or current timezone rules.",
                "A paired endpoint can be absent without a saved reason; time comparison absence uses its saved reason, independently of row presence.",
                "Cursors bind input, target, result version and limit. Stored reopen in the same Runtime version may continue.",
                "Stats report decoded coordinate records and receipt-hash bytes, not total I/O or latency; receipt validation and endpoint lookup may scan files.",
            ),
            effects="Decode one selected period/map page and its endpoint records; page objects retain no reader or whole attachment.",
            failures=(
                "AnalysisError: follow expected/received facts and choose a time target from this Frame. Invalid requests fail before payload reads.",
            ),
            example=ExampleInput(
                "fixed = time_frame.compute()\naxis = next(axis for axis in fixed.axes if axis.role in ('calendar', 'interval', 'paired'))\nresult = fixed.read_periods(axis, limit=100)",
                ("time_frame",),
                "result",
                "FramePeriodPage",
            ),
            related=("analysis_frame.schema", "analysis_frame.read_page"),
            discovery_group="results.inspect",
        )
    )
    descriptors.append(
        operation(
            "frame_page.to_pandas",
            "page.to_pandas",
            FramePage.to_pandas,
            bindings=(bind(FramePage.to_pandas, FramePage),),
            summary="Copy this page's table without reading files.",
            parameters=(),
            output="pandas.DataFrame",
            constraints=(
                "Isolated page rows follow layout exactly; attrs are empty. The RangeIndex is not a business key.",
                "Large int64[pyarrow] mixed with float may raise ArrowInvalid. For approximate arithmetic: result['value'].astype('float64') / 3600.0; integers may round and pd.NA becomes NaN.",
            ),
            effects="Copy page data only.",
            failures=(),
            example=ExampleInput(
                "result = page.to_pandas()", ("page",), "result", "pandas.DataFrame"
            ),
        )
    )


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    method_owners = {
        (name, getattr(owner, name)): tuple(
            candidate
            for candidate, _, _, _ in READ_TYPES
            if getattr(candidate, name, None) is getattr(owner, name)
        )
        for owner, _, _, _ in READ_TYPES
        for name in ("show", "render")
        if getattr(owner, name, None) is not None
    }

    def method_target(owner: type[object], target: str, name: str) -> str:
        method = getattr(owner, name, None)
        if method is getattr(RenderableResult, name, None):
            return "runtime.values." + name
        if issubclass(owner, FrameAxisSchema):
            return "frame_axis_schema." + name
        return target + "." + name

    page_summaries = {
        FrameCalendarPeriod: "One saved calendar period.",
        FrameIntervalPeriod: "One saved interval.",
        FramePairedPeriod: "A saved pair and its complete owning key.",
        FramePeriodPage: "One bounded immutable period page.",
    }
    page_constraints = {
        time_facts.FrameInstantSourceTime: "Captured source instants and consumption timezone only. Fixed-data operations retain these input facts; no new field interpretation is implied. Example: frame.schema.source_times[0].",
        time_facts.FrameWallClockSourceTime: "timezone and origin identify the effective source declaration; inherited_from records an hour-prefix declaration chain. consumption_timezone records the actual consumer boundary. Example: frame.schema.source_times[0].",
        time_facts.FrameCivilDateSourceTime: "Business dates are assigned by their day start when source and consumption timezones differ. This cannot reconstruct event-day totals from business-day totals. Example: frame.schema.source_times[0].",
        FrameReadStats: "Counts include scanned records and repeated scans, not all disk I/O, elapsed time or peak memory. Example: page.read_stats.show().",
        FramePage: "Retains only this page, with no Frame, reader or complete audit. Coordinates belong to input_id; RangeIndex is not a business key. Example: fixed.read_page(limit=100).to_pandas().",
        FrameCalendarPeriod: "Contains complete actual keys, original ordinals, timezone, civil labels and saved selection progress; current timezone rules and shift history are not reconstructed. Example: page.items[0].show().",
        FrameIntervalPeriod: "Contains complete keys, UTC bounds and boundary timezone; unsaved nominal or civil boundaries are not inferred. Example: page.items[0].show().",
        FramePairedPeriod: "An endpoint may be absent without a saved reason. Example: page.items[0].left.",
        FrameComparisonPeriod: "A missing baseline carries its saved reason, independently of row presence. Example: page.items[0].baseline.",
        FramePeriodPage: "Retains this page and its endpoints only; period keys join to read_page().coordinates and selected periods need not have current rows. Example: fixed.read_periods(fixed.axes[0], limit=100).",
        FrameNamedValueInput: "name is a captured complete input name, not a reference to a current output. subject is copied only from an explicit observation subject; no subject is inferred from lineage. Example: context.x.name.",
        FrameDerivedValueInput: "Origins describe a derived expression without a formula. Equal descriptions do not certify equal expressions or samples and are not computation/cache keys. Example: context.y.origins.",
        FrameShareContext: "Represents numerator / partition_sum(numerator). groups and nonempty along capture the full normalization axes. Signed shares may be negative or exceed 100%. restricted does not recompute the denominator. Example: frame.schema.value_contexts['share'].",
        FramePairStatisticContext: "x and y retain call order and their captured input descriptions. complete does not certify coverage, nonempty valid samples or statistical significance; restricted does not recompute the statistic. Example: frame.schema.value_contexts['coefficient'].",
        FrameAlignment: "Local relation identity applies to input_id and does not reference an ancestor Artifact. Two transported values are not certified as the same business metric. Example: frame.schema.alignments[0].",
        FrameAlignmentSide: "values are current complete names; presence is a nonempty outer-to-inner chain and every flag must be true for this side to exist. A missing side differs from value NA. Example: alignment.left.presence.",
        FrameAlignmentAxis: "Resolve axis_token in this exact schema. self selects an ordinary/equal time axis; left/right select a paired endpoint. Read saved time coordinates with read_periods. Example: alignment.left.coordinates[0].",
    }
    for owner, target, producers, consumers in READ_TYPES:
        method_targets = tuple(
            method_target(owner, target, name)
            for name in ("show", "render")
            if getattr(owner, name, None) is not None
            and getattr(owner, name) is not getattr(RenderableResult, name, None)
        )
        descriptors.append(
            value_type(
                target,
                owner,
                summary=page_summaries.get(
                    owner,
                    cleandoc(owner.__doc__).splitlines()[0] if owner.__doc__ else owner.__name__,
                ),
                acquisition=f"Read {producers[0]}; returned immutable facts only. Direct construction and extension are unsupported.",
                producers=producers,
                consumers=(
                    *consumers,
                    *method_targets,
                ),
                constraints=(
                    "Read identities survive inheritance across results; scopes retain original read bindings. No source execution, Store lookup or data authority."
                    if owner in (FrameSourceRead, FrameSourceReadScope, FrameSourceReadPeriod)
                    else "Identities apply within one exact input. No source execution, Store lookup or data authority.",
                )
                + ((page_constraints[owner],) if owner in page_constraints else ()),
            )
        )
        for method_name in ("show", "render"):
            method = getattr(owner, method_name, None)
            if method is None or method is getattr(RenderableResult, method_name, None):
                continue
            method_id = (method_name, method)
            owners = method_owners[method_id]
            if owner is not owners[0]:
                continue
            parameters = tuple(
                P(name, "Positive UTF-8 display budget or None.")
                for name in signature(getattr(owner, method_name)).parameters
                if name != "self"
            )
            output = "None" if method_name == "show" else "str"
            shared_target = method_target(owner, target, method_name)
            descriptors.append(
                operation(
                    shared_target,
                    "value." + method_name,
                    method,
                    bindings=tuple(
                        bind(getattr(candidate, method_name), candidate) for candidate in owners
                    ),
                    summary="Display bounded captured read metadata.",
                    parameters=parameters,
                    output=output,
                    constraints=("No further data reads.",),
                    effects="Display captured facts.",
                    failures=(),
                    example=ExampleInput(
                        f"result = value.{method_name}()", ("value",), "result", output
                    ),
                )
            )
    _page_operations(descriptors)
    results = result_provider()
    return DisclosureProvider(
        "analysis.dsl.reads",
        tuple(descriptors) + results.descriptors,
        tuple(
            ExportInput(owner.__name__, owner, target)
            for owner, target, _, _ in READ_TYPES
            if owner in _EXPORTED_TYPES
        )
        + results.exports,
    )
