from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest

from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.analysis.semantic_inputs import (
    normalize_dimension_boundary,
    normalize_dimension_input,
    normalize_dimension_inputs,
    normalize_metric_input,
    normalize_where_inputs,
)
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.refs import make_ref


class _EmptyCatalogList:
    def __iter__(self) -> Iterator[object]:
        return iter(())


class _ExplodingCatalog:
    def get(self, ref: object) -> object:
        raise RuntimeError("boom")

    def list(self, *args: object, **kwargs: object) -> _EmptyCatalogList:
        return _EmptyCatalogList()


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/model.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def country(table):\n"
                "    return table.country\n"
                "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
                "def ds(table):\n"
                "    return table.ds\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(table):\n"
                "    return table.amount.sum()\n"
            ),
        }
    )
    return SemanticCatalog(project)


def test_normalize_metric_accepts_semantic_object_and_ref(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    metric = catalog.get("sales.revenue")

    assert normalize_metric_input(catalog, metric) == "sales.revenue"
    assert normalize_metric_input(catalog, metric.ref) == "sales.revenue"


def test_normalize_metric_rejects_bare_string(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, "sales.revenue")

    assert exc.value.details["expected_kind"] == "metric"
    assert exc.value.details["actual_kind"] == "str"


def test_normalize_metric_rejects_wrong_semantic_kind(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    dim = catalog.get("sales.orders.country")

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, dim.ref)

    assert exc.value.details["expected_kind"] == "metric"
    assert exc.value.details["actual_kind"] == "dimension"


def test_normalize_metric_rejects_forged_metric_ref_to_dimension(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    forged = make_ref("sales.orders.country", SemanticKind.METRIC)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, forged)

    assert exc.value.details["expected_kind"] == "metric"
    assert exc.value.details["actual_kind"] == "dimension"


def test_normalize_metric_unknown_ref_raises_metric_not_found(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(MetricNotFoundError) as exc:
        normalize_metric_input(catalog, make_ref("sales.missing", SemanticKind.METRIC))

    assert exc.value.details["metric"] == "sales.missing"
    assert "sales.revenue" in exc.value.details["available_ids"]
    assert "Available metrics: sales.revenue" in str(exc.value)


def test_normalize_metric_does_not_swallow_unexpected_catalog_failure() -> None:
    catalog = cast("SemanticCatalog", _ExplodingCatalog())

    with pytest.raises(RuntimeError, match="boom"):
        normalize_metric_input(catalog, make_ref("sales.revenue", SemanticKind.METRIC))


def test_normalize_dimension_accepts_dimension_and_time_dimension(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert (
        normalize_dimension_input(catalog, catalog.get("sales.orders.country"))
        == "sales.orders.country"
    )
    assert (
        normalize_dimension_input(catalog, catalog.get("sales.orders.ds").ref) == "sales.orders.ds"
    )
    assert normalize_dimension_inputs(catalog, [catalog.get("sales.orders.country").ref]) == [
        "sales.orders.country"
    ]


def test_normalize_dimension_rejects_forged_dimension_ref_to_metric(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    forged = make_ref("sales.revenue", SemanticKind.DIMENSION)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_input(catalog, forged)

    assert exc.value.details["expected_kind"] == "dimension"
    assert exc.value.details["actual_kind"] == "metric"


def test_normalize_dimension_boundary_rejects_metric_object_when_catalog_has_no_dimensions(
    semantic_project_factory,
) -> None:
    source_catalog = _catalog(semantic_project_factory)
    empty_catalog = cast("SemanticCatalog", _ExplodingCatalog())

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_boundary(empty_catalog, source_catalog.get("sales.revenue"))

    assert exc.value.details["expected_kind"] == "dimension"
    assert exc.value.details["actual_kind"] == "metric"


def test_normalize_dimension_unknown_ref_raises_analysis_error(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_input(
            catalog,
            make_ref("sales.orders.missing", SemanticKind.DIMENSION),
        )

    assert exc.value.details["argument"] == "dimension"
    assert exc.value.details["ref"] == "sales.orders.missing"
    assert exc.value.details["expected_kind"] == "dimension"
    assert exc.value.details["actual_kind"] == "not_found"
    assert "sales.orders.country" in exc.value.details["available_ids"]
    assert "sales.orders.ds" in exc.value.details["available_ids"]


def test_normalize_where_inputs_returns_plain_string_keys(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    country = catalog.get("sales.orders.country").ref
    ds = catalog.get("sales.orders.ds")

    assert normalize_where_inputs(
        catalog, {country: "US", ds: {"op": ">=", "value": "2026-01-01"}}
    ) == {
        "sales.orders.country": "US",
        "sales.orders.ds": {"op": ">=", "value": "2026-01-01"},
    }


def test_normalize_where_inputs_unknown_key_raises_analysis_error(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_where_inputs(
            catalog,
            {make_ref("sales.orders.missing", SemanticKind.DIMENSION): "US"},
        )

    assert exc.value.details["argument"] == "where"
    assert exc.value.details["ref"] == "sales.orders.missing"
    assert exc.value.details["expected_kind"] == "dimension"
    assert exc.value.details["actual_kind"] == "not_found"
    assert "sales.orders.country" in exc.value.details["available_ids"]
    assert "sales.orders.ds" in exc.value.details["available_ids"]


def test_measure_ref_is_rejected_as_dimension_axis(semantic_project_factory) -> None:
    from marivo.analysis.semantic_inputs import normalize_dimension_input

    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(
            catalog,
            make_ref("sales.orders.amount", SemanticKind.MEASURE),
        )

    message = str(exc_info.value)
    assert "measure" in message
    assert "group-by axis" in message
    assert "categorical dimension" in message
