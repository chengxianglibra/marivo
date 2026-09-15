"""Immutable terminal projections for the private Dataset v3 read surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.datasets.descriptors import DatasetByteCount
from marivo.analysis.evidence._dataset_types import ArtifactEvidenceSummary, ArtifactIssueCounts
from marivo.analysis.materialization.contracts import RunDatasetInput, RunFailure, invalid
from marivo.analysis.refs import ArtifactRef
from marivo.render import Card, RenderableResult

RunLifecycle: TypeAlias = Literal["incomplete", "succeeded", "failed"]
GraphDirection: TypeAlias = Literal["ancestors", "descendants"]


def _identity(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value.encode()) > 4096
        or "\n" in value
        or "\r" in value
    ):
        raise invalid("invalid bounded runtime identity")


def _aware(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise invalid("runtime timestamp must be timezone-aware")


def _count(value: int) -> None:
    if type(value) is not int or value < 0:
        raise invalid("runtime count must be a non-negative integer")


def _ordered_refs(values: tuple[ArtifactRef, ...]) -> None:
    if type(values) is not tuple or any(type(value) is not ArtifactRef for value in values):
        raise invalid("runtime Artifact references must be an immutable typed tuple")


def _refs(values: tuple[ArtifactRef, ...]) -> None:
    _ordered_refs(values)
    if len(set(values)) != len(values):
        raise invalid("runtime Artifact references must be duplicate-free")


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class _RunBase(RenderableResult):
    run_id: str
    session_id: str
    admitted_at: datetime
    dataset_input: RunDatasetInput
    input_artifact_refs: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if type(self) not in (IncompleteRun, SucceededRun, FailedRun):
            raise invalid("unregistered terminal Run variant")
        _identity(self.run_id)
        _identity(self.session_id)
        _aware(self.admitted_at)
        if type(self.dataset_input) is not RunDatasetInput:
            raise invalid("Run input must use the exact typed projection")
        _ordered_refs(self.input_artifact_refs)
        if isinstance(self, SucceededRun):
            _aware(self.finished_at)
            if (
                type(self.output_artifact_ref) is not ArtifactRef
                or self.finished_at < self.admitted_at
            ):
                raise invalid("invalid succeeded Run terminal")
        elif isinstance(self, FailedRun):
            _aware(self.failed_at)
            if type(self.failure) is not RunFailure or self.failed_at < self.admitted_at:
                raise invalid("invalid failed Run terminal")

    @property
    def lifecycle(self) -> RunLifecycle:
        raise NotImplementedError

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} id={self.run_id} session={self.session_id}"[:256]

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()", ".dataset_input"))
            .status(self.lifecycle)
            .field("admitted_at", self.admitted_at.isoformat())
            .field("shape", str(self.dataset_input.shape_id))
            .field("definition", self.dataset_input.definition_fingerprint)
            .listing("inputs", (str(ref) for ref in self.input_artifact_refs))
        )


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class IncompleteRun(_RunBase):
    @property
    def lifecycle(self) -> Literal["incomplete"]:
        return "incomplete"


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SucceededRun(_RunBase):
    finished_at: datetime
    output_artifact_ref: ArtifactRef

    @property
    def lifecycle(self) -> Literal["succeeded"]:
        return "succeeded"

    def _card(self) -> Card:
        return (
            super(SucceededRun, self)
            ._card()
            .field("finished_at", self.finished_at.isoformat())
            .field("output", str(self.output_artifact_ref))
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
            .field(
                "failure", f"{self.failure.phase}/{self.failure.kind}: {self.failure.safe_message}"
            )
        )


RunRecord: TypeAlias = IncompleteRun | SucceededRun | FailedRun


def _page_fields(items: tuple[object, ...], limit: int, has_more: bool, cursor: str | None) -> None:
    if type(items) is not tuple or type(limit) is not int or not 1 <= limit <= 100:
        raise invalid("invalid immutable page collection or limit")
    if type(has_more) is not bool or len(items) > limit:
        raise invalid("invalid bounded page state")
    if cursor is not None and (type(cursor) is not str or not cursor or len(cursor) > 16_384):
        raise invalid("invalid bounded page cursor")
    if has_more != (cursor is not None):
        raise invalid("page continuation contradicts has_more")


@dataclass(frozen=True, repr=False, slots=True)
class RunPage(_BoundedPage[RunRecord]):
    """Bounded newest-admission-first Run records."""

    def __post_init__(self) -> None:
        _page_fields(self.items, self.limit, self.has_more, self.next_cursor)
        if any(type(value) not in (IncompleteRun, SucceededRun, FailedRun) for value in self.items):
            raise invalid("Run page contains an unregistered Run variant")


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class ArtifactSummary(RenderableResult):
    artifact_ref: ArtifactRef
    artifact_session_ref: str
    run_admitted_at: datetime
    run_finished_at: datetime
    family_id: str
    shape_id: str
    definition_fingerprint: str
    committed_at: datetime
    producing_run_ref: str
    realized_row_count: int
    realized_byte_count: DatasetByteCount
    storage_kind_id: str
    content_authority_digest: str
    evidence: ArtifactEvidenceSummary
    issue_counts: ArtifactIssueCounts

    def __post_init__(self) -> None:
        _refs((self.artifact_ref,))
        for identity in (
            self.artifact_session_ref,
            self.family_id,
            self.shape_id,
            self.definition_fingerprint,
            self.producing_run_ref,
            self.storage_kind_id,
            self.content_authority_digest,
        ):
            _identity(identity)
        for value in (self.run_admitted_at, self.run_finished_at, self.committed_at):
            _aware(value)
        _count(self.realized_row_count)
        if self.run_finished_at < self.run_admitted_at or self.committed_at < self.run_admitted_at:
            raise invalid("Artifact timing precedes producer admission")

    def _repr_identity(self) -> str:
        return f"ArtifactSummary ref={self.artifact_ref} family={self.family_id} rows={self.realized_row_count}"[
            :256
        ]

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(), available=(".show()", ".evidence", ".issue_counts")
            )
            .field("owner", self.artifact_session_ref)
            .field("producer", self.producing_run_ref)
            .field("shape", self.shape_id)
            .field("committed_at", self.committed_at.isoformat())
            .field("storage", self.storage_kind_id)
            .field("findings", str(self.evidence.finding_count))
            .field(
                "issues",
                f"warning={self.issue_counts.warning} blocking={self.issue_counts.blocking}",
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class SessionGraphEdge(RenderableResult):
    kind: Literal["consumes", "produces"]
    run_id: str
    artifact_ref: ArtifactRef

    def __post_init__(self) -> None:
        if self.kind not in ("consumes", "produces"):
            raise invalid("unregistered graph edge kind")
        _identity(self.run_id)
        _refs((self.artifact_ref,))

    def _repr_identity(self) -> str:
        return f"SessionGraphEdge kind={self.kind} run={self.run_id} artifact={self.artifact_ref}"[
            :256
        ]

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".show()",))


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionGraph(RenderableResult):
    session_id: str
    artifacts: tuple[ArtifactSummary, ...]
    runs: tuple[RunRecord, ...]
    edges: tuple[SessionGraphEdge, ...]
    root_run_ids: tuple[str, ...]
    head_artifact_refs: tuple[ArtifactRef, ...]
    failed_run_ids: tuple[str, ...]
    incomplete_run_ids: tuple[str, ...]
    boundary_artifact_refs: tuple[ArtifactRef, ...]
    boundary_run_ids: tuple[str, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _identity(self.session_id)
        if (
            type(self.artifacts) is not tuple
            or type(self.runs) is not tuple
            or type(self.edges) is not tuple
        ):
            raise invalid("graph collections must be immutable tuples")
        if len(self.artifacts) + len(self.runs) > 500 or type(self.truncated) is not bool:
            raise invalid("invalid bounded Session graph")
        artifact_ids = {item.artifact_ref for item in self.artifacts}
        run_ids = {run.run_id for run in self.runs}
        if len(artifact_ids) != len(self.artifacts) or len(run_ids) != len(self.runs):
            raise invalid("duplicate selected graph identity")
        for refs in (self.head_artifact_refs, self.boundary_artifact_refs):
            _refs(refs)
            if not set(refs) <= artifact_ids:
                raise invalid("graph Artifact set escapes selected records")
        for run_refs in (
            self.root_run_ids,
            self.failed_run_ids,
            self.incomplete_run_ids,
            self.boundary_run_ids,
        ):
            if (
                type(run_refs) is not tuple
                or len(set(run_refs)) != len(run_refs)
                or not set(run_refs) <= run_ids
            ):
                raise invalid("graph Run set escapes selected records")
        if any(
            edge.run_id not in run_ids or edge.artifact_ref not in artifact_ids
            for edge in self.edges
        ):
            raise invalid("graph edge escapes selected records")

    def _repr_identity(self) -> str:
        return f"SessionGraph session={self.session_id} artifacts={len(self.artifacts)} runs={len(self.runs)} edges={len(self.edges)}"[
            :256
        ]

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(".artifacts", ".runs", ".edges", ".show()"),
            )
            .field("truncated", str(self.truncated))
            .listing("artifacts", (repr(value) for value in self.artifacts))
            .listing("runs", (repr(value) for value in self.runs))
            .listing("heads", (str(ref) for ref in self.head_artifact_refs))
            .listing("boundaries", (*map(str, self.boundary_artifact_refs), *self.boundary_run_ids))
            .field("full integrity", "not checked; call session.revalidate(ref)")
            .field("source freshness", "not checked by SessionGraph")
        )


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionRuntimeRecap(RenderableResult):
    session_id: str
    run_count: int
    artifact_count: int
    head_artifact_count: int
    head_artifact_refs: tuple[ArtifactRef, ...]
    succeeded_run_count: int
    failed_run_count: int
    incomplete_run_count: int
    attention_run_ids: tuple[str, ...]
    overall_graph_available: bool

    def __post_init__(self) -> None:
        _identity(self.session_id)
        for value in (
            self.run_count,
            self.artifact_count,
            self.head_artifact_count,
            self.succeeded_run_count,
            self.failed_run_count,
            self.incomplete_run_count,
        ):
            _count(value)
        _refs(self.head_artifact_refs)
        if (
            type(self.attention_run_ids) is not tuple
            or type(self.overall_graph_available) is not bool
        ):
            raise invalid(
                "runtime recap requires an immutable attention tuple and exact availability boolean"
            )
        for run_id in self.attention_run_ids:
            _identity(run_id)
        if len(set(self.attention_run_ids)) != len(self.attention_run_ids):
            raise invalid("runtime recap attention identities must be duplicate-free")
        if len(self.head_artifact_refs) > 3 or len(self.attention_run_ids) > 3:
            raise invalid("runtime recap identity bound exceeded")
        if not len(self.head_artifact_refs) <= self.head_artifact_count <= self.artifact_count:
            raise invalid("runtime recap head counts disagree")
        if (
            self.run_count
            != self.succeeded_run_count + self.failed_run_count + self.incomplete_run_count
        ):
            raise invalid("runtime recap Run counts disagree")

    def _repr_identity(self) -> str:
        return f"SessionRuntimeRecap session={self.session_id} runs={self.run_count} artifacts={self.artifact_count}"[
            :256
        ]

    def _card(self) -> Card:
        available = [".runs()", ".get_run(run_id)", ".artifact(ref)"]
        if self.overall_graph_available:
            available.append(".graph()")
        return (
            Card(identity=self._repr_identity(), available=available)
            .field(
                "runs",
                f"succeeded={self.succeeded_run_count} failed={self.failed_run_count} incomplete={self.incomplete_run_count}",
            )
            .field("artifacts", f"total={self.artifact_count} heads={self.head_artifact_count}")
            .listing("heads", map(str, self.head_artifact_refs))
            .listing("attention", self.attention_run_ids)
            .field("full integrity", "not checked; call session.revalidate(ref)")
        )


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionSummary(RenderableResult):
    id: str
    name: str
    question: str | None
    created_at: datetime
    updated_at: datetime
    run_count: int
    artifact_count: int

    def __post_init__(self) -> None:
        _identity(self.id)
        _identity(self.name)
        _aware(self.created_at)
        _aware(self.updated_at)
        _count(self.run_count)
        _count(self.artifact_count)

    def _repr_identity(self) -> str:
        return f"SessionSummary id={self.id} name={self.name}"[:256]

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("updated_at", self.updated_at.isoformat())
            .field("runs", str(self.run_count))
            .field("artifacts", str(self.artifact_count))
            .field("question", self.question or "none")
        )


@dataclass(frozen=True, repr=False, slots=True)
class SessionSummaryPage(_BoundedPage[SessionSummary]):
    """Bounded newest-updated-first Session summaries."""

    def __post_init__(self) -> None:
        _page_fields(self.items, self.limit, self.has_more, self.next_cursor)
        if any(type(value) is not SessionSummary for value in self.items):
            raise invalid("Session page contains an unregistered summary value")


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionInspection(RenderableResult):
    summary: SessionSummary
    runs: RunPage

    def __post_init__(self) -> None:
        if type(self.summary) is not SessionSummary or type(self.runs) is not RunPage:
            raise invalid("Session inspection requires exact summary and Run page values")
        if any(run.session_id != self.summary.id for run in self.runs.items):
            raise invalid("Session inspection contains a foreign Run")

    def _repr_identity(self) -> str:
        return f"SessionInspection id={self.summary.id} name={self.summary.name}"[:256]

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".summary", ".runs", ".show()"))
            .field("runs", str(self.summary.run_count))
            .field("artifacts", str(self.summary.artifact_count))
            .listing("recent_runs", (repr(run) for run in self.runs.items))
        )


__all__: list[str] = []
