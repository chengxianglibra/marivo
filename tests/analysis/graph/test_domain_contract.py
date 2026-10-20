"""Independent value statistics have their own Frame quantities and units."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory


def test_reduced_count_compares_as_an_independent_quantity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
    )
    selected = values.where(values.coord(values.axes[1]) != "D")
    current = values.reduce({"rows": mv.row_count()})
    baseline = selected.reduce({"rows": mv.row_count()})
    assert current.persist().to_pandas()[("value", "rows")].tolist() == [4]
    assert baseline.persist().to_pandas()[("value", "rows")].tolist() == [3]
    result = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    ).persist()
    frame = result.to_pandas()
    assert frame[("value", "reference.absolute.rows")].tolist() == [1]
    assert frame[("value", "reference.relative.rows")].tolist() == pytest.approx([1 / 3])
    schema = result.artifact._manifest.data.schema
    assert all(column.unit is None for column in schema.values)
    same = (
        current.compare(
            {"reference": current},
            on={"reference": tuple(zip(current.axes, current.axes, strict=True))},
        )
        .persist()
        .to_pandas()
    )
    assert same[("value", "reference.absolute.rows")].tolist() == [0]
    assert same[("value", "reference.relative.rows")].tolist() == [0.0]
