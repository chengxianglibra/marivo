"""Native contracts for captured structure and bounded fixed-data reads."""

from inspect import signature

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
from marivo.analysis.core.frame_page import FramePage, FrameReadStats
from marivo.analysis.core.frame_schema import (
    CoordinateExportColumn,
    FieldExportColumn,
    FrameAxisSchema,
    FrameField,
    FrameSchema,
    UnresolvedDataType,
)
from marivo.analysis.dsl.frame import AnalysisFrame


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = []
    types: tuple[tuple[type[object], str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            FrameSchema,
            "frame_schema",
            ("analysis_frame.schema", "runtime.persisted_frame_summary.schema"),
            ("frame_field", "frame_axis_schema", "field_export_column", "coordinate_export_column"),
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
        (FrameAxisSchema, "frame_axis_schema", ("frame_schema.axes",), ("frame_field",)),
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
    )
    for owner, target, producers, consumers in types:
        descriptors.append(
            value_type(
                target,
                owner,
                summary=owner.__doc__ or owner.__name__,
                acquisition=f"Read {producers[0]}; returned immutable facts only. Direct construction and extension are unsupported.",
                producers=producers,
                consumers=(
                    *consumers,
                    "runtime.values.show"
                    if owner in (FramePage, FrameReadStats)
                    else target + ".show",
                ),
                constraints=(
                    "Identities apply within one exact input. No source execution, Store lookup or data authority.",
                ),
            )
        )
        if owner in (FramePage, FrameReadStats):
            continue
        for method_name in ("show", "render"):
            method = getattr(owner, method_name, None)
            if method is None:
                continue
            parameters = tuple(
                P(name, "Positive UTF-8 display budget or None.")
                for name in signature(method).parameters
                if name != "self"
            )
            output = "None" if method_name == "show" else "str"
            descriptors.append(
                operation(
                    target + "." + method_name,
                    "value." + method_name,
                    method,
                    bindings=(bind(method, owner),),
                    summary="Display bounded captured read metadata.",
                    parameters=parameters,
                    output=output,
                    constraints=("No further data or audit reads.",),
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
                "Stored reads check consumed file receipts, selected fields and required coordinates. They do not certify unread semantic data or complete audit collections.",
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
            "frame_page.to_pandas",
            "page.to_pandas",
            FramePage.to_pandas,
            bindings=(bind(FramePage.to_pandas, FramePage),),
            summary="Copy this page's table without reading files.",
            parameters=(),
            output="pandas.DataFrame",
            constraints=(
                "Isolated page rows follow layout exactly; attrs are empty. The RangeIndex is not a business key.",
            ),
            effects="Copy page data only.",
            failures=(),
            example=ExampleInput(
                "result = page.to_pandas()", ("page",), "result", "pandas.DataFrame"
            ),
        )
    )
    exported = (FrameSchema, FrameField, FrameAxisSchema, FramePage, FrameReadStats)
    return DisclosureProvider(
        "analysis.dsl.reads",
        tuple(descriptors),
        tuple(
            ExportInput(owner.__name__, owner, target)
            for owner, target, _, _ in types
            if owner in exported
        ),
    )
