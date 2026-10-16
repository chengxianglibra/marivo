"""Pure output layout conversion; pandas exports carry data descriptions only."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from marivo.analysis.core.artifact_data import ArtifactManifest, CalendarDataAxis, DataColumn
from marivo.analysis.materialization.graph_reading import (
    ExportRead,
    OutputRead,
    PreviewRead,
    ReadLayout,
)
from marivo.analysis.materialization.terminal_protocol import invalid


def column_metadata(column: DataColumn) -> dict[str, object]:
    return {
        "name": column.name,
        "dtype": asdict(column.dtype),
        "nullable": column.nullable,
        "unit": column.unit,
    }


def export_frame(
    manifest: ArtifactManifest, reader: OutputRead, request: PreviewRead | ExportRead
) -> pd.DataFrame:
    data = manifest.data
    raw = reader.read(ReadLayout(data.schema, data.rows), request)
    series: list[pd.Series] = []
    labels: list[tuple[str, str | tuple[str, ...]]] = []
    axes: list[dict[str, object]] = []
    for axis in data.schema.axes:
        description: dict[str, object] = {
            "column": axis.token,
            "kind": "calendar" if isinstance(axis, CalendarDataAxis) else "field",
            "label": axis.columns[0].name,
            "dtype": asdict(axis.columns[0].dtype) if len(axis.columns) == 1 else "tuple",
            "key_components": tuple(column_metadata(column) for column in axis.columns),
            "projections": tuple(
                {"columns": projection.columns}
                for projection in data.coordinate_projections
                if projection.axis_token == axis.token
            ),
        }
        if isinstance(axis, CalendarDataAxis):
            attachment = next(
                (part for part in data.attachments if part.name == "calendar_bounds"), None
            )
            if attachment is None or len(axis.columns) != 1:
                raise invalid("calendar output is missing its saved boundary attachment")
            key = axis.columns[0].slot_id
            indexes = raw[key]
            bound_request = (
                PreviewRead(int(indexes.max()) + 1 if len(indexes) else 0)
                if isinstance(request, PreviewRead)
                else ExportRead()
            )
            bounds = reader.read(ReadLayout(attachment.schema, attachment.rows), bound_request)
            if len(bounds) and bounds[attachment.schema.key_fields[0]].tolist() != list(
                range(len(bounds))
            ):
                raise invalid("calendar boundary output has invalid bucket positions")
            if indexes.isna().any() or any(index < 0 or index >= len(bounds) for index in indexes):
                raise invalid("calendar coordinate does not reference a saved boundary")
            starts = bounds["nominal_start"].iloc[pd.Index(indexes)].reset_index(drop=True)
            series.append(starts)
            description["dtype"] = {"unit": "us", "timezone": "UTC"}
            description["key_components"] = (
                {**column_metadata(axis.columns[0]), "dtype": {"unit": "us", "timezone": "UTC"}},
            )
            description["timezone"] = axis.timezone
            description["bounds"] = tuple(bounds.to_dict(orient="records"))
        elif len(axis.columns) == 1:
            series.append(raw[axis.columns[0].slot_id])
        else:
            series.append(
                pd.Series(
                    [
                        tuple(None if pd.isna(value) else value for value in row)
                        for row in raw.loc[
                            :, [column.slot_id for column in axis.columns]
                        ].itertuples(index=False, name=None)
                    ],
                    dtype=object,
                )
            )
        axes.append(description)
        labels.append(("coordinate", axis.token))
    for column in data.schema.values:
        series.append(raw[column.slot_id])
        labels.append(("value", column.name))
    for column in sorted(data.schema.presence, key=lambda column: column.name):
        series.append(raw[column.slot_id])
        labels.append(("presence", (column.name,)))
    result = pd.concat(series, axis=1) if series else pd.DataFrame(index=pd.RangeIndex(len(raw)))
    result.columns = pd.MultiIndex.from_tuples(labels, names=("kind", "name"))
    result.index = pd.RangeIndex(len(result))
    result.attrs["marivo"] = {
        "layout_version": 1,
        "session_id": manifest.session_id,
        "artifact_id": manifest.artifact_id,
        "axes": tuple(axes),
        "quantities": tuple(
            {"kind": "value", **column_metadata(column)} for column in data.schema.values
        ),
    }
    return result
