"""Typed metadata contract-drift tests for metric/component persistence.

Issue #54: MetricFrame/ComponentFrame must persist a single typed
value/component/axis contract. Display names and compact dicts may only be
derived at render boundaries — they must never become a second source of truth
for intents, evidence, or recovery.

These tests pin the typed surface so that future additions either stay on the
typed authority or fail loudly.
"""

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import AxisBindingV1, MeasureBindingV1
from marivo.analysis.intents._metric_axes import (
    metric_dimension_columns,
    metric_time_axis,
)
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    RuntimeExpressionIdentity,
)


def _time_binding(*, column: str, grain: str | None = "day") -> AxisBindingV1:
    return AxisBindingV1(
        ref=RefPayloadV1.from_ref(ref_factory.time_dimension("sales.orders.created_at")),
        column=column,
        role="time_dimension",
        grain=grain,
    )


def _dim_binding(*, column: str, path: str = "sales.orders.region") -> AxisBindingV1:
    return AxisBindingV1(
        ref=RefPayloadV1.from_ref(ref_factory.dimension(path)),
        column=column,
        role="dimension",
        grain=None,
    )


def _catalog_identity(path: str) -> CatalogMetricIdentity:
    return CatalogMetricIdentity(
        kind="catalog",
        metric_ref=RefPayloadV1.from_ref(ref_factory.metric(path)),
    )


def _runtime_identity(fingerprint: str) -> RuntimeExpressionIdentity:
    return RuntimeExpressionIdentity(
        kind="runtime_expression",
        expression_schema="metric-expression/v1",
        expression_fingerprint=fingerprint,
    )


def test_measure_binding_accepts_catalog_identity() -> None:
    binding = MeasureBindingV1(
        identity=_catalog_identity("sales.revenue"),
        value_column="value",
        display_name="revenue",
        unit="CNY",
        additivity="additive",
        aggregation=None,
        reaggregatable=True,
    )
    assert binding.identity.metric_ref.path == "sales.revenue"
    assert binding.value_column == "value"
    assert binding.display_name == "revenue"
    assert binding.unit == "CNY"
    assert binding.additivity == "additive"


def test_measure_binding_accepts_runtime_identity() -> None:
    binding = MeasureBindingV1(
        identity=_runtime_identity("sha256:abc"),
        value_column="value",
    )
    assert binding.identity.expression_fingerprint == "sha256:abc"


def test_measure_binding_rejects_blank_value_column() -> None:
    import pytest

    with pytest.raises(ValueError):
        MeasureBindingV1(identity=_catalog_identity("sales.revenue"), value_column="")


def test_measure_binding_rejects_missing_status_dimension_ref_type() -> None:
    import pytest

    with pytest.raises(TypeError):
        MeasureBindingV1(
            identity=_catalog_identity("sales.revenue"),
            value_column="value",
            status_time_dimension_ref="sales.orders.created_at",  # type: ignore[arg-type]
        )


def test_measure_binding_rejects_dict_unit_state() -> None:
    """A raw canonical dict must not pass as a typed unit_state.

    Issue #54 P2: ``_semantic_unit_state``'s legacy fallback could hand the
    binding a canonical *dict*, which then diverged from the typed form after a
    disk reload (cold/hot type split on the same ref). The binding is fail-closed:
    only a real ``MetricUnitStateV2`` is accepted.
    """
    import pytest

    from marivo.semantic.unit_algebra import UnknownUnitV2

    # Typed state passes.
    MeasureBindingV1(
        identity=_catalog_identity("sales.revenue"),
        value_column="value",
        unit_state=UnknownUnitV2(schema="metric-unit-unknown/v2"),
    )
    # Canonical dict form is rejected.
    with pytest.raises(TypeError, match="unit_state must be a MetricUnitStateV2"):
        MeasureBindingV1(
            identity=_catalog_identity("sales.revenue"),
            value_column="value",
            unit_state={"schema": "metric-unit-unknown/v2"},  # type: ignore[arg-type]
        )


