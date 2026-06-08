from __future__ import annotations

from pathlib import Path

import pytest

from marivo.analysis.publish import (
    DataPolicy,
    Dataset,
    DatasetMetadata,
    Flow,
    FlowStep,
    GroundedClaim,
    Grounding,
    MarivoReportArtifact,
    ReportBlock,
    ReportChartSpec,
    ReportColumn,
    ReportManifest,
    ReportMetric,
    ReportSection,
    ReportSpec,
    SourceProvenance,
)


def _valid_artifact() -> MarivoReportArtifact:
    dataset = Dataset(
        dataset_id="headline_metrics",
        metadata=DatasetMetadata(
            dataset_id="headline_metrics",
            grain="overall",
            row_count=1,
            truncated=False,
            source_artifacts=("artifact_observe_1",),
            source_provenance=SourceProvenance(
                generated_from="intent",
                query_summary="Observed revenue for the requested window.",
                semantic_refs=("sales.revenue",),
                sql_status="not_applicable",
                sql_reason="Typed intent did not expose SQL.",
            ),
            metric_definitions=("sales.revenue = sum(order_amount)",),
            filters=(),
            data_policy=DataPolicy(),
        ),
        rows=({"metric": "revenue", "value": 125.0},),
    )
    return MarivoReportArtifact(
        manifest=ReportManifest(
            report_id="revenue_review",
            export_id="exp_20260605_120000",
            title="Revenue Review",
            created_at="2026-06-05T12:00:00Z",
            marivo_version="0.0.test",
            artifact_count=1,
            evidence_status="complete",
            data_policy=DataPolicy(),
        ),
        report_spec=ReportSpec(
            title="Revenue Review",
            sections=(
                ReportSection(
                    section_id="exec",
                    section_type="executive_summary",
                    title="Executive Summary",
                    blocks=(
                        ReportBlock(
                            block_id="exec_text",
                            block_type="markdown",
                            text="Revenue is up in the reviewed window.",
                        ),
                        ReportBlock(
                            block_id="kpis",
                            block_type="metric_strip",
                            dataset_id="headline_metrics",
                            value_refs=("headline_metrics[0].value",),
                            narrative_ref="exec_text",
                        ),
                    ),
                ),
                ReportSection(
                    section_id="caveats",
                    section_type="caveat",
                    title="Caveats",
                    blocks=(
                        ReportBlock(
                            block_id="caveat_text",
                            block_type="markdown",
                            text="No material caveats were found.",
                        ),
                    ),
                ),
            ),
        ),
        flow=Flow(
            steps=(
                FlowStep(
                    step_id="step_observe",
                    order=1,
                    kind="intent",
                    description="Observe revenue for the requested window.",
                    output_artifacts=("artifact_observe_1",),
                    semantic_refs=("sales.revenue",),
                    evidence_status="complete",
                    query_summary="Observed revenue for the requested window.",
                ),
            ),
        ),
        grounding=Grounding(
            claims=(
                GroundedClaim(
                    claim_id="claim_revenue_up",
                    text_template="Revenue is {value}.",
                    value_refs=("headline_metrics[0].value",),
                    section_id="exec",
                    grounding_type="evidence_backed",
                    evidence_status="complete",
                    supporting_artifacts=("artifact_observe_1",),
                    supporting_steps=("step_observe",),
                    supporting_datasets=("headline_metrics",),
                    source_refs=("sales.revenue",),
                    confidence_scope="Requested window only.",
                ),
            ),
        ),
        datasets={"headline_metrics": dataset},
        evidence={"artifact_observe_1": {"summary": "Revenue observation."}},
    )


def test_validate_report_artifact_accepts_valid_package() -> None:
    from marivo.analysis.publish import validate_report_artifact

    result = validate_report_artifact(_valid_artifact())

    assert result.ok is True
    assert result.issues == ()


def test_validate_report_artifact_accepts_flow_declared_artifacts_without_evidence() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact().model_copy(update={"evidence": {}})

    result = validate_report_artifact(artifact)

    assert result.ok is True
    assert result.issues == ()


@pytest.mark.parametrize(
    "step_evidence_status",
    ["partial", "unavailable"],
)
def test_validate_report_artifact_rejects_complete_manifest_with_incomplete_flow_step(
    step_evidence_status: str,
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (artifact.report_spec.sections[0],)}
            ),
            "flow": artifact.flow.model_copy(
                update={
                    "steps": (
                        artifact.flow.steps[0].model_copy(
                            update={"evidence_status": step_evidence_status}
                        ),
                    )
                }
            ),
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert {"evidence_status", "partial_evidence_visibility"}.issubset(
        {issue.check for issue in result.issues}
    )


