"""One-way fixed Frame evidence for independent recovery and refusal tests."""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from numbers import Integral, Real
from typing import NoReturn

import numpy as np
import pandas as pd
import pyarrow as pa

import marivo.analysis as mv
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.core.frame_metadata import FrameResultMetadata
from tests.support.json import Json


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Fixed Frame accessed a live source or Semantic")


def _scalar(value: object) -> Json:
    if isinstance(value, pa.Scalar):
        if (
            pa.types.is_timestamp(value.type)
            or pa.types.is_duration(value.type)
            or pa.types.is_date32(value.type)
        ):
            result: dict[str, Json] = {"arrow_type": str(value.type), "valid": value.is_valid}
            if value.is_valid:
                integer = pa.int32() if pa.types.is_date32(value.type) else pa.int64()
                result["ticks"] = int(value.cast(integer).as_py())
            return result
        return _scalar(value.as_py())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, pd.Timedelta):
        return {"duration": value.isoformat()}
    if isinstance(value, timedelta):
        return {"days": value.days, "seconds": value.seconds, "microseconds": value.microseconds}
    if isinstance(value, (date, datetime)):
        return {"temporal": value.isoformat()}
    if isinstance(value, (tuple, list)):
        return [_scalar(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Frame evidence requires string metadata keys")
        return {key: _scalar(item) for key, item in value.items()}
    if isinstance(value, pa.Table):
        return _table(value.to_pandas(types_mapper=pd.ArrowDtype))
    if isinstance(value, pd.DataFrame):
        return _table(value)
    raise TypeError(f"Unexpected Frame evidence scalar: {type(value).__name__}")


def _table(rows: pd.DataFrame) -> dict[str, Json]:
    # Inspect Arrow validity before pandas scalar extraction: a valid temporal
    # int64 minimum otherwise looks like NaT. Composite keys retain pa.Scalar.
    columns = [
        [_scalar(value) for value in pa.array(rows.iloc[:, index], from_pandas=True)]
        if isinstance(rows.iloc[:, index].dtype, pd.ArrowDtype)
        else [_scalar(value) for value in rows.iloc[:, index].array]
        for index in range(len(rows.columns))
    ]
    return {
        "columns": [_scalar(column) for column in rows.columns],
        "dtypes": [str(dtype) for dtype in rows.dtypes],
        "rows": [[column[index] for column in columns] for index in range(len(rows))],
    }


def _result_facts(metadata: FrameResultMetadata) -> dict[str, Json]:
    evaluation = metadata.evaluation
    return {
        "result_id": metadata.result_id,
        "evaluated_at": _scalar(metadata.evaluated_at),
        "source_reads": [_scalar(asdict(read)) for read in metadata.source_reads],
        "evaluation": {
            "completed_at": _scalar(evaluation.completed_at),
            "inputs": [
                {"kind": type(value).__name__, "facts": _scalar(asdict(value))}
                for value in evaluation.inputs
            ],
            "operations": list(evaluation.operations),
        },
    }


def snapshot(result: mv.AnalysisFrame, *, frame: pd.DataFrame | None = None) -> dict[str, Json]:
    backing = result._backing
    if not isinstance(backing, (MemoryFrameBacking, StoredFrameBacking)):
        raise AssertionError("Snapshot requires a computed or stored Frame, not an expression")
    rows = result.to_pandas() if frame is None else frame
    return {
        **_table(rows),
        "schema": _scalar(asdict(backing.data.schema)),
        "metadata": _result_facts(
            result._local.read_complete(backing).metadata
            if isinstance(backing, StoredFrameBacking)
            else backing.metadata
        ),
        "attrs": _scalar(rows.attrs),
    }
