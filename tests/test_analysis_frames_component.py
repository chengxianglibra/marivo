"""ComponentFrame contract and load behavior."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import ComponentFrameUnavailableError
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._runtime import persist_frame
from tests.shared_fixtures import (
    make_test_component_contract,
    make_test_metric_meta_contract,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)


def test_component_frame_meta_kind_and_next_intents():
    meta = ComponentFrameMeta(
        ref="frame_component",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        **make_test_component_contract(
            metric_id="sales.failure_rate",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes={"region": {"role": "dimension", "column": "region"}},
        ),
        composition_kind="ratio",
        semantic_kind="segmented",
        semantic_model="sales",
    )
    frame = ComponentFrame(
        _df=pd.DataFrame(
            {
                "region": ["NORTH"],
                "failed_count": [1.0],
                "total_count": [3.0],
                "failure_rate": [1.0 / 3.0],
            }
        ),
        meta=meta,
    )

    assert meta.kind == "component_frame"
    assert not frame.contract().affordances
    assert frame.to_pandas().iloc[0]["failure_rate"] == pytest.approx(1.0 / 3.0)


def test_load_frame_round_trips_component_frame():
    session = session_attach.get_or_create(name="demo")
    component = ComponentFrame(
        _df=pd.DataFrame(
            {
                "region": ["NORTH"],
                "failed_count": [1.0],
                "total_count": [3.0],
                "failure_rate": [1.0 / 3.0],
            }
        ),
        meta=ComponentFrameMeta(
            ref="frame_component",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref="frame_parent",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components={
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
                axes={"region": {"role": "dimension", "column": "region"}},
            ),
            composition_kind="ratio",
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)

    loaded = session.get_frame(component.ref)

    assert isinstance(loaded, ComponentFrame)
    assert loaded.meta.parent_kind == "metric_frame"
    assert loaded.to_pandas().iloc[0]["total_count"] == pytest.approx(3.0)


def test_metric_frame_components_loads_linked_component_frame():
    session = session_attach.get_or_create(name="demo")
    component = ComponentFrame(
        _df=pd.DataFrame({"failed_count": [1.0], "total_count": [2.0], "failure_rate": [0.5]}),
        meta=ComponentFrameMeta(
            ref="frame_component",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref="frame_metric",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components={
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
                axes={},
            ),
            composition_kind="ratio",
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    parent = MetricFrame(
        _df=pd.DataFrame({"failure_rate": [0.5]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.failure_rate"),
            ref="frame_metric",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes={},
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
            component_ref=component.ref,
            composition={
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        ),
    )

    loaded = parent.components()

    assert isinstance(loaded, ComponentFrame)
    assert loaded.ref == component.ref


def test_ordinary_metric_frame_components_raise_structured_unavailable_error():
    session = session_attach.get_or_create(name="demo")
    parent = MetricFrame(
        _df=pd.DataFrame({"revenue": [100.0]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.revenue"),
            ref="frame_metric",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            axes={},
            measure={"name": "revenue"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert exc_info.value._context["parent_ref"] == "frame_metric"
    assert exc_info.value._context["parent_kind"] == "metric_frame"


def test_component_frame_meta_accepts_time_series_semantic_kind():
    meta = ComponentFrameMeta(
        ref="frame_component_ts",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        **make_test_component_contract(
            metric_id="sales.failure_rate",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes={
                "time": {
                    "role": "time",
                    "column": "bucket_start",
                    "grain": "day",
                    "time_dimension": "order_date",
                }
            },
        ),
        composition_kind="ratio",
        semantic_kind="time_series",
        semantic_model="sales",
    )

    assert meta.semantic_kind == "time_series"


def test_component_frame_meta_accepts_panel_semantic_kind():
    meta = ComponentFrameMeta(
        ref="frame_component_panel",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        **make_test_component_contract(
            metric_id="sales.failure_rate",
            components={
                "numerator": "sales.weighted_score",
                "weight": "sales.weight",
            },
            axes={
                "time": {
                    "role": "time",
                    "column": "bucket_start",
                    "grain": "day",
                    "time_dimension": "order_date",
                },
                "region": {"role": "dimension", "column": "region"},
            },
        ),
        composition_kind="weighted_mean",
        semantic_kind="panel",
        semantic_model="sales",
    )

    assert meta.semantic_kind == "panel"


def _make_component_metric_parent(
    session, *, ref="frame_metric", component_ref=None, composition=None
):
    """Build a persisted parent MetricFrame with the given component sidecar."""
    parent = MetricFrame(
        _df=pd.DataFrame({"failure_rate": [0.5]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.failure_rate"),
            ref=ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes={},
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
            component_ref=component_ref,
            composition=composition,
        ),
    )
    parent.meta = persist_frame(session, parent)
    return parent


def test_metric_frame_components_stale_ref_fails_closed():
    """A stale component_ref must raise, not fall back to a deterministic ref.

    Issue #57: the two-phase random/deterministic lookup silently recovered a
    missing sidecar by re-deriving a deterministic ref. Reject that silent
    recovery: a saved pointer that no longer resolves is a typed error.
    """
    from marivo.analysis.evidence.identity import make_component_artifact_id

    session = session_attach.get_or_create(name="demo")

    parent_artifact_id = "art_abcd1234efgh"
    parent = _make_component_metric_parent(
        session,
        ref=parent_artifact_id,
        component_ref="frame_deadbeef",  # stale ref pointing to nothing
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    # A ComponentFrame DOES exist at the deterministic ref, but the saved
    # pointer must be the only authority — no silent fallback to it.
    det_ref = make_component_artifact_id(parent_artifact_id)
    component = ComponentFrame(
        _df=pd.DataFrame({"a": [1.0], "b": [2.0], "failure_rate": [0.5]}),
        meta=ComponentFrameMeta(
            ref=det_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref=parent_artifact_id,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components={"numerator": "a", "denominator": "b"},
                axes={},
            ),
            composition_kind="ratio",
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert exc_info.value._context["component_ref"] == "frame_deadbeef"
    assert exc_info.value._context["parent_ref"] == parent_artifact_id


def test_metric_frame_components_missing_ref_with_composition_fails_closed():
    """composition alone (admission) without a resolvable ref is a typed error."""
    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        component_ref=None,
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    with pytest.raises(ComponentFrameUnavailableError):
        parent.components()


def test_metric_frame_components_wrong_kind_fails_closed():
    """component_ref resolving to a non-ComponentFrame is a typed error."""
    session = session_attach.get_or_create(name="demo")
    component_ref = "frame_wrong_kind"
    wrong_kind = MetricFrame(
        _df=pd.DataFrame({"revenue": [100.0]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.revenue"),
            ref=component_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            axes={},
            measure={"name": "revenue"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    wrong_kind.meta = persist_frame(session, wrong_kind)
    parent = _make_component_metric_parent(
        session,
        component_ref=component_ref,
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert exc_info.value._context["loaded_kind"] == "metric_frame"


def test_metric_frame_components_corrupt_ref_propagates_corruption():
    """A corrupt sidecar must surface as corruption, not silent recovery.

    Issue #57: cold-start and live reads must behave the same way. A corrupted
    persisted sidecar frame surfaces as FrameCacheCorruptedError (typed,
    with recovery repair), never as a silent deterministic-ref retry.
    """
    from marivo.analysis.errors import FrameCacheCorruptedError

    session = session_attach.get_or_create(name="demo")
    component_ref = "frame_corrupt_comp"
    corrupt = ComponentFrame(
        _df=pd.DataFrame({"a": [1.0], "failure_rate": [0.5]}),
        meta=ComponentFrameMeta(
            ref=component_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref="frame_metric",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components={"numerator": "a", "denominator": "b"},
                axes={},
            ),
            composition_kind="ratio",
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    corrupt.meta = persist_frame(session, corrupt)
    # Corrupt the sidecar by deleting its persisted meta while it remains
    # registered in the store, so load_frame fails closed as corruption
    # rather than "not found" (which would also exercise the fallback).
    meta_path = session._layout.frames_dir / component_ref / "meta.json"
    meta_path.unlink()
    parent = _make_component_metric_parent(
        session,
        component_ref=component_ref,
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    with pytest.raises(FrameCacheCorruptedError):
        parent.components()


def test_legacy_component_graph_ref_artifact_fails_closed_on_load():
    """A pre-#57 artifact carrying component_graph_ref is not migrated.

    Per AGENTS.md ("remove legacy artifacts, aliases, migrations, and
    dual-read compatibility unless explicitly required") and the issue #57
    simplification directive, old v7 artifacts that wrote the removed
    component_graph_ref key must fail closed with a typed error — the operator
    re-runs observe() to regenerate the frame under the single component_ref
    contract. There is no read-side migration.
    """
    import json

    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.session._load import load_frame

    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        ref="frame_legacy_key",
        component_ref="frame_sidecar",
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )
    meta_path = session._layout.frames_dir / parent.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["component_graph_ref"] = "frame_sidecar"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(parent.ref, session=session)

    # The failure must name the removed field, not call the artifact 'corrupt',
    # and the repair must be visible to the agent (issue #57 review P1/P3).
    assert "component_graph_ref" in exc_info.value.message
    assert "no longer in" in exc_info.value.message
    assert exc_info.value.repair is not None
    assert "Re-run observe()" in exc_info.value.repair.action


def test_legacy_graph_only_artifact_fails_closed_on_load():
    """A pre-#57 graph-only artifact (component_ref absent) fails closed.

    Real pre-#57 observe() writes are graph-only: component_ref is absent and
    component_graph_ref carries the sidecar ref. That shape is the one that
    actually hits the fail-closed path, so it must produce the same dedicated
    version-mismatch error as the dual-write shape.
    """
    import json

    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.session._load import load_frame

    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        ref="frame_legacy_graph_only",
        component_ref=None,
        composition=None,
    )
    meta_path = session._layout.frames_dir / parent.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["component_graph_ref"] = "frame_graph_sidecar"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(parent.ref, session=session)

    assert "component_graph_ref" in exc_info.value.message
    assert "no longer in" in exc_info.value.message
    assert exc_info.value.repair is not None
    assert "Re-run observe()" in exc_info.value.repair.action


def test_corrupt_payload_still_reports_corruption():
    """A genuinely corrupt value must keep reporting corruption.

    Issue #57 review P3-2: the corrupt branch is still reachable and must stay
    covered. A payload with a wrong-typed field (no removed fields) is real data
    damage, not a version mismatch — it must say 'corrupt', not 'no longer in'.
    """
    import json

    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.session._load import load_frame

    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        ref="frame_corrupt_payload",
        component_ref="frame_sidecar",
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )
    meta_path = session._layout.frames_dir / parent.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["row_count"] = "not-an-int"  # wrong type, not a removed field
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(parent.ref, session=session)

    assert "corrupt current-schema" in exc_info.value.message
    assert "no longer in" not in exc_info.value.message


def test_removed_field_with_corrupt_value_reports_corruption():
    """When a removed field coexists with a corrupt value, corruption wins.

    Issue #57 review P3-1: the extra-forbidden dispatch must not mask a real
    data problem. A payload carrying component_graph_ref *and* a wrong-typed
    field is genuinely damaged — it must report corruption, not a pure version
    mismatch whose 're-run observe()' repair would paper over the damage.
    """
    import json

    from marivo.analysis.errors import FrameMetaInvalidError
    from marivo.analysis.session._load import load_frame

    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        ref="frame_mixed_corrupt",
        component_ref="frame_sidecar",
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )
    meta_path = session._layout.frames_dir / parent.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["component_graph_ref"] = "frame_sidecar"  # removed field
    payload["row_count"] = "not-an-int"  # corrupt value
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(parent.ref, session=session)

    assert "corrupt current-schema" in exc_info.value.message
    assert "no longer in" not in exc_info.value.message


def test_no_composition_scalar_frame_keeps_inspect_repair():
    """An ordinary scalar frame without composition keeps the original repair.

    Issue #57 review P2-1: the no-ref, no-composition case is the pre-existing
    'this frame type has no components' guidance — it must not be confused with
    the 'declared decomposable but sidecar missing' case.
    """
    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(session)

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert "only available for derived ratio" in exc_info.value.repair.action


def test_declared_composition_missing_sidecar_gets_environment_repair():
    """A frame that declares a composition but has no sidecar is an incomplete
    write, distinct from a scalar frame (issue #57 review P2-1).

    The repair must point at re-running observe to regenerate the sidecar, not
    at the 'this frame type has no components' inspect guidance.
    """
    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        component_ref=None,
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert "no component sidecar was persisted" in exc_info.value.message
    assert "incomplete write" in exc_info.value.repair.action


def test_stale_ref_repair_points_at_reobserve():
    """A stale component_ref repair must instruct re-running observe (issue #57
    review P2-1), not the 'frame type has no components' guidance."""
    session = session_attach.get_or_create(name="demo")
    parent = _make_component_metric_parent(
        session,
        component_ref="frame_deadbeef",
        composition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
    )

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert "no longer available on disk" in exc_info.value.message
    assert "Re-run observe()" in exc_info.value.repair.action
