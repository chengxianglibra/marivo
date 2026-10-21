"""Checked target reports share a union domain without filling absent values."""

from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.data_schema import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_attribute_merge import (
    AttributeMergeProgram,
    AttributeMergeValues,
    infer_attribute_merge_schema,
)
from marivo.analysis.dsl.frame_attribute_merge import merge_attribute_reports
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods.frame_attribute_merge import merge_attribute_data
from tests.shared_fixtures import DslCaseFactory


def test_union_preserves_exact_joint_null_keys_and_per_target_missingness() -> None:
    key = DataColumn("key", "key", ScalarDataType("int64"), True)
    group = DataColumn("group", "group", ScalarDataType("string"), False)
    a = DataColumn("a", "a.contribution", DecimalDataType(38, 0), True)
    b = DataColumn("b", "b.contribution", DecimalDataType(38, 0), True)
    present = DataColumn("present", "a.present", ScalarDataType("boolean"), False)
    other_present = DataColumn("present", "b.present", ScalarDataType("boolean"), False)
    axes = (DataAxis("joint", (key, group), name="joint"),)
    schemas = (
        DataSchema(
            axes,
            (a,),
            (present,),
            value_paths=(("a", "contribution"),),
            presence_paths=(("a", "present"),),
        ),
        DataSchema(
            axes,
            (b,),
            (other_present,),
            value_paths=(("b", "contribution"),),
            presence_paths=(("b", "present"),),
        ),
    )
    large = 2**60 + 1
    left = pd.DataFrame(
        {
            "key": pd.Series([large, None], dtype="int64[pyarrow]"),
            "group": ["x", "n"],
            "a": [Decimal(2), Decimal(-1)],
            "present": [True, True],
        }
    )
    right = pd.DataFrame(
        {
            "key": pd.Series([large, large], dtype="int64[pyarrow]"),
            "group": ["y", "x"],
            "b": [Decimal(5), None],
            "present": [True, True],
        }
    )
    inputs = tuple(
        StageOutput.from_frame(table, schema.columns, schema.key_fields, frame_schema=schema)
        for table, schema in zip((left, right), schemas, strict=True)
    )
    output = infer_attribute_merge_schema(schemas)
    result, attachments = merge_attribute_data(
        AttributeMergeProgram(AttributeMergeValues(), schemas, output), inputs
    )
    assert attachments == ()
    assert result["key"].iloc[0] == large
    assert result["key"].isna().tolist() == [False, True, False]
    assert result["group"].tolist() == ["x", "n", "y"]
    assert pa.array(result[output.values[0].slot_id]).to_pylist() == [Decimal(2), Decimal(-1), None]
    assert pa.array(result[output.values[1].slot_id]).to_pylist() == [None, None, Decimal(5)]
    assert result[output.presence[0].slot_id].tolist() == [True, True, False]
    assert result[output.presence[1].slot_id].tolist() == [True, False, True]


def test_merge_uses_existing_frame_schedule_for_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    region = case.session.catalog.fields.get("sales.customer.region")
    left = case.session.values(
        by=(region,), columns={"a": "CNY"}, rows=((("east",), {"a": 2}),)
    ).compute()
    right = case.session.values(
        by=(region,), columns={"b": "CNY"}, rows=((("west",), {"b": 3}),)
    ).compute()
    merged = merge_attribute_reports((left, right)).compute()
    assert merged.output_names == ("a", "b")
    rows = merged._backing.data.copy_rows()
    assert len(rows) == 2
    assert rows[merged._schema.values[0].slot_id].isna().tolist() == [False, True]
    assert rows[merged._schema.values[1].slot_id].isna().tolist() == [True, False]
