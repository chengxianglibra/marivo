from __future__ import annotations

import inspect
from typing import cast

import pytest

import marivo.semantic as ms
from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.analysis.semantic_inputs import (
    normalize_dimension_boundary,
    normalize_dimension_input,
    normalize_dimension_inputs,
    normalize_metric_input,
    normalize_where_inputs,
)
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from tests.ref_helpers import make_ref


class _EmptyIndex:
    _by_ref: dict[object, object] = {}

    def semantic_ids(self, *args: object, **kwargs: object) -> tuple[str, ...]:
        return ()

    def kind_of(self, *args: object, **kwargs: object) -> None:
        return None

    def require(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")


class _ExplodingCatalog:
    def require(self, ref: object) -> object:
        raise RuntimeError("boom")

    def _require_index(self) -> _EmptyIndex:
        return _EmptyIndex()


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/model.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
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


@pytest.mark.parametrize(
    ("owner", "parameter"),
    [
        ("Session.observe", "metrics"),
        ("Session.observe", "dimensions"),
        ("Session.observe", "slice_by"),
        ("Session.observe", "time_dimension"),
        ("Session.attribute", "axes"),
        ("SessionDiscoverNamespace.driver_axes", "search_space"),
        ("SessionDiscoverNamespace.interesting_slices", "search_space"),
        ("SessionDiscoverNamespace.cross_sectional_outliers", "peer_scope"),
        ("_FrameTransforms.slice", "slice_by"),
        ("_FrameTransforms.rollup", "drop_axes"),
    ],
)
def test_frozen_analysis_consumer_inventory_uses_semantic_input(
    owner: str,
    parameter: str,
) -> None:
    from marivo.analysis.frames.transforms import _FrameTransforms
    from marivo.analysis.session.core import Session, SessionDiscoverNamespace

    owners = {
        "Session.observe": Session.observe,
        "Session.attribute": Session.attribute,
        "SessionDiscoverNamespace.driver_axes": SessionDiscoverNamespace.driver_axes,
        "SessionDiscoverNamespace.interesting_slices": (
            SessionDiscoverNamespace.interesting_slices
        ),
        "SessionDiscoverNamespace.cross_sectional_outliers": (
            SessionDiscoverNamespace.cross_sectional_outliers
        ),
        "_FrameTransforms.slice": _FrameTransforms.slice,
        "_FrameTransforms.rollup": _FrameTransforms.rollup,
    }
    annotation = inspect.signature(owners[owner]).parameters[parameter].annotation
    assert "_SemanticInput" in str(annotation)


def test_event_journey_signature_has_no_direct_semantic_input() -> None:
    from marivo.analysis.session.core import SessionEvents

    signature = inspect.signature(SessionEvents.match)
    assert all(
        "_SemanticInput" not in str(parameter.annotation)
        for parameter in signature.parameters.values()
    )


def test_normalize_metric_accepts_exact_ref_and_loaded_object(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    assert normalize_metric_input(catalog, metric.ref) == "sales.revenue"
    assert normalize_metric_input(catalog, metric) == "sales.revenue"


def test_stale_metric_entry_does_not_claim_an_executable_analysis_retry(
    semantic_project_factory,
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_metric = old_catalog.metrics.get("sales.revenue")
    current_catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError, match="earlier catalog instance") as exc_info:
        normalize_metric_input(current_catalog, stale_metric)

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert repair.snippet is None
    assert repair.help_target.surface == "analysis"
    assert repair.help_target.canonical_id == "observe"
    assert repair.candidates == ("metric:sales.revenue",)


def test_normalize_metric_rejects_bare_string(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, "sales.revenue")

    assert exc.value._context["expected_kind"] == "metric"
    assert exc.value._context["actual_kind"] == "str"
    assert "available_refs" in exc.value._context
    assert "metric:sales.revenue" in exc.value._context["available_refs"]
    message = str(exc.value)
    assert "sales.revenue" in message
    assert "Candidates: metric:sales.revenue" in message


def test_normalize_metric_rejects_wrong_semantic_kind(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    dim = catalog.require(ms.ref.dimension("sales.orders.country"))

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_metric_input(catalog, dim.ref)

    assert exc.value._context["expected_kind"] == "metric"
    assert exc.value._context["actual_kind"] == "dimension"


def test_metric_factory_prevents_forged_metric_ref_to_dimension(
    semantic_project_factory,
) -> None:
    _catalog(semantic_project_factory)
    with pytest.raises(ValueError, match="exactly 2 segments"):
        ms.ref.metric("sales.orders.country")


def test_normalize_metric_unknown_ref_raises_metric_not_found(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(MetricNotFoundError) as exc:
        normalize_metric_input(catalog, make_ref("sales.missing", SemanticKind.METRIC))

    assert exc.value._context["metric"] == "sales.missing"
    assert "metric:sales.revenue" in exc.value._context["available_refs"]
    assert "metric:sales.revenue" in str(exc.value)


def test_normalize_metric_does_not_swallow_unexpected_catalog_failure() -> None:
    catalog = cast("SemanticCatalog", _ExplodingCatalog())

    with pytest.raises(RuntimeError, match="boom"):
        normalize_metric_input(catalog, make_ref("sales.revenue", SemanticKind.METRIC))


def test_normalize_dimension_accepts_dimension_and_time_dimension(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert (
        normalize_dimension_input(
            catalog, catalog.require(ms.ref.dimension("sales.orders.country")).ref
        )
        == "sales.orders.country"
    )
    assert (
        normalize_dimension_input(
            catalog, catalog.require(ms.ref.time_dimension("sales.orders.ds")).ref
        )
        == "sales.orders.ds"
    )
    assert normalize_dimension_inputs(
        catalog, [catalog.require(ms.ref.dimension("sales.orders.country")).ref]
    ) == ["sales.orders.country"]


def test_dimension_factory_prevents_forged_dimension_ref_to_metric(
    semantic_project_factory,
) -> None:
    _catalog(semantic_project_factory)
    with pytest.raises(ValueError, match="exactly 3 segments"):
        ms.ref.dimension("sales.revenue")


def test_normalize_dimension_boundary_rejects_wrong_kind_entry(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    with pytest.raises(SemanticKindMismatchError) as exc:
        normalize_dimension_boundary(catalog, metric)

    assert exc.value._context["expected_kind"] == "dimension or time_dimension"
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
    assert exc.value._context["ref"] == "dimension:sales.orders.missing"
    assert exc.value._context["expected_kind"] == "dimension or time_dimension"
    assert exc.value._context["actual_kind"] == "dimension"
    assert "dimension:sales.orders.country" in exc.value._context["available_refs"]
    assert "time_dimension:sales.orders.ds" in exc.value._context["available_refs"]


def test_normalize_where_inputs_returns_plain_string_keys(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    country = catalog.require(ms.ref.dimension("sales.orders.country")).ref
    ds = catalog.require(ms.ref.time_dimension("sales.orders.ds")).ref

    assert normalize_where_inputs(
        catalog, {country: "US", ds: {"op": ">=", "value": "2026-01-01"}}
    ) == {
        "sales.orders.country": "US",
        "sales.orders.ds": {"op": ">=", "value": "2026-01-01"},
    }


def test_normalize_where_inputs_rejects_entry_ref_key_collision(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    country = catalog.dimensions.get("sales.orders.country")

    with pytest.raises(
        SemanticKindMismatchError,
        match="must remain unique after semantic input normalization",
    ) as exc_info:
        normalize_where_inputs(
            catalog,
            {country: "US", country.ref: "CA"},
        )

    assert exc_info.value.location == "slice_by"
    assert exc_info.value._context["duplicate_dimension"] == "sales.orders.country"


def test_normalize_where_inputs_preserves_time_dimension_kind_in_collision_repair(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    ds = catalog.time_dimensions.get("sales.orders.ds")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_where_inputs(
            catalog,
            {ds: "2026-01-01", ds.ref: "2026-01-02"},
        )

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.candidates == ("time_dimension:sales.orders.ds",)


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
    assert exc.value._context["ref"] == "dimension:sales.orders.missing"
    assert exc.value._context["expected_kind"] == "dimension or time_dimension"
    assert exc.value._context["actual_kind"] == "dimension"
    assert "dimension:sales.orders.country" in exc.value._context["available_refs"]
    assert "time_dimension:sales.orders.ds" in exc.value._context["available_refs"]


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
    assert exc_info.value.expected == (
        "exact Ref or current CatalogEntry with kind in {dimension, time_dimension}"
    )


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
    assert details["expected_kind"] == "dimension or time_dimension"
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert "dimension:sales.orders.country" in exc_info.value.repair.candidates

    # str(error) must surface the repair snippets — the primary way agents
    # consume error messages — and must not fall through to the generic
    # "Input frame kind" fallback cause.
    message = str(exc_info.value)
    assert "Candidates:" in message
    assert "measure" in message
    assert "current CatalogEntry" in message
    assert "Input frame kind" not in message


# --- Repair guidance tests (Task 4: semantic input error guidance) ---


def test_time_dimension_argument_uses_correct_label(semantic_project_factory) -> None:
    """When argument='time_dimension', the error must say 'time dimension',
    not 'catalog dimension'."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    message = str(exc_info.value)
    assert "current CatalogEntry" in message
    assert "catalog dimension" not in message


def test_time_dimension_argument_includes_repair_guidance(semantic_project_factory) -> None:
    """A wrong-kind input for the time_dimension argument must include repair
    guidance with copyable catalog snippets in both details and str(error)."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    details = exc_info.value._context
    assert details["argument"] == "time_dimension"
    assert details["ref"] == "metric:sales.revenue"
    assert details["expected_kind"] == "dimension or time_dimension"
    assert details["actual_kind"] == "metric"
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"

    # Repair snippets must be surfaced in str(error) — the primary way agents
    # consume error messages.
    message = str(exc_info.value)
    assert "Candidates:" in message
    assert "current CatalogEntry" in message
    assert "metric" in message  # actual_kind appears in the cause


def test_dimension_argument_label_says_dimension_or_time_dimension(
    semantic_project_factory,
) -> None:
    """When expected_kind='dimension', the error label should mention both
    'dimension' and 'time dimension' since both are accepted."""
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="dimension")

    message = str(exc_info.value)
    assert "current CatalogEntry" in message


def test_wrong_kind_metric_includes_repair_and_available_ids(
    semantic_project_factory,
) -> None:
    """A wrong-kind metric input must carry available_ids and repair guidance,
    and both must be surfaced in str(error)."""
    catalog = _catalog(semantic_project_factory)
    dim = catalog.require(ms.ref.dimension("sales.orders.country"))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_metric_input(catalog, dim.ref)

    details = exc_info.value._context
    assert details["argument"] == "metric"
    assert details["ref"] == "dimension:sales.orders.country"
    assert details["expected_kind"] == "metric"
    assert details["actual_kind"] == "dimension"
    assert "available_refs" in details
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"

    # str(error) must surface the kind info, available ids, and repair snippets.
    message = str(exc_info.value)
    assert "metric" in message  # expected_kind in cause
    assert "dimension" in message  # actual_kind in cause
    assert "sales.revenue" in message  # available_ids preview
    assert "Candidates:" in message


def test_wrong_kind_repair_is_inspection_without_placeholder_retry(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, metric, argument="time_dimension")

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert repair.snippet is None
    assert repair.candidates == (
        "dimension:sales.orders.country",
        "time_dimension:sales.orders.ds",
    )