def test_measure_binding_serializes_to_stable_json() -> None:
    import dataclasses
    import json

    binding = MeasureBindingV1(
        identity=_catalog_identity("sales.revenue"),
        value_column="value",
        display_name="revenue",
        unit="CNY",
        additivity="additive",
    )
    payload = json.dumps(dataclasses.asdict(binding), sort_keys=True, default=str)
    assert '"path": "sales.revenue"' in payload
    assert '"value_column": "value"' in payload
    assert '"display_name": "revenue"' in payload


def test_measure_binding_pydantic_roundtrip() -> None:
    from pydantic import BaseModel, ConfigDict

    binding = MeasureBindingV1(
        identity=_catalog_identity("sales.revenue"),
        value_column="value",
        display_name="revenue",
        unit="CNY",
        additivity="additive",
        reaggregatable=False,
    )

    class Holder(BaseModel):
        model_config = ConfigDict(extra="forbid")
        bindings: tuple[MeasureBindingV1, ...] = ()

    holder = Holder(bindings=(binding,))
    dumped = holder.model_dump(mode="json")
    assert dumped["bindings"][0]["identity"]["metric_ref"]["path"] == "sales.revenue"
    assert dumped["bindings"][0]["value_column"] == "value"

    restored = Holder.model_validate(dumped)
    assert restored.bindings[0] == binding


# ---------------------------------------------------------------------------
# Axis resolution must come only from typed axis_bindings — never from the
# legacy compact ``axes`` dict (issue #54: delete display-copy fallbacks).
# ---------------------------------------------------------------------------


def _meta_with_bindings(*bindings: AxisBindingV1) -> object:
    """Build a minimal metric meta exposing only typed axis bindings."""
    import sys
    from datetime import datetime

    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from shared_fixtures import make_test_metric_contract

    contract = make_test_metric_contract(
        __import__("pandas").DataFrame(),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "created_at"}},
        where={},
    )
    return MetricFrameMeta(
        kind="metric_frame",
        ref="frame_1",
        session_id="sess",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="test",
                    job_ref=None,
                    inputs=[],
                    params_digest="x",
                )
            ]
        ),
        **{**contract, "axis_bindings": bindings},
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )


def _frame_with_bindings(*bindings: AxisBindingV1) -> object:
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    meta = _meta_with_bindings(*bindings)
    return MetricFrame(_df=pd.DataFrame(), meta=meta)  # type: ignore[arg-type]


def test_metric_time_axis_uses_bindings_only() -> None:
    frame = _frame_with_bindings(_time_binding(column="created_at", grain="week"))
    column, grain = metric_time_axis(frame)  # type: ignore[arg-type]
    assert column == "created_at"
    assert grain == "week"


def test_metric_time_axis_defaults_grain_to_day() -> None:
    frame = _frame_with_bindings(_time_binding(column="created_at", grain=None))
    column, grain = metric_time_axis(frame)  # type: ignore[arg-type]
    assert column == "created_at"
    assert grain == "day"


def test_metric_dimension_columns_uses_bindings_only() -> None:
    frame = _frame_with_bindings(
        _time_binding(column="created_at"),
        _dim_binding(column="region"),
        _dim_binding(column="channel"),
    )
    columns = metric_dimension_columns(frame)  # type: ignore[arg-type]
    assert columns == ["region", "channel"]


def test_metric_time_axis_ignores_legacy_axes_dict() -> None:
    """The compact ``axes`` dict must never be a fallback axis source."""
    # A frame whose legacy axes dict claims a column but whose typed bindings
    # disagree must resolve from the typed binding only.
    frame = _frame_with_bindings(
        _time_binding(column="created_at", grain="week"),
        _dim_binding(column="region"),
    )
    # Inject a conflicting legacy axes dict: bindings say created_at/week,
    # legacy says a different column/grain. The typed authority must win.
    frame.meta.axes["time"] = {  # type: ignore[attr-defined]
        "role": "time",
        "column": "legacy_ts",
        "grain": "month",
    }
    column, grain = metric_time_axis(frame)  # type: ignore[arg-type]
    assert column == "created_at"
    assert grain == "week"