@pytest.mark.parametrize(
    "claim_evidence_status",
    ["partial", "unavailable"],
)
def test_validate_report_artifact_rejects_complete_manifest_with_incomplete_claim(
    claim_evidence_status: str,
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (artifact.report_spec.sections[0],)}
            ),
            "grounding": artifact.grounding.model_copy(
                update={
                    "claims": (
                        artifact.grounding.claims[0].model_copy(
                            update={"evidence_status": claim_evidence_status}
                        ),
                    )
                }
            ),
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert {"evidence_status", "partial_evidence_visibility"}.issubset(
        {issue.check for issue in result.issues}
    )


def test_validate_report_artifact_accepts_partial_manifest_with_visible_child_caveat() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    artifact = artifact.model_copy(
        update={
            "manifest": artifact.manifest.model_copy(update={"evidence_status": "partial"}),
            "flow": artifact.flow.model_copy(
                update={
                    "steps": (
                        artifact.flow.steps[0].model_copy(update={"evidence_status": "partial"}),
                    )
                }
            ),
            "grounding": artifact.grounding.model_copy(
                update={
                    "claims": (
                        artifact.grounding.claims[0].model_copy(
                            update={"evidence_status": "unavailable"}
                        ),
                    )
                }
            ),
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is True
    assert {
        issue.check
        for issue in result.issues
        if issue.check in {"evidence_status", "partial_evidence_visibility"}
    } == set()


@pytest.mark.parametrize(
    ("mutator", "checks"),
    [
        (
            lambda artifact: artifact.model_copy(
                update={"report_spec": artifact.report_spec.model_copy(update={"sections": ()})}
            ),
            ("required_sections", "section_ref", "executive_claim_grounding"),
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0].model_copy(
                                    update={
                                        "blocks": (
                                            artifact.report_spec.sections[0].blocks[0],
                                            artifact.report_spec.sections[0]
                                            .blocks[1]
                                            .model_copy(update={"dataset_id": "missing_dataset"}),
                                        )
                                    }
                                ),
                            )
                        }
                    )
                }
            ),
            ("dataset_ref",),
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "grounding": artifact.grounding.model_copy(
                        update={
                            "claims": (
                                artifact.grounding.claims[0].model_copy(
                                    update={"supporting_steps": ("missing_step",)}
                                ),
                            )
                        }
                    )
                }
            ),
            ("step_ref",),
        ),
    ],
)
def test_validate_report_artifact_rejects_broken_contract(mutator, checks: tuple[str, ...]) -> None:
    from marivo.analysis.publish import validate_report_artifact

    result = validate_report_artifact(mutator(_valid_artifact()))

    assert result.ok is False
    assert tuple(issue.check for issue in result.issues) == checks
    assert {issue.severity for issue in result.issues} == {"error"}


def _artifact_with_visual_block(block: ReportBlock) -> MarivoReportArtifact:
    artifact = _valid_artifact()
    visual_section = ReportSection(
        section_id="visual",
        section_type="finding",
        title="Visual Detail",
        blocks=(block,),
    )
    return artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (*artifact.report_spec.sections, visual_section)}
            )
        }
    )


def test_validate_report_artifact_rejects_report_metric_value_ref() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    exec_section = artifact.report_spec.sections[0]
    metric_block = exec_section.blocks[1].model_copy(
        update={
            "metrics": (
                ReportMetric(
                    label="Missing metric",
                    value_ref="headline_metrics[0].missing",
                ),
            )
        }
    )
    exec_section = exec_section.model_copy(
        update={"blocks": (exec_section.blocks[0], metric_block)}
    )
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (exec_section, artifact.report_spec.sections[1])}
            )
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert ("value_ref", "report_spec.sections[0].blocks[1].metrics[0].value_ref") in {
        (issue.check, issue.location) for issue in result.issues
    }


