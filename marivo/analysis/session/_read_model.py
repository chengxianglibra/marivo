"""Immutable result algebra for public Session runtime reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from marivo.analysis._capabilities.model import ArtifactFamily
from marivo.analysis._pages import _BoundedPage
from marivo.analysis.candidate_lineage import CandidateResolutionIssue
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.evidence.types import (
    ArtifactIssue,
    EvidenceStatus,
    QualitySummary,
)
from marivo.analysis.frames.base import ArtifactMaterialization
from marivo.analysis.session._runs import JsonValue
from marivo.render import Card, RenderableResult

RunLifecycle: TypeAlias = Literal["incomplete", "succeeded", "failed"]
GraphDirection: TypeAlias = Literal["ancestors", "descendants"]


@dataclass(frozen=True, slots=True, kw_only=True)
class RunArgument:
    name: str
    value: JsonValue


@dataclass(frozen=True, slots=True, kw_only=True)
class RunFailure:
    error_type: str
    message: str
    expected: JsonValue | None
    received: JsonValue | None
    location: str | None
    repair: AnalysisRepair | None


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class _RunBase(RenderableResult):
    run_id: str
    capability_id: str
    analysis_purpose: str | None
    input_artifact_refs: tuple[str, ...]
    arguments: tuple[RunArgument, ...]
    omitted_argument_names: tuple[str, ...]
    started_at: datetime

    @property
    def lifecycle(self) -> RunLifecycle:
        raise NotImplementedError

    def _repr_identity(self) -> str:
        return (
            f"{type(self).__name__} id={self.run_id} "
            f"capability={self.capability_id} status={self.lifecycle}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".show()", ".arguments", ".input_artifact_refs"),
        ).status(self.lifecycle)
        card.field("started_at", self.started_at.isoformat())
        if self.analysis_purpose:
            card.field("analysis_purpose", self.analysis_purpose)
        card.listing(
            "inputs",
            self.input_artifact_refs or ("none",),
        )
        card.listing(
            "arguments",
            (f"{item.name}={item.value!r}" for item in self.arguments),
        )
        if self.omitted_argument_names:
            card.listing("omitted_arguments", self.omitted_argument_names)
        return card


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class IncompleteRun(_RunBase):
    @property
    def lifecycle(self) -> Literal["incomplete"]:
        return "incomplete"


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SucceededRun(_RunBase):
    output_artifact_ref: str
    output_mode: Literal["produced", "reused"]
    finished_at: datetime

    @property
    def lifecycle(self) -> Literal["succeeded"]:
        return "succeeded"

    def _card(self) -> Card:
        return (
            super(SucceededRun, self)
            ._card()
            .field("finished_at", self.finished_at.isoformat())
            .field("output", f"{self.output_artifact_ref} ({self.output_mode})")
        )


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class FailedRun(_RunBase):
    failed_at: datetime
    failure: RunFailure

    @property
    def lifecycle(self) -> Literal["failed"]:
        return "failed"

    def _card(self) -> Card:
        return (
            super(FailedRun, self)
            ._card()
            .field("failed_at", self.failed_at.isoformat())
            .field("failure", f"{self.failure.error_type}: {self.failure.message}")
        )


RunRecord: TypeAlias = IncompleteRun | SucceededRun | FailedRun


class RunPage(_BoundedPage[RunRecord]):
    """Immutable bounded newest-first Run page."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactEvidenceSummary:
    status: EvidenceStatus
    digest_present: bool
    digest_item_count: int
    omitted_item_count: int
    finding_count: int

    def __post_init__(self) -> None:
        counts = (self.digest_item_count, self.omitted_item_count, self.finding_count)
        if any(value < 0 for value in counts):
            raise ValueError("Artifact Evidence counts must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactIssueCounts:
    warning: int
    blocking: int

    @classmethod
    def from_issues(cls, issues: tuple[ArtifactIssue, ...]) -> ArtifactIssueCounts:
        return cls(
            warning=sum(issue.severity == "warning" for issue in issues),
            blocking=sum(issue.severity == "blocking" for issue in issues),
        )


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class ArtifactSummary(RenderableResult):
    ref: str
    family: ArtifactFamily
    semantic_shape: str | None
    created_at: datetime
    produced_by_run: str | None
    analysis_purpose: str | None
    row_count: int
    content_hash: str | None
    materialization: ArtifactMaterialization
    evidence: ArtifactEvidenceSummary
    quality: QualitySummary | None
    issue_counts: ArtifactIssueCounts

    def _repr_identity(self) -> str:
        return f"ArtifactSummary ref={self.ref} family={self.family} rows={self.row_count}"

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".show()", ".evidence", ".quality", ".issue_counts"),
        ).status(self.materialization)
        card.field("created_at", self.created_at.isoformat())
        card.field("producer", self.produced_by_run or "none")
        card.field(
            "evidence",
            f"{self.evidence.status} findings={self.evidence.finding_count} "
            f"digest_items={self.evidence.digest_item_count}",
        )
        card.field(
            "issues",
            f"warning={self.issue_counts.warning} blocking={self.issue_counts.blocking}",
        )
        if self.analysis_purpose:
            card.field("analysis_purpose", self.analysis_purpose)
        return card


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionGraphEdge:
    kind: Literal["consumes", "produces", "reuses"]
    run_id: str
    artifact_ref: str


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionGraph(RenderableResult):
    session_id: str
    artifacts: tuple[ArtifactSummary, ...]
    runs: tuple[RunRecord, ...]
    edges: tuple[SessionGraphEdge, ...]
    root_run_ids: tuple[str, ...]
    head_artifact_refs: tuple[str, ...]
    failed_run_ids: tuple[str, ...]
    incomplete_run_ids: tuple[str, ...]
    boundary_artifact_refs: tuple[str, ...]
    boundary_run_ids: tuple[str, ...]
    truncated: bool

    def _repr_identity(self) -> str:
        return (
            f"SessionGraph session={self.session_id} artifacts={len(self.artifacts)} "
            f"runs={len(self.runs)} edges={len(self.edges)} "
            f"heads={len(self.head_artifact_refs)}"
        )

    def _card(self) -> Card:
        runs = {run.run_id: run for run in self.runs}
        artifacts = {artifact.ref: artifact for artifact in self.artifacts}
        available = [
            ".artifacts",
            ".runs",
            ".edges",
            ".head_artifact_refs",
            ".failed_run_ids",
            ".show()",
        ]
        focus_ref = next(
            iter(
                (
                    *self.boundary_artifact_refs,
                    *self.head_artifact_refs,
                    *(artifact.ref for artifact in self.artifacts),
                )
            ),
            None,
        )
        if focus_ref is not None:
            available.extend(
                (
                    f"session.artifact({focus_ref!r})",
                    f"session.graph(artifact_ref={focus_ref!r}, direction='ancestors')",
                    f"session.graph(artifact_ref={focus_ref!r}, direction='descendants')",
                )
            )
        recovery_run_id = next(
            iter(
                (
                    *self.failed_run_ids,
                    *self.incomplete_run_ids,
                    *self.boundary_run_ids,
                    *(run.run_id for run in self.runs),
                )
            ),
            None,
        )
        if recovery_run_id is not None:
            available.append(f"session.get_run({recovery_run_id!r})")
        card = Card(
            identity=self._repr_identity(),
            available=tuple(available),
        ).status("truncated" if self.truncated else "complete")
        attention = [
            f"{run.run_id} {run.lifecycle} {run.capability_id} "
            f"inputs={list(run.input_artifact_refs)!r}"
            for run in self.runs
            if run.lifecycle in {"failed", "incomplete"}
        ]
        if attention:
            card.listing("attention", attention)
        flow: list[str] = []
        for edge in self.edges:
            run = runs[edge.run_id]
            artifact = artifacts[edge.artifact_ref]
            if edge.kind == "consumes":
                flow.append(f"{artifact.ref} -> {run.run_id} {run.capability_id}")
            else:
                flow.append(
                    f"{run.run_id} {run.capability_id} -> {artifact.ref} "
                    f"[{edge.kind} evidence={artifact.evidence.status}]"
                )
        card.listing("flow", flow or ("empty",))
        card.listing(
            "heads",
            (
                f"{ref} {artifacts[ref].family} evidence={artifacts[ref].evidence.status}"
                for ref in self.head_artifact_refs
            ),
        )
        if self.boundary_artifact_refs or self.boundary_run_ids:
            card.listing(
                "boundaries",
                (
                    *(f"artifact {ref}" for ref in self.boundary_artifact_refs),
                    *(f"run {run_id}" for run_id in self.boundary_run_ids),
                ),
            )
        card.listing(
            "read_boundaries",
            (
                "semantic authority is not checked by SessionGraph",
                "datasource freshness is not checked by SessionGraph",
            ),
        )
        return card


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionRuntimeRecap(RenderableResult):
    session_id: str
    artifact_count: int
    head_artifact_count: int
    head_artifact_refs: tuple[str, ...]
    succeeded_run_count: int
    failed_run_count: int
    incomplete_run_count: int
    evidence_complete_count: int
    evidence_partial_count: int
    evidence_unavailable_count: int
    attention_run_ids: tuple[str, ...]
    overall_graph_available: bool

    def _repr_identity(self) -> str:
        return f"SessionRuntimeRecap session={self.session_id}"

    def _card(self) -> Card:
        available = [".runs(...)", ".artifact(ref)"]
        if self.overall_graph_available:
            available.insert(0, ".graph()")
        available.extend(
            (
                ".graph(artifact_ref='<ref>', direction='ancestors')",
                ".graph(artifact_ref='<ref>', direction='descendants')",
                ".get_run(run_id)",
                ".revalidate(ref)",
            )
        )
        return (
            Card(identity=self._repr_identity(), available=tuple(available))
            .field(
                "artifacts",
                f"total={self.artifact_count} heads={self.head_artifact_count} "
                f"evidence_complete={self.evidence_complete_count} "
                f"partial={self.evidence_partial_count} "
                f"unavailable={self.evidence_unavailable_count}",
            )
            .field(
                "runs",
                f"succeeded={self.succeeded_run_count} failed={self.failed_run_count} "
                f"incomplete={self.incomplete_run_count}",
            )
            .listing("attention", self.attention_run_ids or ("none",))
            .listing("heads", self.head_artifact_refs or ("none",))
            .field("current authority", "not checked; call session.revalidate('<ref>')")
            .field("source freshness", "not checked by Session reads")
        )


def issue_severity(issue: ArtifactIssue | CandidateResolutionIssue) -> str:
    """Return the closed severity shared by all current Artifact issue variants."""
    return issue.severity


__all__: list[str] = []