# ---------------------------------------------------------------------------
# measure_bindings is the typed authority for measure semantics.
# ---------------------------------------------------------------------------


def test_measure_bindings_arity_mismatch_rejected() -> None:
    import sys

    import pytest

    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from shared_fixtures import make_test_multi_metric_contract

    contract = make_test_multi_metric_contract(
        "sales.revenue",
        "sales.order_count",
        axes={"time": {"role": "time", "column": "created_at"}},
    )
    # Two identities but one binding -> validator must reject.
    with pytest.raises(ValueError, match="count must match"):
        MetricFrameMeta(
            kind="metric_frame",
            ref="frame_1",
            session_id="sess",
            project_root="/tmp",
            produced_by_job=None,
            created_at=__import__("datetime").datetime.now(UTC),
            row_count=0,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="test",
                        job_ref=None,
                        inputs=[],
                        params_digest="x",
                    )
                ]
            ),
            **contract,
            axes={},
            measure={},
            measures=[],
            measure_bindings=(
                MeasureBindingV1(
                    identity=contract["metric_identities"][0],
                    value_column="revenue",
                    display_name="revenue",
                ),
            ),
            window=None,
            where={},
            semantic_kind="time_series",
            semantic_model="sales",
        )


def test_measure_bindings_identity_mismatch_rejected() -> None:
    import sys

    import pytest

    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from shared_fixtures import make_test_metric_contract

    contract = make_test_metric_contract(
        __import__("pandas").DataFrame(),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "created_at"}},
        where={},
    )
    # Binding identity must equal the metric identity; an unrelated identity is
    # rejected even though the arity count matches.
    with pytest.raises(ValueError, match="does not match"):
        MetricFrameMeta(
            kind="metric_frame",
            ref="frame_1",
            session_id="sess",
            project_root="/tmp",
            produced_by_job=None,
            created_at=__import__("datetime").datetime.now(UTC),
            row_count=0,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="test",
                        job_ref=None,
                        inputs=[],
                        params_digest="x",
                    )
                ]
            ),
            **contract,
            axes={},
            measure={"name": "revenue"},
            measure_bindings=(
                MeasureBindingV1(
                    identity=_catalog_identity("sales.orders_count"),
                    value_column="value",
                    display_name="orders",
                ),
            ),
            window=None,
            where={},
            semantic_kind="time_series",
            semantic_model="sales",
        )


# ---------------------------------------------------------------------------
# measures_meta() must expose the same closed key set from both branches and
# must carry unit_state (issue #54 P1 regression: typed branch dropped it).
# ---------------------------------------------------------------------------


def _metric_meta_holder(
    *,
    measure_bindings: tuple[MeasureBindingV1, ...] = (),
    measures: list[dict] | None = None,
    unit_state: object | None = None,
) -> object:
    import sys
    from datetime import datetime

    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from shared_fixtures import make_test_metric_contract

    contract = make_test_metric_contract(
        __import__("pandas").DataFrame(),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "created_at"}},
        where={},
    )
    return MetricFrameMeta(
        kind="metric_frame",
        ref="frame_1",
        session_id="sess",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="test",
                    job_ref=None,
                    inputs=[],
                    params_digest="x",
                )
            ]
        ),
        **contract,
        axes={},
        measure={"name": "revenue"},
        measures=measures,
        measure_bindings=measure_bindings,
        unit_state=unit_state,
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )


def _unit_state_unknown() -> object:
    from marivo.semantic.unit_algebra import UnknownUnitV2

    return UnknownUnitV2(schema="metric-unit-unknown/v2")


