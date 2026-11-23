"""Display and export option handling for the current Frame."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pandas as pd

from marivo._definition.query import ResultQuery
from marivo._definition.relations import ReadQuery
from marivo._kernel.render import Card
from marivo.analysis._scope import scope_summary
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    FrameDerivation,
    FrameNode,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.frame_disclosure import (
    _input_kind,
    _operation_summary,
    _pending_source_inputs,
)
from marivo.analysis.materialization.carriers.frame_page import FramePage
from marivo.analysis.materialization.frame_paging import read_frame_page
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_metadata import MemoryInput, SourceInput, StoredInput
from marivo.analysis.model.frame_schema import column_type_summary
from marivo.analysis.model.frame_structure import PendingDataSchema
from marivo.analysis.model.time_facts import source_time_summary
from marivo.analysis.model.value_time import time_summary


def export_axis_names(
    axes: tuple[FrameAxis, ...], input_id: str, axis_names: object, member: str
) -> tuple[str, ...]:
    if axis_names is not None and not isinstance(axis_names, Mapping):
        reject(
            "a mapping from axis names or tokens to export names",
            type(axis_names).__name__,
            "Pass axis_names={'full.axis.name': 'name'}.",
            f"frame.{member}",
            canonical_id=f"analysis_frame.{member}",
        )
    names = [axis.name.rsplit(".", 1)[-1] for axis in axes]
    assigned: set[int] = set()
    for selector, name in (axis_names or {}).items():
        matches = [
            i
            for i, axis in enumerate(axes)
            if isinstance(selector, str) and selector in (axis.name, axis.token)
        ]
        if len(matches) != 1 or matches[0] in assigned:
            guidance = (
                f"Choose a full name or token from {tuple((a.name, a.token) for a in axes)!r}."
            )
            if isinstance(selector, FrameAxis):
                if selector._input_id == input_id:
                    guidance = f"Pass axis_names={{{selector.token!r}: 'bucket'}}; use axis.token as the key."
                else:
                    guidance = (
                        "Reacquire an axis from this Frame, then use its .token string as the key."
                    )
            reject(
                "one unambiguous current axis name or token string as an axis_names key, assigned once",
                f"{type(selector).__name__}: {selector!r}",
                guidance,
                f"frame.{member}.axis_names.key",
                canonical_id=f"analysis_frame.{member}",
            )
        if type(name) is not str or not name or name != name.strip() or not name.isprintable():
            reject(
                "a nonempty printable export name without surrounding whitespace",
                repr(name),
                "Assign a distinct readable name to each renamed axis.",
                f"frame.{member}.axis_names[{selector!r}]",
                canonical_id=f"analysis_frame.{member}",
            )
        assigned.add(matches[0])
        names[matches[0]] = name
    return tuple(names)


def validate_time_details(time_details: bool, member: str) -> None:
    if type(time_details) is not bool:
        reject(
            "a boolean time_details",
            repr(time_details),
            "Pass time_details=True or False.",
            f"frame.{member}",
            canonical_id=f"analysis_frame.{member}",
        )


def _describe_schema(
    card: Card,
    schema: DataSchema | PendingDataSchema,
    declaration: FrameDerivation | None,
    axes: tuple[FrameAxis, ...],
) -> None:
    card.field("presence", repr(tuple(".".join(path) for path in schema.presence_paths)))
    value_columns = schema.values
    card.listing(
        "values (type, nullable, unit)",
        (
            f"{column.name!r}: {column_type_summary(column)}, {column.nullable!r}, {column.unit!r}"
            for column in value_columns[:3]
        ),
    )
    if len(value_columns) > 3:
        card.field(
            "value.more",
            f"{len(value_columns) - 3} omitted; .schema.values",
        )
    if schema.value_time_semantics:
        summaries = tuple(time_summary(fact) for _, fact in schema.value_time_semantics)
        card.field(
            "time",
            summaries[0]
            if len(summaries) == 1
            else repr(
                tuple(
                    (name, summary)
                    for (name, _), summary in zip(
                        schema.value_time_semantics[:6], summaries[:6], strict=True
                    )
                )
            ),
        )
        if any(
            "independent" in summary or "mixed" in summary or "multiple clocks" in summary
            for summary in summaries
        ):
            card.field("time_details", ".schema.value_time_semantics")
    if schema.source_times:
        card.field(
            "source_times",
            "\n".join(source_time_summary(fact)[:640] for fact in schema.source_times[:12]),
        )
        if len(schema.source_times) > 12:
            card.field(
                "source_times.more",
                f"{len(schema.source_times) - 12} omitted; read .schema.source_times",
            )
    if schema.alignments:
        card.field("alignments.count", str(len(schema.alignments)))
    for index, alignment in enumerate(schema.alignments[:3]):
        summary = f"{alignment.alignment_id[:80]} left={alignment.left.values[:2]!r} right={alignment.right.values[:2]!r}"
        card.field("alignments[" + str(index) + "]", summary[:320])
    if len(schema.alignments) > 3:
        card.field("alignments.more", str(len(schema.alignments) - 3) + " omitted")
    if declaration is None:
        card.field(
            "attribute",
            "rebuild an explicit source observation with saved joint keys",
        )
    elif tuple(output.path for output in declaration.outputs) != schema.value_paths:
        card.field(
            "attribute",
            "select direct Metric or comparison outputs for attribute",
        )
    else:
        card.field(
            "attribute",
            "retained declaration; .attribute(by=...) reads fresh sources",
        )
    card.field(
        "axes",
        repr(tuple((axis.token, axis.name, axis.role, axis.key_names) for axis in axes)),
    )


def _describe_pending(card: Card, frame: FrameNode, backing: ExpressionFrameBacking) -> None:
    root = backing.root
    if isinstance(root, OperationFrameBinding):
        card.field("operation", _operation_summary(root))
        card.field(
            "inputs",
            repr(tuple((child._input_id, _input_kind(child._backing)) for child in root.inputs)),
        )
    elif isinstance(root, SourceFrameBinding):
        node = root.graph.root
        operation = (
            "read"
            if isinstance(node, ReadQuery)
            else "literal"
            if isinstance(node, ResultQuery)
            else "observe"
            if root.graph.signature.quantities
            else "frame"
        )
        card.field("operation", operation).field("inputs", "source")
    else:
        card.field("operation", "literal").field("inputs", "literal")
    sources = _pending_source_inputs(frame)
    if sources:
        scopes = tuple(
            dict.fromkeys(
                scope_summary(source.graph.root, source.graph.signature.observation)
                for _, source in sources
            )
        )
        card.field(
            "scope",
            "; ".join(scopes) if scopes else "no captured filter or member restriction",
        )
        anchors = tuple(
            (
                frame._input_id,
                axis.token,
                axis.at.isoformat() if isinstance(axis.at, datetime) else repr(axis.at),
            )
            for frame, _ in sources
            for axis in frame._schema.axes
            if axis.at is not None
        )
        if anchors:
            card.field("anchors", repr(anchors))
    card.field("rows", "pending; no input rows read")


def _describe_complete(
    card: Card, backing: MemoryFrameBacking | StoredFrameBacking, local: LocalFrameServices
) -> None:
    if isinstance(backing, MemoryFrameBacking):
        total = backing.data.row_count
    else:
        selected = local.open_frame(backing.artifact_id)
        if selected != backing:
            reject(
                "the exact original committed descriptor",
                backing.artifact_id,
                "Preserve the selected Artifact metadata.",
                "frame.show",
                canonical_id="analysis_frame.show",
            )
        total = backing.data.rows.receipt.realized_row_count
    card.field("result_id", backing.metadata.result_id)
    facts = backing.method_facts
    card.field("method_facts", str(len(facts)))
    if facts:
        card.field("method_origins", repr(tuple(item.origin_result_id for item in facts)))
        card.field("method_facts_read", "use .method_facts for complete original explanation facts")
    card.field("evaluated_at", backing.metadata.evaluated_at.isoformat())
    if isinstance(backing, StoredFrameBacking):
        card.field("session_id", local.session_ref)
        card.field("artifact_id", backing.artifact_id)
        card.field(
            "description",
            repr(backing.description) if backing.description is not None else "(not provided)",
        )
        card.field("committed_at", local.committed_at(backing).isoformat())
    if isinstance(backing, StoredFrameBacking):
        stored_metadata = backing.metadata
        inputs, operations = stored_metadata.inputs, stored_metadata.operations
    else:
        memory_metadata = backing.metadata
        inputs = memory_metadata.evaluation.inputs
        operations = memory_metadata.evaluation.operations
    source_reads = backing.metadata.source_reads
    input_kinds = tuple(
        "stored"
        if isinstance(item, StoredInput)
        else "memory"
        if isinstance(item, MemoryInput)
        else "source"
        if isinstance(item, SourceInput)
        else "literal"
        for item in inputs
    )
    card.field("inputs", repr(input_kinds))
    card.field("operations", repr(operations))
    card.field("source_reads", str(len(source_reads)))
    if source_reads:
        first = source_reads[0]
        card.field("source_read", f"{first.scopes[0].source} at {first.completed_at.isoformat()}")
        if len(source_reads) > 1:
            card.field("source_reads_omitted", str(len(source_reads) - 1))
        card.field(
            "read_details",
            ".source_reads"
            if isinstance(backing, StoredFrameBacking)
            else ".persist().source_reads",
        )
    card.field("rows", str(total))
    data = backing.data if isinstance(backing, MemoryFrameBacking) else local.read_data(backing)
    table = export_frame(
        data.schema, data.copy_rows(), data.copy_attachments(), validate_names=False
    )
    card.field("data", "\n" + table.to_string(max_rows=None, max_cols=None))


def show_frame(frame: FrameNode, local: LocalFrameServices, axes: tuple[FrameAxis, ...]) -> None:
    """Display captured facts and complete data at the original read boundary."""
    backing = frame._backing
    available = (
        (".compute()", ".to_pandas()", ".persist()")
        if isinstance(backing, ExpressionFrameBacking)
        else (".read_page()", ".to_pandas()", ".persist()")
        if isinstance(backing, MemoryFrameBacking)
        else (".read_page()", ".to_pandas()", ".source_reads")
    )
    card = Card(
        identity=repr(frame),
        available=(*available, *((".schema.alignments",) if frame._schema.alignments else ())),
    )
    _describe_schema(card, frame._schema, frame._derivation, axes)
    if isinstance(backing, ExpressionFrameBacking):
        _describe_pending(card, frame, backing)
    else:
        _describe_complete(card, backing, local)
    print(card.render(max_output_bytes=None))


def export_fixed_frame(
    backing: StoredFrameBacking | MemoryFrameBacking,
    local: LocalFrameServices,
    names: tuple[str, ...],
    *,
    time_details: bool,
) -> pd.DataFrame:
    data = local.read_data(backing) if isinstance(backing, StoredFrameBacking) else backing.data
    return export_frame(
        data.schema,
        data.copy_rows(),
        data.copy_attachments(),
        axis_names=names,
        time_details=time_details,
    )


def read_fixed_page(
    frame: FrameNode,
    local: LocalFrameServices,
    axes: tuple[FrameAxis, ...],
    *,
    columns: tuple[str, ...] | None,
    limit: int,
    cursor: str | None,
    axis_names: Mapping[str, str] | None,
    time_details: bool,
) -> FramePage:
    backing = frame._backing
    if isinstance(backing, ExpressionFrameBacking):
        reject(
            "a complete memory or stored Frame",
            "expression",
            "Call compute() or persist() and read the returned Frame.",
            "frame.read_page",
            canonical_id="analysis_frame.read_page",
        )
    validate_time_details(time_details, "read_page")
    names = export_axis_names(axes, frame._input_id, axis_names, "read_page")
    assert isinstance(frame._schema, DataSchema)
    return read_frame_page(
        input_id=frame._input_id,
        schema=frame._schema,
        backing=backing,
        local=local,
        columns=columns,
        limit=limit,
        cursor=cursor,
        axis_names=names,
        time_details=time_details,
    )
