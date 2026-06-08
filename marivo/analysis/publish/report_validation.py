"""Deterministic cross-reference validation for report artifact packages."""

from __future__ import annotations

import re
from pathlib import Path

from marivo.analysis.publish.report_models import (
    MarivoReportArtifact,
    ReportBlock,
    ReportPackageValidationIssue,
    ReportPackageValidationResult,
)

_VALUE_REF_RE = re.compile(
    r"^(?P<dataset>[A-Za-z0-9_-]+)\[(?P<row>\d+)\]\.(?P<field>[A-Za-z0-9_]+)$"
)
_NUMERIC_LITERAL_RE = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?%?")
_VISUAL_BLOCK_TYPES = {"metric_strip", "chart", "table"}


def _issue(check: str, message: str, location: str | None = None) -> ReportPackageValidationIssue:
    return ReportPackageValidationIssue(
        severity="error",
        check=check,
        message=message,
        location=location,
    )


def _incomplete_child_evidence_statuses(
    artifact: MarivoReportArtifact,
) -> tuple[tuple[str, str], ...]:
    statuses: list[tuple[str, str]] = []
    for step_index, step in enumerate(artifact.flow.steps):
        if step.evidence_status != "complete":
            statuses.append((step.evidence_status, f"flow.steps[{step_index}].evidence_status"))
    for claim_index, claim in enumerate(artifact.grounding.claims):
        if claim.evidence_status != "complete":
            statuses.append(
                (claim.evidence_status, f"grounding.claims[{claim_index}].evidence_status")
            )
    return tuple(statuses)