def test_measures_meta_key_sets_closed_equality_across_branches() -> None:
    """All three branches must expose the same closed key set.

    Issue #54 P1 regression: the typed branch dropped ``unit_state``. Pin the
    full key set on all three branches (typed bindings / compact ``measures`` /
    single ``measure`` fallback) so a future omission fails loudly.
    """
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    typed_meta = _metric_meta_holder(
        measure_bindings=(
            MeasureBindingV1(
                identity=_catalog_identity("sales.revenue"),
                value_column="value",
                display_name="revenue",
                unit="CNY",
                unit_state=_unit_state_unknown(),
                additivity="additive",
                reaggregatable=True,
            ),
        )
    )
    legacy_meta = _metric_meta_holder(
        measures=[
            {
                "metric_id": "sales.revenue",
                "name": "revenue",
                "column": "value",
                "unit": "CNY",
                "unit_state": None,
                "additivity": "additive",
                "aggregation": None,
                "status_time_dimension": None,
                "reaggregatable": True,
            }
        ]
    )
    # Third branch: no bindings, no compact measures — falls back to the single
    # ``measure`` dict and top-level meta scalars.
    single_measure_meta = _metric_meta_holder(
        measures=None,
        unit_state=_unit_state_unknown(),
    )
    expected_measures_keys = {
        "metric_id",
        "name",
        "column",
        "unit",
        "unit_state",
        "additivity",
        "aggregation",
        "status_time_dimension",
        "reaggregatable",
        "cumulative",
    }
    typed_keys = set(MetricFrame(_df=pd.DataFrame(), meta=typed_meta).measures_meta()[0])
    legacy_keys = set(MetricFrame(_df=pd.DataFrame(), meta=legacy_meta).measures_meta()[0])
    single_keys = set(MetricFrame(_df=pd.DataFrame(), meta=single_measure_meta).measures_meta()[0])
    assert typed_keys == legacy_keys == single_keys == expected_measures_keys


def test_measures_meta_typed_branch_carries_unit_state() -> None:
    """The typed branch must surface the binding's unit_state, not drop it."""
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    meta = _metric_meta_holder(
        measure_bindings=(
            MeasureBindingV1(
                identity=_catalog_identity("sales.revenue"),
                value_column="value",
                display_name="revenue",
                unit="CNY",
                unit_state=_unit_state_unknown(),
                additivity="additive",
                reaggregatable=True,
            ),
        )
    )
    entry = MetricFrame(_df=pd.DataFrame(), meta=meta).measures_meta()[0]
    assert entry["unit_state"] == {"schema": "metric-unit-unknown/v2"}


def test_measures_meta_single_measure_branch_carries_unit_state() -> None:
    """The third branch (single ``measure`` fallback) must also surface
    ``unit_state`` — the P3 sibling of the P1 leak."""
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    meta = _metric_meta_holder(
        measures=None,
        unit_state=_unit_state_unknown(),
    )
    entry = MetricFrame(_df=pd.DataFrame(), meta=meta).measures_meta()[0]
    assert entry["unit_state"] == {"schema": "metric-unit-unknown/v2"}


def test_legacy_meta_without_bindings_remains_v7_compatible() -> None:
    """A legacy v7 artifact (no measure_bindings) must keep loading.

    Issue #54 P2-2: ``measure_bindings`` defaults to ``()`` so an old v7 frame
    that only persisted the compact ``measures`` dict must not be treated as
    corrupt. Its display projection stays on the legacy branch and the key set
    is normalized to the same closed set as the typed branch.
    """
    import pandas as pd

    from marivo.analysis.frames.metric import MetricFrame

    legacy_meta = _metric_meta_holder(
        measures=[
            {
                "metric_id": "sales.revenue",
                "name": "revenue",
                "column": "value",
                "unit": "CNY",
                "unit_state": {"schema": "metric-unit-unknown/v2"},
                "additivity": "additive",
                "aggregation": None,
                "status_time_dimension": None,
                "reaggregatable": True,
            }
        ]
    )
    assert legacy_meta.measure_bindings == ()
    frame = MetricFrame(_df=pd.DataFrame(), meta=legacy_meta)
    entry = frame.measures_meta()[0]
    assert entry["unit_state"] == {"schema": "metric-unit-unknown/v2"}
    assert set(entry) == {
        "metric_id",
        "name",
        "column",
        "unit",
        "unit_state",
        "additivity",
        "aggregation",
        "status_time_dimension",
        "reaggregatable",
        "cumulative",
    }
    assert entry["cumulative"] is None
