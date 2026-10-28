"""One numeric column can have separately named grouping and quantity roles."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value, load_inline_semantic

ProjectFactory = Callable[..., SemanticProject]


def _model(*, derived: bool, dimension_name: str = "quantity_axis") -> str:
    entity = (
        '@ms.entity(datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])\n'
        "def orders(raw):\n"
        '    return raw.mutate(quantity=raw["amount"] * 2)\n'
        if derived
        else 'orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])\n'
    )
    column = "quantity" if derived else "amount"
    return (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        + entity
        + f"dimension = ms.dimension_column(name={dimension_name!r}, entity=orders, column={column!r})\n"
        + f'quantity = ms.measure_column(name="quantity", entity=orders, column={column!r}, '
        'unit="ms", additivity=ms.additive_all())\n'
    )


@pytest.mark.parametrize("derived", (False, True), ids=("physical", "entity-derived"))
def test_numeric_column_supports_separate_dimension_and_measure_roles(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    derived: bool,
) -> None:
    source_path = tmp_path / "orders.duckdb"
    with duckdb.connect(str(source_path)) as connection:
        connection.execute("CREATE TABLE orders(id BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO orders VALUES (1, 2), (2, 4), (3, NULL)")
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(source_path)!r})\n",
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _model(derived=derived),
        }
    )
    assert not project.errors(), project.errors()
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("dual-role-fields", timezone="UTC")
    dimension = catalog_value(session.catalog, "sales.orders.quantity_axis", kind="dimension")
    quantity = catalog_value(session.catalog, "sales.orders.quantity", kind="measure")
    for expression in (dimension.mean(), dimension.sum()):
        with pytest.raises(SemanticExpressionError, match="numeric Measure"):
            consume_metric(expression)
    with pytest.raises(SemanticExpressionError, match="numeric row expression"):

        @ms.measure()
        def invalid_conversion():
            return dimension

    frame = session.observe(
        {"mean": quantity.mean(), "sum": quantity.sum()}, by=(dimension,)
    ).compute()
    rows = frame.to_pandas()
    axis_name = frame.axes[0].name
    expected = [4, 8] if derived else [2, 4]
    represented = rows.loc[rows[axis_name].notna()].sort_values(axis_name)
    assert represented[axis_name].tolist() == expected
    assert represented["mean"].tolist() == expected
    assert represented["sum"].tolist() == expected
    missing = rows.loc[rows[axis_name].isna()]
    assert len(missing) == 1
    assert all(pd.isna(missing.iloc[0][name]) for name in ("mean", "sum"))
    assert {name: field.unit for name, field in frame.schema.values.items()} == {
        "mean": "ms",
        "sum": "ms",
    }
    total = (
        session.observe({"mean": quantity.mean(), "distinct": dimension.count_distinct()})
        .compute()
        .to_pandas()
    )
    assert total["mean"].tolist() == [sum(expected) / len(expected)]
    assert total["distinct"].tolist() == [2]


@pytest.mark.parametrize("derived", (False, True), ids=("physical", "entity-derived"))
def test_dimension_and_measure_roles_keep_distinct_semantic_names(derived: bool) -> None:
    source = _model(derived=derived, dimension_name="quantity").replace('"warehouse"', '"wh"')
    with load_inline_semantic(source) as result:
        assert result.registry is None
        assert any(error.kind == ErrorKind.DUPLICATE_NAME for error in result.errors)
        assert any("already claimed by a dimension" in str(error) for error in result.errors)