def _validate_required_sections(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    section_types = {section.section_type for section in artifact.report_spec.sections}
    incomplete_child_evidence = _incomplete_child_evidence_statuses(artifact)
    if "executive_summary" not in section_types:
        issues.append(
            _issue(
                "required_sections",
                "report_spec requires an executive_summary section",
                "report_spec.sections",
            )
        )
    if artifact.manifest.evidence_status == "complete" and incomplete_child_evidence:
        first_status, first_location = incomplete_child_evidence[0]
        issues.append(
            _issue(
                "evidence_status",
                f"manifest evidence_status cannot be complete when child evidence is {first_status}",
                first_location,
            )
        )
    if (
        artifact.manifest.evidence_status != "complete" or incomplete_child_evidence
    ) and "caveat" not in section_types:
        issues.append(
            _issue(
                "partial_evidence_visibility",
                "report_spec requires a caveat section when evidence is partial or unavailable",
                "report_spec.sections",
            )
        )


def _validate_duplicate_ids(
    label: str,
    ids: tuple[tuple[str, str], ...],
    issues: list[ReportPackageValidationIssue],
) -> None:
    seen: set[str] = set()
    for value, location in ids:
        if value in seen:
            issues.append(
                _issue(
                    "duplicate_id",
                    f"duplicate {label} id {value!r}",
                    location,
                )
            )
        else:
            seen.add(value)


def _validate_report_ids(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    _validate_duplicate_ids(
        "section",
        tuple(
            (section.section_id, f"report_spec.sections[{section_index}].section_id")
            for section_index, section in enumerate(artifact.report_spec.sections)
        ),
        issues,
    )
    _validate_duplicate_ids(
        "block",
        tuple(
            (
                block.block_id,
                f"report_spec.sections[{section_index}].blocks[{block_index}].block_id",
            )
            for section_index, section in enumerate(artifact.report_spec.sections)
            for block_index, block in enumerate(section.blocks)
        ),
        issues,
    )
    _validate_duplicate_ids(
        "flow step",
        tuple(
            (step.step_id, f"flow.steps[{step_index}].step_id")
            for step_index, step in enumerate(artifact.flow.steps)
        ),
        issues,
    )
    _validate_duplicate_ids(
        "claim",
        tuple(
            (claim.claim_id, f"grounding.claims[{claim_index}].claim_id")
            for claim_index, claim in enumerate(artifact.grounding.claims)
        ),
        issues,
    )


def _validate_value_ref(
    artifact: MarivoReportArtifact,
    value_ref: str,
    location: str,
    issues: list[ReportPackageValidationIssue],
) -> None:
    match = _VALUE_REF_RE.match(value_ref)
    if not match:
        issues.append(
            _issue(
                "value_ref",
                f"value ref {value_ref!r} must use dataset[row].field syntax",
                location,
            )
        )
        return

    dataset_id = match.group("dataset")
    row_index = int(match.group("row"))
    field = match.group("field")
    dataset = artifact.datasets.get(dataset_id)
    if dataset is None:
        issues.append(
            _issue(
                "dataset_ref",
                f"value ref {value_ref!r} references missing dataset {dataset_id!r}",
                location,
            )
        )
        return

    if row_index >= len(dataset.rows):
        issues.append(
            _issue(
                "value_ref",
                f"value ref {value_ref!r} references missing row {row_index}",
                location,
            )
        )
        return

    if field not in dataset.rows[row_index]:
        issues.append(
            _issue(
                "value_ref",
                f"value ref {value_ref!r} references missing field {field!r}",
                location,
            )
        )


def _validate_report_refs(
    artifact: MarivoReportArtifact,
    section_ids: set[str],
    block_ids: set[str],
    step_ids: set[str],
    issues: list[ReportPackageValidationIssue],
) -> None:
    claim_ids = {claim.claim_id for claim in artifact.grounding.claims}

    for section_index, section in enumerate(artifact.report_spec.sections):
        for block_index, block in enumerate(section.blocks):
            location = f"report_spec.sections[{section_index}].blocks[{block_index}]"
            if block.dataset_id is not None and block.dataset_id not in artifact.datasets:
                issues.append(
                    _issue(
                        "dataset_ref",
                        f"block {block.block_id!r} references missing dataset {block.dataset_id!r}",
                        f"{location}.dataset_id",
                    )
                )
            for ref_index, value_ref in enumerate(block.value_refs):
                _validate_value_ref(
                    artifact, value_ref, f"{location}.value_refs[{ref_index}]", issues
                )
            if block.narrative_ref is not None and block.narrative_ref not in block_ids:
                issues.append(
                    _issue(
                        "narrative_ref",
                        f"block {block.block_id!r} references missing narrative block {block.narrative_ref!r}",
                        f"{location}.narrative_ref",
                    )
                )
            for ref_index, claim_ref in enumerate(block.claim_refs):
                if claim_ref not in claim_ids:
                    issues.append(
                        _issue(
                            "claim_ref",
                            f"block {block.block_id!r} references missing claim {claim_ref!r}",
                            f"{location}.claim_refs[{ref_index}]",
                        )
                    )
            for ref_index, step_ref in enumerate(block.step_refs):
                if step_ref not in step_ids:
                    issues.append(
                        _issue(
                            "step_ref",
                            f"block {block.block_id!r} references missing flow step {step_ref!r}",
                            f"{location}.step_refs[{ref_index}]",
                        )
                    )

    for claim_index, claim in enumerate(artifact.grounding.claims):
        location = f"grounding.claims[{claim_index}]"
        if claim.section_id not in section_ids:
            issues.append(
                _issue(
                    "section_ref",
                    f"claim {claim.claim_id!r} references missing section {claim.section_id!r}",
                    f"{location}.section_id",
                )
            )
        for ref_index, step_ref in enumerate(claim.supporting_steps):
            if step_ref not in step_ids:
                issues.append(
                    _issue(
                        "step_ref",
                        f"claim {claim.claim_id!r} references missing flow step {step_ref!r}",
                        f"{location}.supporting_steps[{ref_index}]",
                    )
                )
        for ref_index, dataset_ref in enumerate(claim.supporting_datasets):
            if dataset_ref not in artifact.datasets:
                issues.append(
                    _issue(
                        "dataset_ref",
                        f"claim {claim.claim_id!r} references missing dataset {dataset_ref!r}",
                        f"{location}.supporting_datasets[{ref_index}]",
                    )
                )
        for ref_index, value_ref in enumerate(claim.value_refs):
            _validate_value_ref(artifact, value_ref, f"{location}.value_refs[{ref_index}]", issues)


def _validate_dataset_field(
    artifact: MarivoReportArtifact,
    dataset_id: str | None,
    field: str,
    location: str,
    issues: list[ReportPackageValidationIssue],
) -> None:
    if dataset_id is None:
        issues.append(
            _issue(
                "dataset_ref",
                "visual hint field references require block dataset_id",
                location,
            )
        )
        return
    dataset = artifact.datasets.get(dataset_id)
    if dataset is None:
        return
    if not dataset.rows:
        issues.append(
            _issue(
                "visual_ref",
                f"visual hint field {field!r} references empty dataset {dataset_id!r}",
                location,
            )
        )
        return
    for row_index, row in enumerate(dataset.rows):
        if field not in row:
            issues.append(
                _issue(
                    "visual_ref",
                    f"visual hint field {field!r} is missing from dataset {dataset_id!r} row {row_index}",
                    location,
                )
            )


def _validate_visual_hints(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    for section_index, section in enumerate(artifact.report_spec.sections):
        for block_index, block in enumerate(section.blocks):
            location = f"report_spec.sections[{section_index}].blocks[{block_index}]"
            for metric_index, metric in enumerate(block.metrics):
                _validate_value_ref(
                    artifact,
                    metric.value_ref,
                    f"{location}.metrics[{metric_index}].value_ref",
                    issues,
                )
            if block.block_type == "chart":
                if block.chart is None:
                    issues.append(
                        _issue(
                            "visual_spec",
                            f"chart block {block.block_id!r} requires chart spec",
                            f"{location}.chart",
                        )
                    )
                else:
                    x_field = block.chart.fields.get("x", "").strip()
                    y_field = block.chart.fields.get("y", "").strip()
                    if not x_field or not y_field:
                        issues.append(
                            _issue(
                                "visual_spec",
                                f"chart block {block.block_id!r} requires non-empty x/y encodings",
                                f"{location}.chart.fields",
                            )
                        )
                    for channel, field in block.chart.fields.items():
                        field = field.strip()
                        if field:
                            _validate_dataset_field(
                                artifact,
                                block.dataset_id,
                                field,
                                f"{location}.chart.fields.{channel}",
                                issues,
                            )
            if block.block_type == "table":
                for column_index, column in enumerate(block.columns):
                    _validate_dataset_field(
                        artifact,
                        block.dataset_id,
                        column.key,
                        f"{location}.columns[{column_index}].key",
                        issues,
                    )


def _validate_artifact_refs(
    artifact: MarivoReportArtifact,
    artifact_ids: set[str],
    issues: list[ReportPackageValidationIssue],
) -> None:
    for dataset_id, dataset in artifact.datasets.items():
        for ref_index, artifact_ref in enumerate(dataset.metadata.source_artifacts):
            if artifact_ref not in artifact_ids:
                issues.append(
                    _issue(
                        "artifact_ref",
                        f"dataset {dataset_id!r} references missing artifact {artifact_ref!r}",
                        f"datasets.{dataset_id}.metadata.source_artifacts[{ref_index}]",
                    )
                )

    for claim_index, claim in enumerate(artifact.grounding.claims):
        for ref_index, artifact_ref in enumerate(claim.supporting_artifacts):
            if artifact_ref not in artifact_ids:
                issues.append(
                    _issue(
                        "artifact_ref",
                        f"claim {claim.claim_id!r} references missing artifact {artifact_ref!r}",
                        f"grounding.claims[{claim_index}].supporting_artifacts[{ref_index}]",
                    )
                )


def _artifact_ids(artifact: MarivoReportArtifact) -> set[str]:
    ids: set[str] = set(artifact.evidence)
    for step in artifact.flow.steps:
        ids.update(step.input_artifacts)
        ids.update(step.output_artifacts)
    return ids


def _includes_when_manifest_omits(manifest_value: str, dataset_value: str) -> bool:
    return manifest_value == "omitted" and dataset_value == "included"


def _validate_data_policy(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    manifest_policy = artifact.manifest.data_policy
    for dataset in artifact.datasets.values():
        policy = dataset.metadata.data_policy
        location = f"datasets.{dataset.dataset_id}.metadata.data_policy"
        if _includes_when_manifest_omits(manifest_policy.row_level_data, policy.row_level_data):
            issues.append(
                _issue(
                    "data_policy",
                    "dataset row_level_data cannot be included when manifest omits row-level data",
                    location=location,
                )
            )
        if _includes_when_manifest_omits(manifest_policy.frame_snapshots, policy.frame_snapshots):
            issues.append(
                _issue(
                    "data_policy",
                    "dataset frame_snapshots cannot be included when manifest omits frame snapshots",
                    location=location,
                )
            )


def _validate_source_provenance(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    artifact_producers: dict[str, str] = {}
    for step in artifact.flow.steps:
        for artifact_ref in step.output_artifacts:
            if artifact_ref not in artifact_producers:
                artifact_producers[artifact_ref] = step.kind

    for dataset in artifact.datasets.values():
        source = dataset.metadata.source_provenance
        location = f"datasets.{dataset.dataset_id}.metadata.source_provenance"
        if source.generated_from == "pandas_scratch" and not source.script_refs:
            issues.append(
                _issue(
                    "script_refs",
                    "pandas_scratch datasets require script_refs",
                    location=location,
                )
            )
        for ref_index, artifact_ref in enumerate(dataset.metadata.source_artifacts):
            source_artifact_location = (
                f"datasets.{dataset.dataset_id}.metadata.source_artifacts[{ref_index}]"
            )
            producer_kind = artifact_producers.get(artifact_ref)
            if producer_kind is not None and source.generated_from != producer_kind:
                issues.append(
                    _issue(
                        "source_provenance",
                        (
                            f"dataset source_provenance generated_from {source.generated_from!r} "
                            f"does not match producing flow step kind {producer_kind!r} "
                            f"for artifact {artifact_ref!r}"
                        ),
                        source_artifact_location,
                    )
                )
        if source.generated_from == "promotion" and not source.promotion_ref:
            issues.append(
                _issue(
                    "promotion_ref",
                    "promotion datasets require promotion_ref",
                    location=location,
                )
            )


def _has_numeric_literal(text: str) -> bool:
    return bool(_NUMERIC_LITERAL_RE.search(text))


def _is_visual_block(block: ReportBlock) -> bool:
    return block.block_type in _VISUAL_BLOCK_TYPES


def _validate_grounding_and_numbers(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    executive_sections = [
        section
        for section in artifact.report_spec.sections
        if section.section_type == "executive_summary"
    ]
    executive_claims = [
        claim
        for claim in artifact.grounding.claims
        if any(claim.section_id == section.section_id for section in executive_sections)
    ]
    if not executive_claims:
        issues.append(
            _issue(
                "executive_claim_grounding",
                "executive_summary requires at least one grounded claim",
            )
        )

    for claim in artifact.grounding.claims:
        location = f"grounding.claims.{claim.claim_id}"
        if claim.grounding_type != "commentary" and not (
            claim.supporting_artifacts or claim.supporting_steps or claim.supporting_datasets
        ):
            issues.append(
                _issue(
                    "claim_grounding",
                    "evidence-backed claims require supporting evidence refs",
                    location=location,
                )
            )
        if _has_numeric_literal(claim.text_template) and not claim.value_refs:
            issues.append(
                _issue(
                    "single_source_number",
                    "numeric claim text requires value_refs instead of literal reader-facing numbers",
                    location=location,
                )
            )


def _validate_narrative_adjacency(
    artifact: MarivoReportArtifact,
    issues: list[ReportPackageValidationIssue],
) -> None:
    block_ids = {
        block.block_id for section in artifact.report_spec.sections for block in section.blocks
    }
    for section in artifact.report_spec.sections:
        for block in section.blocks:
            if not _is_visual_block(block):
                continue
            location = f"report_spec.sections.{section.section_id}.blocks.{block.block_id}"
            if block.narrative_ref is None:
                issues.append(
                    _issue(
                        "narrative_adjacency",
                        "visual blocks require narrative_ref",
                        location=location,
                    )
                )
            elif block.narrative_ref not in block_ids:
                issues.append(
                    _issue(
                        "narrative_adjacency",
                        f"narrative_ref does not resolve: {block.narrative_ref}",
                        location=location,
                    )
                )


def validate_report_artifact(
    artifact: MarivoReportArtifact,
    *,
    script_root: str | Path | None = None,
) -> ReportPackageValidationResult:
    """Validate report package cross-references and required report sections.

    When *script_root* is provided, every ``FlowStep.script_refs`` and
    ``SourceProvenance.script_refs`` entry is also checked for existence on
    disk relative to that directory. Pass ``None`` (the default) to skip the
    file-existence check — for example, when validating an artifact before the
    scripts are staged alongside it.
    """
    issues: list[ReportPackageValidationIssue] = []
    section_ids = {section.section_id for section in artifact.report_spec.sections}
    block_ids = {
        block.block_id for section in artifact.report_spec.sections for block in section.blocks
    }
    step_ids = {step.step_id for step in artifact.flow.steps}
    artifact_ids = _artifact_ids(artifact)

    _validate_required_sections(artifact, issues)
    _validate_report_ids(artifact, issues)
    _validate_report_refs(artifact, section_ids, block_ids, step_ids, issues)
    _validate_visual_hints(artifact, issues)
    _validate_artifact_refs(artifact, artifact_ids, issues)
    _validate_data_policy(artifact, issues)
    _validate_source_provenance(artifact, issues)
    _validate_grounding_and_numbers(artifact, issues)
    _validate_narrative_adjacency(artifact, issues)
    if script_root is not None:
        _validate_script_refs_exist(artifact, Path(script_root), issues)

    return ReportPackageValidationResult(ok=not issues, issues=tuple(issues))


def _validate_script_refs_exist(
    artifact: MarivoReportArtifact,
    script_root: Path,
    issues: list[ReportPackageValidationIssue],
) -> None:
    for step_index, step in enumerate(artifact.flow.steps):
        for ref_index, script_ref in enumerate(step.script_refs):
            location = f"flow.steps[{step_index}].script_refs[{ref_index}]"
            _check_script_ref_exists(script_ref, script_root, location, issues)
    for dataset_id, dataset in artifact.datasets.items():
        for ref_index, script_ref in enumerate(dataset.metadata.source_provenance.script_refs):
            location = f"datasets.{dataset_id}.metadata.source_provenance.script_refs[{ref_index}]"
            _check_script_ref_exists(script_ref, script_root, location, issues)


def _check_script_ref_exists(
    script_ref: str,
    script_root: Path,
    location: str,
    issues: list[ReportPackageValidationIssue],
) -> None:
    resolved = script_root / script_ref
    if not resolved.is_file():
        issues.append(
            _issue(
                "script_ref_missing",
                (
                    f"script_ref {script_ref!r} does not exist relative to "
                    f"script_root {str(script_root)!r}"
                ),
                location=location,
            )
        )
