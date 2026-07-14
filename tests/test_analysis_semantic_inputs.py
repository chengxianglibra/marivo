from __future__ import annotations

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


class _EmptyIndex:
    def semantic_ids(self, *args: object, **kwargs: object) -> tuple[str, ...]:
        return ()

    def kind_of(self, *args: object, **kwargs: object) -> None:
        return None


class _ExplodingCatalog:
    def get(self, ref: object) -> object:
        raise RuntimeError("boom")

    def _require_index(self) -> _EmptyIndex:
        return _EmptyIndex()


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/model.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
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
    metric = catalog.get("metric.sales.revenue")

    assert normalize_metric_input(catalog, metric) == "sales.revenue"
    assert normalize_metric_input(catalog, metric.ref) == "sales.revenue"


def test_normalize_metric_rejects_bare_string(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, "sales.revenue")

    assert exc.value._context["expected_kind"] == "metric"
    assert exc.value._context["actual_kind"] == "str"
    # Bare-string path must include available_ids so the agent can discover
    # the correct metric id from the catalog.
    assert "available_ids" in exc.value._context
    assert "sales.revenue" in exc.value._context["available_ids"]
    # str(error) must also surface available_ids and repair snippets.
    message = str(exc.value)
    assert "sales.revenue" in message
    assert "session.catalog." in message


def test_normalize_metric_rejects_wrong_semantic_kind(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    dim = catalog.get("dimension.sales.orders.country")

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, dim.ref)

    assert exc.value._context["expected_kind"] == "metric"
    assert exc.value._context["actual_kind"] == "dimension"


def test_normalize_metric_rejects_forged_metric_ref_to_dimension(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    forged = make_ref("sales.orders.country", SemanticKind.METRIC)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, forged)

    assert exc.value._context["expected_kind"] == "metric"
    assert exc.value._context["actual_kind"] == "dimension"


