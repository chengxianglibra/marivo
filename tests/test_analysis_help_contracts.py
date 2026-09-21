"""Independent witnesses for exact analysis Help content and executable examples."""

from __future__ import annotations

import dataclasses
import inspect
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._capabilities.model import ARTIFACT_FAMILIES
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis._capabilities.render import (
    _extract_example,
    _rendered_help_targets,
    _resolve_callable,
)
from marivo.analysis._capabilities.surface import TYPE_REGISTRY
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.candidate import CandidateSet
from tests.shared_fixtures import (
    bootstrap_sales_project_from_template,
    connect_sales_orders,
    rendered_help,
    sales_backends,
)


def _example(target: str) -> str:
    descriptor = REGISTRY.by_id(target)
    function = _resolve_callable(descriptor)
    doc_example = _extract_example(inspect.getdoc(function) or "")
    if doc_example:
        return "\n".join(
            line.removeprefix(">>> ").removeprefix("... ") for line in doc_example.splitlines()
        )
    assert len(descriptor.additional_examples) == 1
    return descriptor.additional_examples[0].code


def test_absolute_window_entrypoint_matches_constructor_and_scope_stays_separate() -> None:
    text = rendered_help("AbsoluteWindow", owner="analysis")
    assert "Entrypoint: mv.AbsoluteWindow(...)" in text
    assert "Signature: AbsoluteWindow(" in text
    assert "session.observe(grain=...)" in text
    namespace = {"mv": mv}
    exec(_example("AbsoluteWindow"), namespace)
    assert isinstance(namespace["window"], mv.AbsoluteWindow)
    exec(_example("time_scope"), namespace)
    assert isinstance(namespace["scope"], mv.TimeScope)
    assert set(inspect.signature(mv.time_scope).parameters) == {"start", "end"}
    with pytest.raises(TypeError, match="grain"):
        # Exercise the incorrect call previously suggested by the mixed Help page.
        invalid_arguments = {"start": "2026-07-01", "end": "2026-08-01", "grain": "day"}
        mv.time_scope(**invalid_arguments)


def test_sampling_help_matches_closed_values_and_validation_bounds() -> None:
    text = rendered_help("SamplingPolicy", owner="analysis")
    assert "segment_key" in text
    assert "window_bucket" in text
    assert ">= 2" in text
    assert "drop_pair" in text
    namespace = {"mv": mv}
    exec(_example("SamplingPolicy"), namespace)
    assert namespace["sampling"].pairing == "segment_key"
    assert mv.SamplingPolicy(min_n=2).min_n == 2
    for kwargs in ({"min_n": 1}, {"pairing": "position"}):
        with pytest.raises(ValidationError):
            mv.SamplingPolicy.model_validate(kwargs)


def test_parameter_semantics_survive_rendering() -> None:
    observe = rendered_help("observe", owner="analysis")
    assert "list/tuple/set" in observe and "between" in observe
    assert "Omit, pass ``None``, or pass ``[]``" in observe
    assert "including explicit None" in observe
    forecast = rendered_help("forecast", owner="analysis")
    assert "day=7, week=52, month=12, quarter=4" in forecast
    assert "(0, 1)" in forecast
    assert "missing" in forecast and "time buckets" in forecast


def test_public_dataclass_fields_and_page_protocol_are_disclosed() -> None:
    for cls, target in TYPE_REGISTRY.items():
        if target in ARTIFACT_FAMILIES or not dataclasses.is_dataclass(cls):
            continue
        text = rendered_help(target, owner="analysis")
        for field in dataclasses.fields(cls):
            if not field.name.startswith("_"):
                assert f"    {field.name}: " in text, (target, field.name)
        assert "Call marivo.help" not in text
    for target in ("FindingPage", "RunPage"):
        text = rendered_help(target, owner="analysis")
        for field in ("items", "limit", "has_more", "next_cursor"):
            assert f"{field}:" in text
        assert "same filters" in text and "next_cursor is None" in text
        assert ".show()" in text and ".render()" in text
    assert "items: tuple[Finding, ...]" in rendered_help("FindingPage", owner="analysis")
    assert "items: tuple[IncompleteRun | SucceededRun | FailedRun, ...]" in rendered_help(
        "RunPage", owner="analysis"
    )
    assert "artifact.findings" in rendered_help("FindingPage", owner="analysis")
    assert "artifact.finding" in rendered_help("Finding", owner="analysis")
    assert "session.get_run" in rendered_help("FailedRun", owner="analysis")


@pytest.mark.parametrize(
    ("target", "identity"),
    (
        ("session.runs", "bound_session (no identity argument)"),
        ("session.get_run", "run_id within the bound Session"),
        ("session.artifact", "artifact_ref within the bound Session"),
        ("session.graph", "bound_session with optional artifact_ref"),
    ),
)
def test_recovery_identity_is_exact(target: str, identity: str) -> None:
    text = rendered_help(target, owner="analysis")
    assert f"Identity input: {identity}" in text
    assert "session_id_or_artifact_ref_or_run_id" not in text


@contextmanager
def _example_session(project: Path) -> Iterator[mv.Session]:
    bootstrap_sales_project_from_template(project)
    path = project / "models/semantic/sales/datasets.py"
    path.write_text(
        path.read_text()
        + '\nms.dimension_column(name="country", entity=orders, column="country")\n'
        + 'ms.dimension_column(name="channel", entity=orders, column="channel")\n'
    )
    connection = connect_sales_orders()
    connection.raw_sql(
        "CREATE OR REPLACE TABLE orders AS SELECT "
        "TIMESTAMP '2026-07-01' + i * INTERVAL '1 day' AS created_at, "
        "CAST(10 + i AS DOUBLE) AS amount, 'NORTH' AS region, "
        "'US' AS country, 'online' AS channel FROM range(30) AS t(i)"
    )
    try:
        session = mv.session.get_or_create("help-examples", backends=sales_backends(connection))
        yield session
    finally:
        connection.disconnect()


