"""Lineage + LineageStep dataclasses."""

from marivo.analysis_py.lineage import Lineage, LineageStep


def test_lineage_step_construction():
    step = LineageStep(
        intent="observe",
        job_ref="job_abc",
        inputs=[],
        params_digest="sha256:deadbeef",
    )
    assert step.intent == "observe"
    assert step.job_ref == "job_abc"
    assert step.inputs == []


def test_lineage_default_empty():
    lin = Lineage()
    assert lin.steps == []
    assert lin.external_inputs == []


def test_lineage_with_steps():
    step = LineageStep(intent="observe", job_ref="job_1", inputs=[], params_digest="x")
    lin = Lineage(steps=[step])
    assert len(lin.steps) == 1


def test_external_inputs_track_from_dataframe_entries():
    lin = Lineage(external_inputs=["frame_abc"])
    assert "frame_abc" in lin.external_inputs


def test_lineage_step_external_marker_sets_job_ref_none():
    step = LineageStep(
        intent="from_dataframe",
        job_ref=None,
        inputs=[],
        params_digest="external",
    )
    assert step.job_ref is None


def test_compose_concatenates_steps():
    a = Lineage(steps=[LineageStep(intent="observe", job_ref="j1", inputs=[], params_digest="a")])
    b = Lineage(steps=[LineageStep(intent="observe", job_ref="j2", inputs=[], params_digest="b")])
    combined = Lineage.compose(
        a,
        b,
        new_step=LineageStep(
            intent="compare",
            job_ref="j3",
            inputs=["frame_x", "frame_y"],
            params_digest="c",
        ),
    )
    assert [s.intent for s in combined.steps] == ["observe", "observe", "compare"]


def test_compose_preserves_external_inputs():
    a = Lineage(external_inputs=["frame_a"])
    b = Lineage(external_inputs=["frame_b"])
    combined = Lineage.compose(
        a,
        b,
        new_step=LineageStep(
            intent="compare",
            job_ref="j",
            inputs=[],
            params_digest="x",
        ),
    )
    assert set(combined.external_inputs) == {"frame_a", "frame_b"}
