"""Boolean result Columns retain fixed-input and fixed-data filtering semantics."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import DslCase, DslCaseFactory


@pytest.fixture
def boolean_frame(analysis_dsl_case_factory: DslCaseFactory) -> tuple[DslCase, mv.AnalysisFrame]:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE customer SET region=NULL WHERE customer_id='B'")
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    source = session.read(members, {"region": session.catalog.fields.get("sales.customer.region")})
    flags = source.select({"flag": source["region"] == "east", "region": source["region"]})
    return case, flags.compute()


def test_boolean_operations_return_plain_columns_and_select_only_true(
    boolean_frame: tuple[DslCase, mv.AnalysisFrame],
) -> None:
    _, frame = boolean_frame
    flag = frame["flag"]
    region = frame["region"]
    for column in (
        flag,
        ~flag,
        flag & ~flag,
        flag | ~flag,
        flag.is_null(),
        region == "east",
        region != "east",
        region < "east",
        region <= "east",
        region > "east",
        region >= "east",
    ):
        assert type(column) is mv.Column
        assert column._kind == "boolean"
    with pytest.raises(AnalysisError, match="Python truth conversion"):
        bool(flag)
    with pytest.raises(AnalysisError, match="a boolean Column"):
        frame.where(region)

    rows = frame.to_pandas()
    key = ordinary_columns(rows)[0]
    for predicate, expected in (
        (flag, ["A"]),
        (~flag, ["C", "D"]),
        (flag.is_null(), ["B"]),
        (flag | flag.is_null(), ["A", "B"]),
    ):
        filtered = frame.where(predicate).compute().to_pandas()
        assert filtered[key].tolist() == expected
        pd.testing.assert_frame_equal(
            filtered, rows.loc[rows[key].isin(expected)].reset_index(drop=True)
        )

    for derived in (
        frame.select({"flag": flag}),
        frame.where(flag),
        frame.top(2, by=region),
    ):
        with pytest.raises(AnalysisError, match="exact Store/Session/Frame"):
            derived.where(flag)
    other = frame.select({"flag": flag, "region": region}).compute()
    assert other.output_names == frame.output_names
    with pytest.raises(AnalysisError, match="exact Store/Session/Frame"):
        other.where(flag)
    with pytest.raises(AnalysisError, match="different Frame inputs"):
        flag & other["flag"]


def test_presence_and_nullable_boolean_filtering_survive_storage(
    boolean_frame: tuple[DslCase, mv.AnalysisFrame],
) -> None:
    case, frame = boolean_frame
    right = frame.where(frame.coord(frame.axes[0]) != "D")
    joined = frame.align(right, on=tuple(zip(frame.axes, right.axes, strict=True)))
    for name in joined.presence_names:
        assert type(joined[name]) is mv.Column
        assert joined[name]._kind == "boolean"
    original = joined.compute()
    rows = original.to_pandas()
    key = ordinary_columns(rows)[0]
    assert rows[key].tolist() == ["A", "B", "C", "D"]
    assert rows["right_present"].tolist() == [True, True, True, False]
    selected = original.where(original["right.flag"].is_null()).compute()
    expected = selected.to_pandas()
    assert expected[key].tolist() == ["B", "D"]
    assert expected["right_present"].tolist() == [True, False]
    assert selected.presence_names == original.presence_names
    assert tuple(axis.name for axis in selected.axes) == tuple(axis.name for axis in original.axes)

    saved = original.persist()
    with pytest.raises(AnalysisError, match="exact Store/Session/Frame"):
        saved.where(original["left.flag"])
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        reopened = case.session.persisted_frame(saved.artifact.id)
        pd.testing.assert_frame_equal(reopened.to_pandas(), rows)
        restored = reopened.where(reopened["right.flag"].is_null()).compute()
        pd.testing.assert_frame_equal(restored.to_pandas(), expected)
        present = reopened.where(saved["right_present"]).compute().to_pandas()
        assert present[key].tolist() == ["A", "B", "C"]
    finally:
        offline.rename(case.database_path)
