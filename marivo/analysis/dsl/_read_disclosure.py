"""Native contracts for captured structure and bounded fixed-data reads."""

from inspect import cleandoc, signature

from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import ParameterInput as P
from marivo.analysis.core.data_types import (
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_interpretation import (
    FrameAllHistoryWindow,
    FrameAxisGroup,
    FrameAxisTime,
    FrameBuiltinGrain,
    FrameCalendarAxisSchema,
    FrameCalendarComparisonBinding,
    FrameCalendarEndpoint,
    FrameChangeTarget,
    FrameComparedTime,
    FrameComparison,
    FrameComparisonGroup,
    FrameComparisonPresence,
    FrameComparisonValue,
    FrameDecomposition,
    FrameDecompositionField,
    FrameFixedAxis,
    FrameFixedGroup,
    FrameFixedTime,
    FrameIntervalAxisSchema,
    FrameIntervalComparisonBinding,
    FrameIntervalEndpoint,
    FrameLevelTarget,
    FrameNamedGrain,
    FrameObservationContext,
    FrameOrdinaryAxisSchema,
    FrameOrdinaryComparisonBinding,
    FramePairedAxisSchema,
    FrameSinceWindow,
    FrameStatisticContext,
    FrameToDateWindow,
    FrameTrailingDurationWindow,
    FrameTrailingPeriodsWindow,
)
from marivo.analysis.core.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FramePage,
    FramePairedPeriod,
    FramePeriodPage,
    FrameReadStats,
)
from marivo.analysis.core.frame_schema import (
    CoordinateExportColumn,
    FieldExportColumn,
    FrameAxisSchema,
    FrameField,
    FrameSchema,
    UnresolvedDataType,
)
from marivo.analysis.core.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.render import RenderableResult


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    types: tuple[tuple[type[object], str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            FrameSourceRead,
            "frame_source_read",
            ("analysis_frame.source_reads",),
            ("frame_source_read_scope",),
        ),
        (
            FrameSourceReadScope,
            "frame_source_read_scope",
            ("frame_source_read.scopes",),
            ("frame_source_read_period",),
        ),
        (
            FrameSourceReadPeriod,
            "frame_source_read_period",
            ("frame_source_read_scope.period",),
            (),
        ),
        (
            FrameSchema,
            "frame_schema",
            ("analysis_frame.schema", "runtime.persisted_frame_summary.schema"),
            (
                "frame_field",
                "frame_axis_schema",
                "field_export_column",
                "coordinate_export_column",
                "frame_fixed_axis",
                "frame_observation_context",
                "frame_statistic_context",
                "frame_comparison",
                "frame_decomposition",
            ),
        ),
        (
            FrameField,
            "frame_field",
            (
                "frame_schema.values",
                "frame_schema.presence",
                "frame_schema.slots",
                "frame_axis_schema.keys",
            ),
            (
                "scalar_data_type",
                "decimal_data_type",
                "timestamp_data_type",
                "duration_data_type",
                "unresolved_data_type",
            ),
        ),
        (
            FrameAxisSchema,
            "frame_axis_schema",
            ("frame_schema.axes",),
            (
                "frame_field",
                "frame_ordinary_axis_schema",
                "frame_calendar_axis_schema",
                "frame_interval_axis_schema",
                "frame_paired_axis_schema",
            ),
        ),
        (
            FrameOrdinaryAxisSchema,
            "frame_ordinary_axis_schema",
            ("frame_schema.axes",),
            ("frame_fixed_axis",),
        ),
        (
            FrameCalendarAxisSchema,
            "frame_calendar_axis_schema",
            ("frame_schema.axes",),
            ("frame_period_page", "frame_builtin_grain", "frame_named_grain"),
        ),
        (
            FrameIntervalAxisSchema,
            "frame_interval_axis_schema",
            ("frame_schema.axes",),
            ("frame_period_page",),
        ),
        (
            FramePairedAxisSchema,
            "frame_paired_axis_schema",
            ("frame_schema.axes",),
            ("frame_period_page",),
        ),
        (
            FrameBuiltinGrain,
            "frame_builtin_grain",
            (
                "frame_calendar_axis_schema.grain",
                "frame_calendar_endpoint.grain",
                "frame_to_date_window.grain",
            ),
            (),
        ),
        (
            FrameNamedGrain,
            "frame_named_grain",
            (
                "frame_calendar_axis_schema.grain",
                "frame_calendar_endpoint.grain",
                "frame_to_date_window.grain",
            ),
            ("semantic_object_identity",),
        ),
        (
            FrameCalendarEndpoint,
            "frame_calendar_endpoint",
            (
                "frame_paired_axis_schema.left",
                "frame_paired_axis_schema.right",
                "frame_calendar_comparison_binding.baseline",
            ),
            ("frame_period_page", "frame_builtin_grain", "frame_named_grain"),
        ),
        (
            FrameIntervalEndpoint,
            "frame_interval_endpoint",
            (
                "frame_paired_axis_schema.left",
                "frame_paired_axis_schema.right",
                "frame_interval_comparison_binding.baseline",
            ),
            ("frame_period_page",),
        ),
        (FrameFixedAxis, "frame_fixed_axis", ("frame_schema.fixed_axes",), ("frame_fixed_group",)),
        (FrameAxisTime, "frame_axis_time", ("frame_observation_context.time",), ()),
        (
            FrameComparedTime,
            "frame_compared_time",
            ("frame_observation_context.time",),
            ("frame_comparison",),
        ),
        (FrameFixedTime, "frame_fixed_time", ("frame_observation_context.time",), ()),
        (
            FrameAllHistoryWindow,
            "frame_all_history_window",
            ("frame_observation_context.window",),
            (),
        ),
        (FrameSinceWindow, "frame_since_window", ("frame_observation_context.window",), ()),
        (
            FrameToDateWindow,
            "frame_to_date_window",
            ("frame_observation_context.window",),
            ("frame_builtin_grain", "frame_named_grain"),
        ),
        (
            FrameTrailingDurationWindow,
            "frame_trailing_duration_window",
            ("frame_observation_context.window",),
            (),
        ),
        (
            FrameTrailingPeriodsWindow,
            "frame_trailing_periods_window",
            ("frame_observation_context.window",),
            (),
        ),
        (
            FrameObservationContext,
            "frame_observation_context",
            ("frame_schema.value_contexts",),
            (
                "frame_axis_time",
                "frame_compared_time",
                "frame_fixed_time",
                "frame_all_history_window",
                "frame_since_window",
                "frame_to_date_window",
                "frame_trailing_duration_window",
                "frame_trailing_periods_window",
                "frame_comparison_group",
                "frame_level_target",
            ),
        ),
        (
            FrameAxisGroup,
            "frame_axis_group",
            ("frame_statistic_context.groups", "frame_decomposition.groups"),
            ("frame_schema.axes",),
        ),
        (
            FrameFixedGroup,
            "frame_fixed_group",
            ("frame_statistic_context.groups", "frame_decomposition.groups"),
            ("frame_fixed_axis",),
        ),
        (
            FrameStatisticContext,
            "frame_statistic_context",
            ("frame_schema.value_contexts",),
            (
                "frame_axis_group",
                "frame_fixed_group",
                "frame_comparison_group",
                "frame_level_target",
            ),
        ),
        (
            FrameComparisonValue,
            "frame_comparison_value",
            ("frame_comparison_group.values",),
            ("frame_schema.values",),
        ),
        (
            FrameComparisonGroup,
            "frame_comparison_group",
            ("frame_comparison.groups",),
            ("frame_comparison_value",),
        ),
        (
            FrameComparisonPresence,
            "frame_comparison_presence",
            ("frame_comparison.presence",),
            ("frame_schema.presence",),
        ),
        (
            FrameOrdinaryComparisonBinding,
            "frame_ordinary_comparison_binding",
            ("frame_comparison.binding",),
            (),
        ),
        (
            FrameCalendarComparisonBinding,
            "frame_calendar_comparison_binding",
            ("frame_comparison.binding",),
            ("frame_calendar_endpoint",),
        ),
        (
            FrameIntervalComparisonBinding,
            "frame_interval_comparison_binding",
            ("frame_comparison.binding",),
            ("frame_interval_endpoint",),
        ),
        (
            FrameComparison,
            "frame_comparison",
            ("frame_schema.comparisons",),
            ("analysis_frame.read_periods", "frame_period_page.target"),
        ),
        (
            FrameLevelTarget,
            "frame_level_target",
            ("frame_decomposition.target",),
            ("frame_observation_context", "frame_statistic_context"),
        ),
        (
            FrameChangeTarget,
            "frame_change_target",
            ("frame_decomposition.target",),
            ("frame_comparison",),
        ),
        (
            FrameDecompositionField,
            "frame_decomposition_field",
            ("frame_decomposition.outputs",),
            ("frame_schema.values", "frame_schema.presence"),
        ),
        (
            FrameDecomposition,
            "frame_decomposition",
            ("frame_schema.decompositions",),
            (
                "frame_decomposition_field",
                "frame_level_target",
                "frame_change_target",
                "frame_axis_group",
                "frame_fixed_group",
            ),
        ),
        (
            FieldExportColumn,
            "field_export_column",
            ("frame_schema.layout", "frame_page.layout"),
            ("frame_schema.slots",),
        ),
        (
            CoordinateExportColumn,
            "coordinate_export_column",
            ("frame_schema.layout", "frame_page.layout"),
            ("frame_schema.axes",),
        ),
        (UnresolvedDataType, "unresolved_data_type", ("frame_field.dtype",), ()),
        (ScalarDataType, "scalar_data_type", ("frame_field.dtype",), ()),
        (DecimalDataType, "decimal_data_type", ("frame_field.dtype",), ()),
        (TimestampDataType, "timestamp_data_type", ("frame_field.dtype",), ()),
        (DurationDataType, "duration_data_type", ("frame_field.dtype",), ()),
        (
            FramePage,
            "frame_page",
            ("analysis_frame.read_page",),
            (
                "frame_page.to_pandas",
                "frame_read_stats",
                "field_export_column",
                "coordinate_export_column",
            ),
        ),
        (FrameReadStats, "frame_read_stats", ("frame_page.read_stats",), ()),
        (
            FramePeriodPage,
            "frame_period_page",
            ("analysis_frame.read_periods",),
            (
                "frame_calendar_period",
                "frame_interval_period",
                "frame_paired_period",
                "frame_comparison_period",
                "frame_read_stats",
            ),
        ),
        (
            FrameCalendarPeriod,
            "frame_calendar_period",
            ("frame_period_page.items",),
            ("frame_period_page",),
        ),
        (
            FrameIntervalPeriod,
            "frame_interval_period",
            ("frame_period_page.items",),
            ("frame_period_page",),
        ),
        (
            FramePairedPeriod,
            "frame_paired_period",
            ("frame_period_page.items",),
            ("frame_calendar_period", "frame_interval_period"),
        ),
        (
            FrameComparisonPeriod,
            "frame_comparison_period",
            ("frame_period_page.items",),
            ("frame_calendar_period", "frame_interval_period"),
        ),
    )
    method_owners = {
        (name, getattr(owner, name)): tuple(
            candidate
            for candidate, _, _, _ in types
            if getattr(candidate, name, None) is getattr(owner, name)
        )
        for owner, _, _, _ in types
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
        FrameReadStats: "Counts include scanned records and repeated scans, not all disk I/O, elapsed time or peak memory. Example: page.read_stats.show().",
        FramePage: "Retains only this page, with no Frame, reader or complete audit. Coordinates belong to input_id; RangeIndex is not a business key. Example: fixed.read_page(limit=100).to_pandas().",
        FrameCalendarPeriod: "Contains complete keys, actual and nominal UTC bounds, original ordinals, timezone, labels and saved civil boundaries; current timezone rules and shift history are not reconstructed. Example: page.items[0].show().",
        FrameIntervalPeriod: "Contains complete keys, UTC bounds and boundary timezone; unsaved nominal or civil boundaries are not inferred. Example: page.items[0].show().",
        FramePairedPeriod: "An endpoint may be absent without a saved reason. Example: page.items[0].left.",
        FrameComparisonPeriod: "A missing baseline carries its saved reason, independently of row presence. Example: page.items[0].baseline.",
        FramePeriodPage: "Retains this page and its endpoints only; period keys join to read_page().coordinates and selected periods need not have current rows. Example: fixed.read_periods(fixed.axes[0], limit=100).",
    }
    for owner, target, producers, consumers in types:
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
                    "Include calendar labels and nominal boundaries; .nominal_start is the original bucket start, .start may be clipped; default False.",
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
    exported = {
        FrameSourceRead,
        FrameSourceReadScope,
        FrameSourceReadPeriod,
        FrameSchema,
        FrameField,
        FrameAxisSchema,
        FrameOrdinaryAxisSchema,
        FrameCalendarAxisSchema,
        FrameIntervalAxisSchema,
        FramePairedAxisSchema,
        FrameBuiltinGrain,
        FrameNamedGrain,
        FrameCalendarEndpoint,
        FrameIntervalEndpoint,
        FrameFixedAxis,
        FrameAxisTime,
        FrameComparedTime,
        FrameFixedTime,
        FrameAllHistoryWindow,
        FrameSinceWindow,
        FrameToDateWindow,
        FrameTrailingDurationWindow,
        FrameTrailingPeriodsWindow,
        FrameObservationContext,
        FrameAxisGroup,
        FrameFixedGroup,
        FrameStatisticContext,
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
    return DisclosureProvider(
        "analysis.dsl.reads",
        tuple(descriptors),
        tuple(
            ExportInput(owner.__name__, owner, target)
            for owner, target, _, _ in types
            if owner in exported
        ),
    )
