"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import importlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis.errors import (
    AnalysisRepair,
    FrameMutationError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.types import ArtifactEvidenceSummary, QualitySummary
from marivo.analysis.followups import BlockingIssue, ConfidenceScope
from marivo.analysis.lineage import Lineage
from marivo.render import Card, RenderableResult


def _display_column_names(columns: pd.Index) -> list[str]:
    display_columns: list[str] = []
    used_columns: set[str] = set()
    for column in columns:
        column_name = str(column)
        display_name = column_name
        suffix = 2
        while display_name in used_columns:
            display_name = f"{column_name}#{suffix}"
            suffix += 1
        used_columns.add(display_name)
        display_columns.append(display_name)
    return display_columns


def _is_missing(value: Any) -> bool:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            scalar = item()
        except (TypeError, ValueError):
            return False
        return scalar if isinstance(scalar, bool) else False
    return False


def _preview_cell(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            return value
    return value


def assert_semantic_shape(*, got: str, expected: str, frame_kind: str) -> None:
    """Raise SemanticKindMismatchError unless ``got`` semantic shape matches ``expected``."""
    if got != expected:
        raise SemanticKindMismatchError(
            message=f"{frame_kind} semantic_shape is {got!r}, expected {expected!r}",
            context={
                "got_semantic_shape": got,
                "expected_semantic_shape": expected,
                "frame_kind": frame_kind,
            },
        )


def assert_attribution_shape(*, got: str, expected: str, frame_kind: str) -> None:
    """Raise SemanticKindMismatchError unless ``got`` attribution shape matches ``expected``."""
    if got != expected:
        raise SemanticKindMismatchError(
            message=f"{frame_kind} attribution_shape is {got!r}, expected {expected!r}",
            context={
                "got_attribution_shape": got,
                "expected_attribution_shape": expected,
                "frame_kind": frame_kind,
            },
        )


ArtifactColumnRole = Literal["time", "dimension", "value", "measure", "unknown"]
ArtifactMaterialization = Literal["materialized", "recomputed", "partial"]
ArtifactPreconditionStatus = Literal["pass", "fail"]


class ArtifactColumn(BaseModel):
    """Column-level schema fact for an analysis artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    dtype: str
    nullable: bool
    role: ArtifactColumnRole = "unknown"


class ArtifactSchema(BaseModel):
    """Bounded deterministic schema descriptor embedded in an artifact contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: list[ArtifactColumn]
    semantic_shape: str | None = None


class ArtifactPrecondition(BaseModel):
    """Mechanical precondition attached to an affordance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    status: ArtifactPreconditionStatus
    reason: str | None = None
    repair: AnalysisRepair | None = None


class ArtifactParamTemplate(BaseModel):
    """Separated deterministic and judgment-filled parameter slots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deterministic_slots: dict[str, Any] = Field(default_factory=dict)
    judgment_slots: list[str] = Field(default_factory=list)


class ArtifactAffordance(BaseModel):
    """Mechanical compatibility entry, not a recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    public_entrypoint: str
    help_target: str
    required_inputs: list[str] = Field(default_factory=list)
    preconditions: list[ArtifactPrecondition] = Field(default_factory=list)
    param_template: ArtifactParamTemplate = Field(default_factory=ArtifactParamTemplate)
    expected_output_family: str | None = None


class ArtifactBoundaryPort(BaseModel):
    """Terminal exit boundary port derived from the capability registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["terminal_exit"]
    capability_id: Literal["boundary.to_pandas"]
    public_entrypoint: str
    help_target: Literal["boundary.to_pandas"]
    preserves: tuple[str, ...]
    does_not_preserve: tuple[str, ...]


class ArtifactContract(BaseModel):
    """Mechanical consumption contract for an artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    ref: str
    is_canonical: bool
    artifact_schema: ArtifactSchema
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    affordances: list[ArtifactAffordance] = Field(default_factory=list)
    boundary_ports: list[ArtifactBoundaryPort] = Field(default_factory=list)


class ArtifactState(BaseModel):
    """Baseline runtime facts for a materialized artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    materialization: ArtifactMaterialization
    content_hash: str | None = None


class BaseFrameMeta(BaseModel):
    """Shared ownership and provenance fields for every frame family."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    session_id: str
    project_root: str
    produced_by_job: str | None
    analysis_purpose: str | None = None
    created_at: datetime
    row_count: int
    byte_size: int
    lineage: Lineage = Lineage()
    artifact_id: str | None = None
    evidence_status: Literal["complete", "partial", "unavailable"] = "unavailable"
    confidence_scope: ConfidenceScope | None = None
    quality_summary: QualitySummary | None = None
    evidence_summary: ArtifactEvidenceSummary | None = None
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    content_hash: str | None = None


def _visible_precondition(precondition: ArtifactPrecondition) -> bool:
    """Return True when a precondition is visible (has actionable content).

    A passing precondition is visible only when it carries a non-empty reason.
    A failing precondition is visible only when it carries a repair with a
    non-empty action.
    """
    if precondition.status == "pass":
        return bool(precondition.reason and precondition.reason.strip())
    return bool(precondition.repair and precondition.repair.action.strip())


def _output_family_str(desc: Any) -> str:
    """Return a string representation of a capability descriptor's output family."""
    model_module = importlib.import_module("marivo.analysis._capabilities.model")
    output = desc.output_family
    if isinstance(output, model_module.SameAsInputFamily):
        return f"same as {output.parameter}"
    return str(output)


def _build_boundary_ports(registry: Any) -> list[ArtifactBoundaryPort]:
    """Build the single terminal boundary port from the registry."""
    model_module = importlib.import_module("marivo.analysis._capabilities.model")
    desc = registry.by_id("boundary.to_pandas")
    assert isinstance(desc, model_module.BoundaryCapability)
    return [
        ArtifactBoundaryPort(
            kind="terminal_exit",
            capability_id="boundary.to_pandas",
            public_entrypoint=desc.public_entrypoint,
            help_target="boundary.to_pandas",
            preserves=desc.preserves,
            does_not_preserve=desc.does_not_preserve,
        )
    ]


def _column_role(column_name: str) -> ArtifactColumnRole:
    """Infer a column role from name heuristics, defaulting to ``dimension``.

    The ``"unknown"`` role is only reachable via direct ``ArtifactColumn``
    construction or the field default; this function never returns it.
    """
    normalized = column_name.lower()
    if normalized in {"bucket_start", "bucket_end", "window_start", "window_end", "time"}:
        return "time"
    if normalized in {"value", "current_value", "baseline_value", "delta", "contribution"}:
        return "value"
    if normalized in {"measure", "metric"}:
        return "measure"
    return "dimension"


@dataclass(repr=False)
class BaseFrame(RenderableResult):
    """Call mv.help(BaseFrame) for its public consumption contract."""

    _df: pd.DataFrame
    meta: BaseFrameMeta

    _NEXT_INTENTS: tuple[str, ...] = ()
    _AVAILABLE_ENTRIES: tuple[str, ...] = (
        ".show()",
        ".contract()",
        ".to_pandas()",
    )

    @property
    def ref(self) -> str:
        return self.meta.ref

    @property
    def lineage(self) -> Lineage:
        return self.meta.lineage

    @property
    def kind(self) -> str:
        return self.meta.kind

    @property
    def quality_summary(self) -> QualitySummary | None:
        return self.meta.quality_summary

    @property
    def evidence_summary(self) -> ArtifactEvidenceSummary | None:
        return self.meta.evidence_summary

    @property
    def blocking_issues(self) -> list[BlockingIssue]:
        return self.meta.blocking_issues

    @property
    def state(self) -> ArtifactState:
        return ArtifactState(
            materialization="materialized",
            content_hash=self.meta.content_hash,
        )

    def _build_schema(self) -> ArtifactSchema:
        """Build the schema descriptor embedded in the artifact contract."""
        columns = [
            ArtifactColumn(
                name=name,
                dtype=str(dtype),
                nullable=bool(self._df.iloc[:, idx].isna().any()) if len(self._df) else True,
                role=_column_role(name),
            )
            for idx, (name, dtype) in enumerate(
                zip(_display_column_names(self._df.columns), self._df.dtypes, strict=True)
            )
        ]
        raw_shape = getattr(self.meta, "semantic_kind", None)
        return ArtifactSchema(
            columns=columns,
            semantic_shape=raw_shape if isinstance(raw_shape, str) else None,
        )

    def contract(self) -> ArtifactContract:
        """Return the mechanical consumption contract for the artifact.

        Affordances are mechanical compatibility entries derived from the
        capability registry's reverse edges (``constructor_consumers``),
        not recommendations.

        Returns:
            ArtifactContract listing artifact_schema, blocking issues,
            affordances, and boundary_ports.

        Example:
            >>> frame.contract().artifact_schema.columns
            [ArtifactColumn(name='bucket_start', ...)]

        Constraints:
            Does not materialize a data copy.
        """
        registry_module = importlib.import_module("marivo.analysis._capabilities.registry")
        model_module = importlib.import_module("marivo.analysis._capabilities.model")
        registry = registry_module.REGISTRY
        operator_cls = model_module.OperatorCapability

        family = type(self).__name__
        consumer_ids = registry.constructor_consumers.get(family, ())
        affordances: list[ArtifactAffordance] = []
        for cap_id in consumer_ids:
            if cap_id == "boundary.to_pandas":
                continue
            desc = registry.by_id(cap_id)
            assert isinstance(desc, operator_cls)
            required_inputs: list[str] = sorted(
                {str(fam) for families in desc.accepted_inputs.values() for fam in families}
            )
            output_family = _output_family_str(desc)
            affordance = ArtifactAffordance(
                capability_id=desc.id,
                public_entrypoint=desc.public_entrypoint,
                help_target=desc.help_target,
                required_inputs=required_inputs,
                param_template=ArtifactParamTemplate(
                    deterministic_slots={"source_ref": self.meta.ref},
                    judgment_slots=[],
                ),
                expected_output_family=output_family,
            )
            # Suppress affordances with failed preconditions that lack visible repair.
            visible = all(_visible_precondition(p) for p in affordance.preconditions)
            if visible:
                affordances.append(affordance)
        return ArtifactContract(
            kind=self.meta.kind,
            ref=self.meta.ref,
            is_canonical=True,
            artifact_schema=self._build_schema(),
            blocking_issues=list(self.meta.blocking_issues),
            affordances=affordances,
            boundary_ports=_build_boundary_ports(registry),
        )

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensive copy of the wrapped DataFrame."""
        return self._df.copy()

    def __getitem__(self, key: Any) -> Any:
        return self._df[key]

    @property
    def shape(self) -> tuple[int, int]:
        return self._df.shape

    @property
    def columns(self) -> list[str]:
        return list(self._df.columns)

    def __len__(self) -> int:
        return len(self._df)

    def __iter__(self) -> Iterator[str]:
        return iter(self.columns)

    def __setitem__(self, key: Any, value: Any) -> None:
        raise FrameMutationError(
            message="frame is immutable; call .to_pandas() to operate on a copy",
        )

    def __add__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __sub__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __mul__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __truediv__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def _preview_rows_provider(self) -> Iterator[tuple[str, ...]]:
        columns = _display_column_names(self._df.columns)
        for row in self._df.itertuples(index=False, name=None):
            yield tuple(str(_preview_cell(value)) for value in row[: len(columns)])

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} ref={self.meta.ref} rows={self.meta.row_count}"

    def _evidence_status_token(self) -> str | None:
        summary_unavailable = any(
            issue.kind == "evidence_summary_unavailable" for issue in self.meta.blocking_issues
        )
        if summary_unavailable:
            return f"evidence={self.meta.evidence_status} summary=unavailable"
        if self.meta.evidence_summary is not None:
            return f"evidence={self.meta.evidence_status}"
        if self.meta.evidence_status in {"partial", "unavailable"}:
            return f"evidence={self.meta.evidence_status}"
        return None

    def _render_status(self) -> str | None:
        parts: list[str] = []
        evidence = self._evidence_status_token()
        if evidence is not None:
            parts.append(evidence)
        if self.meta.quality_summary is not None:
            compat = self.meta.quality_summary.metric_definition_compatibility
            if compat is not None:
                parts.append(f"quality={compat}")
        return " ".join(parts) if parts else None

    def _repr_html_(self) -> None:
        return None

    def _append_evidence_sections(self, card: Card) -> Card:
        if self.meta.blocking_issues:
            card.listing(
                "issues",
                (
                    f"{issue.severity} {issue.kind}: {issue.message}"
                    for issue in self.meta.blocking_issues
                ),
            )
        summary = self.meta.evidence_summary
        if summary is not None:
            if summary.finding_count == 0 and not summary.items:
                card.field("evidence", "no evidence findings emitted")
            else:
                card.field(
                    "evidence",
                    (
                        f"findings={summary.finding_count} items={len(summary.items)} "
                        f"omitted={summary.omitted_count}"
                    ),
                )
                if summary.items:
                    card.listing("evidence items", (item.statement for item in summary.items))
        if any(issue.kind == "evidence_summary_unavailable" for issue in self.meta.blocking_issues):
            card.field("evidence recovery", "inspect canonical records with session.evidence")
        return card

    def _base_card(self) -> Card:
        """Build the shared card header and fields before any preview table."""
        card = Card(identity=self._repr_identity(), available=self._AVAILABLE_ENTRIES)
        status = self._render_status()
        if status is not None:
            card.status(status)
        if self.meta.analysis_purpose:
            card.field("analysis_purpose", self.meta.analysis_purpose)
        self._append_evidence_sections(card)
        return card

    def _card(self) -> Card:
        columns = _display_column_names(self._df.columns)
        return self._base_card().lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )
