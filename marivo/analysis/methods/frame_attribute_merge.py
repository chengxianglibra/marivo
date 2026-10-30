"""Outer union of complete report coordinates, preserving each target's NA."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.frame_attribute_merge import AttributeMergeProgram
from marivo.analysis.core.frozen_calendar import frozen_key_rows, frozen_key_table
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.graph_exchange import StageOutput


def merge_attribute_data(
    program: AttributeMergeProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...]]:
    keys = tuple(
        frozen_key_rows(
            item.table, tuple(column for axis in schema.axes for column in axis.columns)
        )
        for schema, item in zip(program.input_schemas, inputs, strict=True)
    )
    if any(
        not item.unique_keys.proves(schema.key_fields) and len(set(group)) != len(group)
        for schema, item, group in zip(program.input_schemas, inputs, keys, strict=True)
    ):
        reject(
            "one row per complete report key",
            "duplicate target report keys",
            "Preserve each complete parent and fine key once.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    domain = tuple(dict.fromkeys(key for group in keys for key in group))
    columns = tuple(column for axis in program.output_schema.axes for column in axis.columns)
    result = frozen_key_table(columns, domain)
    value_offset = presence_offset = 0
    for schema, item, group in zip(program.input_schemas, inputs, keys, strict=True):
        positions = {key: i for i, key in enumerate(group)}
        indices = pa.array([positions.get(key) for key in domain], type=pa.int64())
        for column in schema.values:
            target = program.output_schema.values[value_offset]
            values = pc.take(pa.array(item.table[column.slot_id], from_pandas=True), indices)
            result[target.slot_id] = pd.Series(values, dtype=pd.ArrowDtype(values.type))
            value_offset += 1
        for column in schema.presence:
            target = program.output_schema.presence[presence_offset]
            values = pc.fill_null(
                pc.take(pa.array(item.table[column.slot_id], from_pandas=True), indices), False
            )
            result[target.slot_id] = pd.Series(values, dtype=pd.ArrowDtype(values.type))
            presence_offset += 1
    attachments: dict[str, OutputAttachment] = {}
    for item in inputs:
        for attachment in item.frame_attachments:
            previous = attachments.get(attachment.name)
            if previous is not None and (
                previous.schema != attachment.schema or not previous.rows.equals(attachment.rows)
            ):
                reject(
                    "consistent frozen coordinate attachments across target reports",
                    attachment.name,
                    "Retain the original complete parent correspondence for every target.",
                    "frame.attribute",
                    canonical_id="analysis_frame.attribute",
                )
            attachments.setdefault(attachment.name, attachment)
    return result.loc[:, [column.slot_id for column in program.output_schema.columns]], tuple(
        attachments.values()
    )