def test_validate_report_artifact_rejects_missing_chart_spec() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _artifact_with_visual_block(
        ReportBlock(
            block_id="trend_chart",
            block_type="chart",
            dataset_id="headline_metrics",
            narrative_ref="exec_text",
        )
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert ("visual_spec", "report_spec.sections[2].blocks[0].chart") in {
        (issue.check, issue.location) for issue in result.issues
    }


@pytest.mark.parametrize(
    ("chart", "expected"),
    [
        (
            ReportChartSpec(type="bar", fields={"x": "metric"}),
            ("visual_spec", "report_spec.sections[2].blocks[0].chart.fields"),
        ),
        (
            ReportChartSpec(type="bar", fields={"x": "metric", "y": "missing"}),
            ("visual_ref", "report_spec.sections[2].blocks[0].chart.fields.y"),
        ),
    ],
)
def test_validate_report_artifact_rejects_invalid_chart_fields(
    chart: ReportChartSpec,
    expected: tuple[str, str],
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _artifact_with_visual_block(
        ReportBlock(
            block_id="trend_chart",
            block_type="chart",
            dataset_id="headline_metrics",
            narrative_ref="exec_text",
            chart=chart,
        )
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert expected in {(issue.check, issue.location) for issue in result.issues}


def test_validate_report_artifact_rejects_table_column_key_without_dataset_field() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _artifact_with_visual_block(
        ReportBlock(
            block_id="detail_table",
            block_type="table",
            dataset_id="headline_metrics",
            narrative_ref="exec_text",
            columns=(ReportColumn(key="missing", label="Missing"),),
        )
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert ("visual_ref", "report_spec.sections[2].blocks[0].columns[0].key") in {
        (issue.check, issue.location) for issue in result.issues
    }


@pytest.mark.parametrize(
    ("mutator", "location"),
    [
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0],
                                artifact.report_spec.sections[1].model_copy(
                                    update={"section_id": "exec"}
                                ),
                            )
                        }
                    )
                }
            ),
            "report_spec.sections[1].section_id",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0],
                                artifact.report_spec.sections[1].model_copy(
                                    update={
                                        "blocks": (
                                            artifact.report_spec.sections[1]
                                            .blocks[0]
                                            .model_copy(update={"block_id": "exec_text"}),
                                        )
                                    }
                                ),
                            )
                        }
                    )
                }
            ),
            "report_spec.sections[1].blocks[0].block_id",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "flow": artifact.flow.model_copy(
                        update={
                            "steps": (
                                artifact.flow.steps[0],
                                artifact.flow.steps[0].model_copy(
                                    update={
                                        "order": 2,
                                        "description": "Repeat the revenue observation.",
                                    }
                                ),
                            )
                        }
                    )
                }
            ),
            "flow.steps[1].step_id",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "grounding": artifact.grounding.model_copy(
                        update={
                            "claims": (
                                artifact.grounding.claims[0],
                                artifact.grounding.claims[0].model_copy(
                                    update={"text_template": "Revenue remains {value}."}
                                ),
                            )
                        }
                    )
                }
            ),
            "grounding.claims[1].claim_id",
        ),
    ],
)
def test_validate_report_artifact_rejects_duplicate_ids(mutator, location: str) -> None:
    from marivo.analysis.publish import validate_report_artifact

    result = validate_report_artifact(mutator(_valid_artifact()))

    assert result.ok is False
    assert tuple(issue.check for issue in result.issues) == ("duplicate_id",)
    assert result.issues[0].severity == "error"
    assert result.issues[0].location == location