def test_normalize_metric_unknown_ref_raises_metric_not_found(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(MetricNotFoundError) as exc:
        normalize_metric_input(catalog, make_ref("sales.missing", SemanticKind.METRIC))

    assert exc.value._context["metric"] == "sales.missing"
    assert "sales.revenue" in exc.value._context["available_ids"]
    assert "Candidates: sales.revenue" in str(exc.value)


def test_normalize_metric_does_not_swallow_unexpected_catalog_failure() -> None:
    catalog = cast("SemanticCatalog", _ExplodingCatalog())

    with pytest.raises(RuntimeError, match="boom"):
        normalize_metric_input(catalog, make_ref("sales.revenue", SemanticKind.METRIC))


def test_normalize_dimension_accepts_dimension_and_time_dimension(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert (
        normalize_dimension_input(catalog, catalog.get("dimension.sales.orders.country"))
        == "sales.orders.country"
    )
    assert (
        normalize_dimension_input(catalog, catalog.get("time_dimension.sales.orders.ds").ref)
        == "sales.orders.ds"
    )
    assert normalize_dimension_inputs(
        catalog, [catalog.get("dimension.sales.orders.country").ref]
    ) == ["sales.orders.country"]


def test_normalize_dimension_rejects_forged_dimension_ref_to_metric(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    forged = make_ref("sales.revenue", SemanticKind.DIMENSION)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_input(catalog, forged)

    assert exc.value._context["expected_kind"] == "dimension"
    assert exc.value._context["actual_kind"] == "metric"


def test_normalize_dimension_boundary_rejects_metric_object_when_catalog_has_no_dimensions(
    semantic_project_factory,
) -> None:
    source_catalog = _catalog(semantic_project_factory)
    empty_catalog = cast("SemanticCatalog", _ExplodingCatalog())

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_boundary(empty_catalog, source_catalog.get("metric.sales.revenue"))

    assert exc.value._context["expected_kind"] == "dimension"
    assert exc.value._context["actual_kind"] == "metric"


def test_normalize_dimension_unknown_ref_raises_analysis_error(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_input(
            catalog,
            make_ref("sales.orders.missing", SemanticKind.DIMENSION),
        )

    assert exc.value._context["argument"] == "dimension"
    assert exc.value._context["ref"] == "sales.orders.missing"
    assert exc.value._context["expected_kind"] == "dimension"
    assert exc.value._context["actual_kind"] == "not_found"
    assert "sales.orders.country" in exc.value._context["available_ids"]
    assert "sales.orders.ds" in exc.value._context["available_ids"]


def test_normalize_where_inputs_returns_plain_string_keys(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    country = catalog.get("dimension.sales.orders.country").ref
    ds = catalog.get("time_dimension.sales.orders.ds")

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

    assert exc.value._context["argument"] == "slice_by"
    assert exc.value._context["ref"] == "sales.orders.missing"
    assert exc.value._context["expected_kind"] == "dimension"
    assert exc.value._context["actual_kind"] == "not_found"
    assert "sales.orders.country" in exc.value._context["available_ids"]
    assert "sales.orders.ds" in exc.value._context["available_ids"]


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


def test_measure_rejection_surfaces_repair_in_str(semantic_project_factory) -> None:
    """A measure-rejection error must surface repair snippets in str(error),
    not fall through to the generic 'Input frame kind' fallback."""
    from marivo.analysis.semantic_inputs import normalize_dimension_input

    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(
            catalog,
            make_ref("sales.orders.amount", SemanticKind.MEASURE),
        )

    details = exc_info.value._context
    assert details["actual_kind"] == "measure"
    assert details["expected_kind"] == "dimension"
    assert "repair" in details
    repair = details["repair"]
    assert isinstance(repair, list)
    assert any("session.catalog." in snippet for snippet in repair)

    # str(error) must surface the repair snippets — the primary way agents
    # consume error messages — and must not fall through to the generic
    # "Input frame kind" fallback cause.
    message = str(exc_info.value)
    assert "session.catalog." in message
    assert "measure" in message
    assert "group-by axis" in message
    assert "categorical dimension" in message
    assert "Input frame kind" not in message


# --- Repair guidance tests (Task 4: semantic input error guidance) ---


def test_time_dimension_argument_uses_correct_label(semantic_project_factory) -> None:
    """When argument='time_dimension', the error must say 'time dimension',
    not 'catalog dimension'."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.get("metric.sales.revenue")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    message = str(exc_info.value)
    assert "time dimension" in message
    assert "catalog dimension" not in message


def test_time_dimension_argument_includes_repair_guidance(semantic_project_factory) -> None:
    """A wrong-kind input for the time_dimension argument must include repair
    guidance with copyable catalog snippets in both details and str(error)."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.get("metric.sales.revenue")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    details = exc_info.value._context
    assert details["argument"] == "time_dimension"
    assert details["ref"] == "sales.revenue"
    assert details["expected_kind"] == "dimension"
    assert details["actual_kind"] == "metric"
    assert "repair" in details
    repair = details["repair"]
    assert isinstance(repair, list)
    assert any("session.catalog." in snippet for snippet in repair)

    # Repair snippets must be surfaced in str(error) — the primary way agents
    # consume error messages.
    message = str(exc_info.value)
    assert "session.catalog." in message
    assert "time dimension" in message
    assert "metric" in message  # actual_kind appears in the cause


def test_dimension_argument_label_says_dimension_or_time_dimension(
    semantic_project_factory,
) -> None:
    """When expected_kind='dimension', the error label should mention both
    'dimension' and 'time dimension' since both are accepted."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.get("metric.sales.revenue")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="dimension")

    message = str(exc_info.value)
    assert "dimension or time dimension" in message


def test_wrong_kind_metric_includes_repair_and_available_ids(
    semantic_project_factory,
) -> None:
    """A wrong-kind metric input must carry available_ids and repair guidance,
    and both must be surfaced in str(error)."""
    catalog = _catalog(semantic_project_factory)
    dim = catalog.get("dimension.sales.orders.country")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_metric_input(catalog, dim.ref)

    details = exc_info.value._context
    assert details["argument"] == "metric"
    assert details["ref"] == "sales.orders.country"
    assert details["expected_kind"] == "metric"
    assert details["actual_kind"] == "dimension"
    assert "available_ids" in details
    assert "repair" in details
    repair = details["repair"]
    assert isinstance(repair, list)
    assert any("session.catalog." in snippet for snippet in repair)

    # str(error) must surface the kind info, available ids, and repair snippets.
    message = str(exc_info.value)
    assert "metric" in message  # expected_kind in cause
    assert "dimension" in message  # actual_kind in cause
    assert "sales.revenue" in message  # available_ids preview
    assert "session.catalog." in message  # repair snippets


def test_repair_snippets_use_typed_collection_form(semantic_project_factory) -> None:
    """Repair snippets must use typed collection form with placeholders,
    not the legacy catalog.list(...) form."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.get("metric.sales.revenue")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    details = exc_info.value._context
    repair = details["repair"]
    assert isinstance(repair, list)
    # At least one snippet must use the typed collection form
    assert any("session.catalog." in snippet for snippet in repair)
    # No snippet should use the legacy catalog.list(...) form
    for snippet in repair:
        assert "catalog.list(" not in snippet
    # At least one snippet must reference time_dimensions
    assert any("time_dimensions" in snippet for snippet in repair)
    # No snippet should hard-code project-specific ids like "sales.orders"
    for snippet in repair:
        assert "sales.orders" not in snippet
        assert "sales.revenue" not in snippet