def test_changed_examples_execute_and_public_pages_recover_all_items(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with _example_session(tmp_path) as session:
        namespace = {"mv": mv, "ms": ms, "session": session}
        exec(_example("observe"), namespace)
        frame = namespace["frame"]
        assert isinstance(frame, mv.MetricFrame)
        assert not frame.to_pandas().empty
        namespace["source"] = frame
        for target in ("discover.point_anomalies", "discover.interesting_windows"):
            exec(_example(target), namespace)
            assert isinstance(namespace["candidates"], mv.CandidateSet)
        delta = session.compare(frame, frame)
        namespace.update(source=delta, delta=delta)
        exec(_example("discover.period_shifts"), namespace)
        exec(_example("DeltaFrame.predicted_attribution_shape"), namespace)
        for target in (
            "BaseFrame.show",
            "Session.show",
            "Session.render",
            "boundary.to_pandas",
            "catalog.require",
            "session.current",
        ):
            exec(_example(target), namespace)
        assert namespace["entry"].ref == session.catalog.metrics.get("sales.revenue").ref

        ratio = mv.runtime_metric.ratio(
            ms.ref.metric("sales.revenue"), ms.ref.metric("sales.revenue"), label="revenue_share"
        )
        ratio_frame = session.observe(ratio)
        ratio_delta = session.compare(ratio_frame, ratio_frame)
        for target, parent in (
            ("MetricFrame.components", ratio_frame),
            ("DeltaFrame.components", ratio_delta),
        ):
            namespace["frame"] = parent
            exec(_example(target), namespace)
            assert namespace["components"].meta.parent_ref == parent.ref

        page = delta.findings(limit=1)
        finding_ids = []
        while True:
            finding_ids.extend(item.finding_id for item in page.items)
            if not page.has_more:
                assert page.next_cursor is None
                break
            page = delta.findings(limit=1, cursor=page.next_cursor)
        assert len(finding_ids) == len(set(finding_ids)) == delta.finding_count
        run_page = session.runs(limit=1)
        run_ids = []
        while True:
            run_ids.extend(item.run_id for item in run_page.items)
            if not run_page.has_more:
                assert run_page.next_cursor is None
                break
            run_page = session.runs(limit=1, cursor=run_page.next_cursor)
        assert len(run_ids) == len(set(run_ids)) > 1
        assert session.get_run(run_ids[0]).run_id == run_ids[0]
        # The destructive example is confined to a disposable, isolated Session.
        disposable = mv.session.get_or_create("help-example-delete", use_datasources=False)
        namespace["name"] = disposable.name
        exec(_example("session.delete"), namespace)
        assert mv.session.current() is None


def test_narrowing_examples_require_the_matching_receiver_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with _example_session(tmp_path) as session:
        revenue = session.catalog.metrics.get("sales.revenue")
        country = session.catalog.dimensions.get("sales.orders.country")
        for shape in ("scalar", "segmented", "time_series", "panel"):
            frame = session.observe(
                revenue,
                time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
                grain=mv.grain("day") if shape in {"time_series", "panel"} else None,
                dimensions=[country] if shape in {"segmented", "panel"} else None,
            )
            for family, receiver in (
                ("MetricFrame", frame),
                ("DeltaFrame", session.compare(frame, frame)),
            ):
                namespace = {"frame": receiver}
                exec(_example(f"{family}.as_{shape}"), namespace)
                assert namespace["view"] is receiver
                wrong = "scalar" if shape != "scalar" else "panel"
                with pytest.raises(SemanticKindMismatchError):
                    exec(_example(f"{family}.as_{wrong}"), namespace)
        delta = session.compare(frame, frame)
        attribution = session.attribute(delta, axes=[country])
        candidates = session.discover.point_anomalies(frame)
        # These helpers read shape metadata only. Seed each supported read state;
        # computation of each attribution/discovery strategy is tested separately.
        for shape in ("sum", "ratio_mix", "weighted_mix"):
            receiver = AttributionFrame(
                _df=attribution.to_pandas(),
                meta=attribution.meta.model_copy(update={"method": shape}),
            )
            namespace = {"frame": receiver}
            exec(_example(f"AttributionFrame.as_{shape}"), namespace)
            assert namespace["view"] is receiver
        for shape in (
            "point_anomaly",
            "period_shift",
            "driver_axis",
            "slice",
            "window",
            "cross_sectional_outlier",
        ):
            receiver = CandidateSet(
                _df=candidates.to_pandas(), meta=candidates.meta.model_copy(update={"shape": shape})
            )
            namespace = {"frame": receiver}
            exec(_example(f"CandidateSet.as_{shape}"), namespace)
            assert namespace["view"] is receiver


def test_every_callable_parameter_has_owned_semantics_in_help() -> None:
    for descriptor in REGISTRY.descriptors:
        function = _resolve_callable(descriptor)
        if not callable(function) or isinstance(function, type):
            continue
        text = rendered_help(descriptor.help_target, owner="analysis")
        documented = set(re.findall(r"^    [*]*([a-z_]+):", text, re.MULTILINE))
        parameters = set(inspect.signature(function).parameters) - {"self", "cls"}
        assert parameters <= documented, (descriptor.id, parameters - documented)


def test_all_navigation_and_leaf_links_are_real_targets() -> None:
    for descriptor in REGISTRY.help_descriptors:
        text = rendered_help(descriptor.help_target, owner="analysis")
        for target in _rendered_help_targets(text):
            assert target.canonical_id is not None
            rendered_help(target.canonical_id, owner=target.surface)