@pytest.mark.parametrize(
    ("mutator", "check"),
    [
        (
            lambda artifact: artifact.model_copy(
                update={
                    "manifest": artifact.manifest.model_copy(update={"evidence_status": "partial"}),
                    "report_spec": artifact.report_spec.model_copy(
                        update={"sections": (artifact.report_spec.sections[0],)}
                    ),
                }
            ),
            "partial_evidence_visibility",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0].model_copy(
                                    update={
                                        "blocks": (
                                            artifact.report_spec.sections[0]
                                            .blocks[1]
                                            .model_copy(
                                                update={
                                                    "value_refs": ("headline_metrics[2].value",)
                                                }
                                            ),
                                        )
                                    }
                                ),
                                artifact.report_spec.sections[1],
                            )
                        }
                    )
                }
            ),
            "value_ref",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0].model_copy(
                                    update={
                                        "blocks": (
                                            artifact.report_spec.sections[0]
                                            .blocks[1]
                                            .model_copy(update={"narrative_ref": "missing_block"}),
                                        )
                                    }
                                ),
                                artifact.report_spec.sections[1],
                            )
                        }
                    )
                }
            ),
            "narrative_ref",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "report_spec": artifact.report_spec.model_copy(
                        update={
                            "sections": (
                                artifact.report_spec.sections[0].model_copy(
                                    update={
                                        "blocks": (
                                            artifact.report_spec.sections[0]
                                            .blocks[1]
                                            .model_copy(update={"claim_refs": ("missing_claim",)}),
                                        )
                                    }
                                ),
                                artifact.report_spec.sections[1],
                            )
                        }
                    )
                }
            ),
            "claim_ref",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "grounding": artifact.grounding.model_copy(
                        update={
                            "claims": (
                                artifact.grounding.claims[0].model_copy(
                                    update={"supporting_artifacts": ("missing_artifact",)}
                                ),
                            )
                        }
                    )
                }
            ),
            "artifact_ref",
        ),
        (
            lambda artifact: artifact.model_copy(
                update={
                    "datasets": {
                        "headline_metrics": artifact.datasets["headline_metrics"].model_copy(
                            update={
                                "metadata": artifact.datasets[
                                    "headline_metrics"
                                ].metadata.model_copy(
                                    update={"source_artifacts": ("missing_artifact",)}
                                )
                            }
                        )
                    }
                }
            ),
            "artifact_ref",
        ),
    ],
)
def test_validate_report_artifact_rejects_additional_invalid_refs(mutator, check: str) -> None:
    from marivo.analysis.publish import validate_report_artifact

    result = validate_report_artifact(mutator(_valid_artifact()))

    assert result.ok is False
    assert check in {issue.check for issue in result.issues}


def test_validate_report_artifact_rejects_dataset_policy_that_weakens_manifest() -> None:
    from marivo.analysis.publish import DataPolicy, validate_report_artifact

    artifact = _valid_artifact()
    dataset = artifact.datasets["headline_metrics"]
    weakened = dataset.model_copy(
        update={
            "metadata": dataset.metadata.model_copy(
                update={
                    "data_policy": DataPolicy(
                        row_level_data="included",
                        frame_snapshots="omitted",
                        authority="dataset_override",
                    )
                }
            )
        }
    )
    artifact = artifact.model_copy(update={"datasets": {"headline_metrics": weakened}})

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert "data_policy" in {issue.check for issue in result.issues}


@pytest.mark.parametrize(
    ("generated_from", "expected_check"),
    [
        ("pandas_scratch", "script_refs"),
        ("promotion", "promotion_ref"),
    ],
)
def test_validate_report_artifact_requires_step_kind_source_metadata(
    generated_from: str, expected_check: str
) -> None:
    from marivo.analysis.publish import SourceProvenance, validate_report_artifact

    artifact = _valid_artifact()
    dataset = artifact.datasets["headline_metrics"]
    bad_source = SourceProvenance(
        generated_from=generated_from,
        query_summary="Produced a dataset.",
        sql_status="not_applicable",
        sql_reason="No SQL was used.",
    )
    artifact = artifact.model_copy(
        update={
            "datasets": {
                "headline_metrics": dataset.model_copy(
                    update={
                        "metadata": dataset.metadata.model_copy(
                            update={"source_provenance": bad_source}
                        )
                    }
                )
            }
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert expected_check in {issue.check for issue in result.issues}


def test_validate_report_artifact_rejects_dataset_source_provenance_step_kind_mismatch() -> None:
    from marivo.analysis.publish import SourceProvenance, validate_report_artifact

    artifact = _valid_artifact()
    dataset = artifact.datasets["headline_metrics"]
    source = SourceProvenance(
        generated_from="pandas_scratch",
        query_summary="Produced a dataset with scratch pandas work.",
        sql_status="not_applicable",
        sql_reason="No SQL was used.",
        script_refs=("analysis.py",),
    )
    artifact = artifact.model_copy(
        update={
            "datasets": {
                "headline_metrics": dataset.model_copy(
                    update={
                        "metadata": dataset.metadata.model_copy(
                            update={"source_provenance": source}
                        )
                    }
                )
            }
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert "source_provenance" in {issue.check for issue in result.issues}


def test_validate_report_artifact_accepts_dataset_source_provenance_step_kind_match() -> None:
    from marivo.analysis.publish import SourceProvenance, validate_report_artifact

    artifact = _valid_artifact()
    dataset = artifact.datasets["headline_metrics"]
    source = SourceProvenance(
        generated_from="intent",
        query_summary="Produced a dataset with typed intent.",
        sql_status="not_applicable",
        sql_reason="Typed intent did not expose SQL.",
    )
    artifact = artifact.model_copy(
        update={
            "datasets": {
                "headline_metrics": dataset.model_copy(
                    update={
                        "metadata": dataset.metadata.model_copy(
                            update={"source_provenance": source}
                        )
                    }
                )
            }
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is True
    assert result.issues == ()


def test_validate_report_artifact_rejects_ungrounded_executive_summary_claim() -> None:
    from marivo.analysis.publish import Grounding, validate_report_artifact

    artifact = _valid_artifact().model_copy(update={"grounding": Grounding(claims=())})

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert "executive_claim_grounding" in {issue.check for issue in result.issues}


def test_validate_report_artifact_rejects_numeric_claim_without_value_ref() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    bad_claim = artifact.grounding.claims[0].model_copy(
        update={"text_template": "Revenue is 125.0.", "value_refs": ()}
    )
    artifact = artifact.model_copy(
        update={"grounding": artifact.grounding.model_copy(update={"claims": (bad_claim,)})}
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert "single_source_number" in {issue.check for issue in result.issues}


def test_validate_report_artifact_rejects_visual_without_adjacent_narrative() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _valid_artifact()
    kpi_block = (
        artifact.report_spec.sections[0].blocks[1].model_copy(update={"narrative_ref": None})
    )
    section = artifact.report_spec.sections[0].model_copy(update={"blocks": (kpi_block,)})
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (section, artifact.report_spec.sections[1])}
            )
        }
    )

    result = validate_report_artifact(artifact)

    assert result.ok is False
    assert "narrative_adjacency" in {issue.check for issue in result.issues}


def _artifact_with_script_refs(
    flow_script_refs: tuple[str, ...] = (),
    provenance_script_refs: tuple[str, ...] = (),
) -> MarivoReportArtifact:
    artifact = _valid_artifact()
    flow = artifact.flow.model_copy(
        update={
            "steps": (artifact.flow.steps[0].model_copy(update={"script_refs": flow_script_refs}),)
        }
    )
    dataset = artifact.datasets["headline_metrics"]
    metadata = dataset.metadata.model_copy(
        update={
            "source_provenance": dataset.metadata.source_provenance.model_copy(
                update={"script_refs": provenance_script_refs}
            )
        }
    )
    datasets = {
        "headline_metrics": dataset.model_copy(update={"metadata": metadata}),
    }
    return artifact.model_copy(update={"flow": flow, "datasets": datasets})


def test_validate_report_artifact_skips_script_existence_without_script_root() -> None:
    from marivo.analysis.publish import validate_report_artifact

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/does_not_exist.py",),
        provenance_script_refs=("scripts/also_missing.py",),
    )

    result = validate_report_artifact(artifact)

    assert result.ok is True
    assert result.issues == ()


def test_validate_report_artifact_accepts_existing_flow_step_scripts(
    tmp_path: Path,
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "step_observe.py").write_text("# observe\n", encoding="utf-8")

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_observe.py",),
    )

    result = validate_report_artifact(artifact, script_root=tmp_path)

    assert result.ok is True
    assert result.issues == ()


def test_validate_report_artifact_rejects_missing_flow_step_script(
    tmp_path: Path,
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "step_observe.py").write_text("# observe\n", encoding="utf-8")

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_observe.py", "scripts/ghost.py"),
    )

    result = validate_report_artifact(artifact, script_root=tmp_path)

    assert result.ok is False
    missing = [issue for issue in result.issues if issue.check == "script_ref_missing"]
    assert len(missing) == 1
    assert "scripts/ghost.py" in missing[0].message
    assert missing[0].location == "flow.steps[0].script_refs[1]"


def test_validate_report_artifact_rejects_missing_source_provenance_script(
    tmp_path: Path,
) -> None:
    from marivo.analysis.publish import validate_report_artifact

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "real.py").write_text("# real\n", encoding="utf-8")

    artifact = _artifact_with_script_refs(
        provenance_script_refs=("scripts/real.py", "scripts/fabricated.py"),
    )

    result = validate_report_artifact(artifact, script_root=tmp_path)

    assert result.ok is False
    missing = [issue for issue in result.issues if issue.check == "script_ref_missing"]
    assert len(missing) == 1
    assert "scripts/fabricated.py" in missing[0].message
    assert missing[0].location == (
        "datasets.headline_metrics.metadata.source_provenance.script_refs[1]"
    )
