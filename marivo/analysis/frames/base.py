"""Base typed analysis frame contracts."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import importlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePath
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from marivo.analysis.attribution_contract import AttributeAdmissionV1
from marivo.analysis.candidate_lineage import CandidateOrigin, merge_candidate_origins
from marivo.analysis.errors import (
    AnalysisRepair,
    FrameMutationError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    ArtifactIssue,
    EvidenceScope,
    QualitySummary,
)
from marivo.analysis.lineage import Lineage
from marivo.refs import SemanticKind
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card, RenderableResult, result_repr
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS

CURRENT_ARTIFACT_SCHEMA_VERSION: Literal["analysis-artifact/v7"] = "analysis-artifact/v7"
_ARTIFACT_SEMANTIC_INPUT_LIMIT = 12


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


class ArtifactSemanticInput(BaseModel):
    """One exact semantic input retained by an artifact with its acquisition path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    semantic_kind: SemanticKind
    semantic_path: str
    output_column: str | None = None
    acquisition: str
    help_target: str


class ArtifactPrecondition(BaseModel):
    """Mechanical precondition attached to an affordance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    status: ArtifactPreconditionStatus
    reason: str | None = None
    repair: AnalysisRepair | None = None
    repair_options: tuple[AnalysisRepair, ...] = ()

    @model_validator(mode="after")
    def _validate_repairs(self) -> ArtifactPrecondition:
        has_repair = self.repair is not None
        has_options = bool(self.repair_options)
        if has_repair and has_options:
            raise ValueError("repair and repair_options are mutually exclusive")
        if self.status == "fail" and not (has_repair or has_options):
            raise ValueError("failed precondition requires repair or repair_options")
        repairs = (self.repair,) if self.repair is not None else self.repair_options
        for item in repairs:
            if not item.action.strip():
                raise ValueError("precondition repair action must be non-empty")
            if item.kind == "retry" and not (item.snippet and item.snippet.strip()):
                raise ValueError("retry precondition repair requires a runnable snippet")
        return self


class ArtifactInputRequirement(BaseModel):
    """One public input role preserved from the capability registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter: str
    accepted_families: tuple[str, ...]
    bindable_from_current_artifact: bool


class ArtifactCallOption(BaseModel):
    """One exact runnable call option owned by an artifact affordance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    semantic_refs: tuple[str, ...] = ()
    snippet: str


class ArtifactAffordance(BaseModel):
    """Mechanical compatibility entry, not a recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    public_entrypoint: str
    help_target: str
    input_requirements: tuple[ArtifactInputRequirement, ...] = ()
    preconditions: tuple[ArtifactPrecondition, ...] = ()
    expected_output_family: str | None = None
    call_options: tuple[ArtifactCallOption, ...] = ()


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
    semantic_inputs: tuple[ArtifactSemanticInput, ...] = ()
    semantic_inputs_omitted: int = Field(default=0, ge=0)
    issues: tuple[ArtifactIssue, ...] = ()
    affordances: tuple[ArtifactAffordance, ...] = ()
    boundary_ports: tuple[ArtifactBoundaryPort, ...] = ()
    attribute_admission: AttributeAdmissionV1 | None = None
    row_arithmetic: (
        Literal[
            "not_additive_across_resolutions",
            "additive_once_per_comparison_bucket",
        ]
        | None
    ) = None

    @computed_field  # type: ignore[prop-decorator]  # Pydantic wraps this property.
    @property
    def output_columns(self) -> tuple[str, ...]:
        """Return the exact ordered public names from the artifact schema."""
        return tuple(column.name for column in self.artifact_schema.columns)

    def _repr_identity(self) -> str:
        return (
            f"ArtifactContract kind={self.kind} ref={self.ref} affordances={len(self.affordances)}"
        )

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        """Render the bounded mechanical contract without reading artifact rows."""
        return _artifact_contract_card(self).render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print the bounded mechanical contract."""
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())


class ArtifactState(BaseModel):
    """Baseline runtime facts for a materialized artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    materialization: ArtifactMaterialization
    content_hash: str | None = None


class BaseFrameMeta(BaseModel):
    """Shared ownership and provenance fields for every frame family."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    artifact_schema_version: Literal["analysis-artifact/v6", "analysis-artifact/v7"] = (
        CURRENT_ARTIFACT_SCHEMA_VERSION
    )
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
    analysis_scope: EvidenceScope | None = None
    quality_summary: QualitySummary | None = None
    evidence_digest: ArtifactDigest | None = None
    issues: tuple[ArtifactIssue, ...] = ()
    content_hash: str | None = None
    candidate_origins: tuple[CandidateOrigin, ...] = ()

    @model_validator(mode="after")
    def _validate_candidate_origins(self) -> BaseFrameMeta:
        normalized = merge_candidate_origins(self.candidate_origins)
        if normalized != self.candidate_origins:
            raise ValueError("candidate_origins must already be ordered and de-duplicated")
        return self


@dataclass(frozen=True, slots=True)
class _FrameAuxiliaryTable:
    """One private frame-owned table written beside the public artifact rows."""

    filename: str
    dataframe: pd.DataFrame

    def __post_init__(self) -> None:
        path = PurePath(self.filename)
        if (
            not self.filename
            or path.is_absolute()
            or len(path.parts) != 1
            or path.name != self.filename
        ):
            raise ValueError("auxiliary table filename must be one safe basename")


@dataclass(frozen=True, slots=True)
class _FrameAuxiliaryReceipt:
    """Persistence receipt bound into shape-specific frame metadata."""

    filename: str
    row_count: int
    byte_size: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class _ArtifactSemanticBinding:
    """Private role-preserving semantic input used to build public guidance."""

    role: str
    semantic_kind: SemanticKind
    semantic_path: str
    output_column: str | None = None


_CATALOG_COLLECTION_BY_KIND: dict[SemanticKind, str] = {
    member.kind: member.property_name for member in CATALOG_MEMBER_CONTRACTS
}


def _artifact_semantic_input(binding: _ArtifactSemanticBinding) -> ArtifactSemanticInput:
    collection = _CATALOG_COLLECTION_BY_KIND[binding.semantic_kind]
    return ArtifactSemanticInput(
        role=binding.role,
        semantic_kind=binding.semantic_kind,
        semantic_path=binding.semantic_path,
        output_column=binding.output_column,
        acquisition=(f'session.catalog.{collection}.get("{binding.semantic_path}")'),
        help_target=f"analysis.catalog.{collection}",
    )


def _visible_precondition(precondition: ArtifactPrecondition) -> bool:
    """Return True when a precondition is visible (has actionable content).

    A passing precondition is visible only when it carries a non-empty reason.
    A failing precondition is visible only when it carries a repair with a
    non-empty action.
    """
    if precondition.status == "pass":
        return bool(precondition.reason and precondition.reason.strip())
    if precondition.repair is not None:
        return _visible_repair(precondition.repair)
    return bool(precondition.repair_options) and all(
        _visible_repair(repair) for repair in precondition.repair_options
    )


def _visible_repair(repair: AnalysisRepair) -> bool:
    if not repair.action.strip():
        return False
    return repair.kind != "retry" or bool(repair.snippet and repair.snippet.strip())


def _affordance_visible(affordance: ArtifactAffordance) -> bool:
    """Suppress only failed preconditions that lack executable or inspectable repair."""
    return not any(
        precondition.status == "fail" and not _visible_precondition(precondition)
        for precondition in affordance.preconditions
    )


def _artifact_contract_card(contract: ArtifactContract) -> Card:
    """Build the bounded contract card from already-materialized typed facts."""
    semantic_shape = contract.artifact_schema.semantic_shape or "unspecified"
    card = (
        Card(
            identity=contract._repr_identity(),
            available=(
                ".artifact_schema",
                ".output_columns",
                ".semantic_inputs",
                ".semantic_inputs_omitted",
                ".issues",
                ".affordances",
                ".boundary_ports",
                ".model_dump()",
                ".render()",
                ".show()",
            ),
        )
        .field("canonical_state", "canonical" if contract.is_canonical else "non_canonical")
        .field("receiver", "frame")
        .field("semantic_shape", semantic_shape)
        .field("output_columns", repr(list(contract.output_columns)))
        .listing(
            "columns",
            (
                f"{column.name}: dtype={column.dtype} nullable={str(column.nullable).lower()} "
                f"role={column.role}"
                for column in contract.artifact_schema.columns
            ),
        )
    )
    if contract.semantic_inputs:
        card.listing(
            "semantic inputs",
            (
                f"{item.role}: {item.semantic_kind.value}:{item.semantic_path}"
                + (f" output_column={item.output_column}" if item.output_column is not None else "")
                + f"; acquire={item.acquisition}; help={item.help_target}"
                for item in contract.semantic_inputs
            ),
        )
    if contract.semantic_inputs_omitted:
        card.field("semantic_inputs_omitted", str(contract.semantic_inputs_omitted))
    if contract.issues:
        from marivo.analysis.evidence.summary import render_artifact_issue

        card.listing(
            "issues/blockers",
            (
                f"{issue.severity} {issue.kind}: {render_artifact_issue(issue)}"
                for issue in contract.issues
            ),
        )
    if contract.attribute_admission is not None:
        admission = contract.attribute_admission
        admission_text = (
            f"status={admission.status} attribution_shape={admission.attribution_shape}"
        )
        if admission.status == "blocked":
            admission_text += f" blocker={admission.blocker}"
        else:
            admission_text += " multiple_axes=" + "|".join(admission.mode.multiple_axes)
        card.field("attribute_admission", admission_text)
    if contract.row_arithmetic is not None:
        card.field("row_arithmetic", contract.row_arithmetic)

    affordances = tuple(
        affordance for affordance in contract.affordances if _affordance_visible(affordance)
    )
    card.listing("typed affordances", (_render_affordance(item) for item in affordances))
    precondition_lines = tuple(
        line
        for affordance in affordances
        for precondition in affordance.preconditions
        if _visible_precondition(precondition)
        for line in _render_precondition(affordance.capability_id, precondition)
    )
    if precondition_lines:
        card.listing("preconditions and repairs", precondition_lines)
    card.listing(
        "terminal boundary ports",
        (
            f"{port.capability_id}: {port.public_entrypoint}; help={port.help_target}; "
            f"preserves={', '.join(port.preserves)}; "
            f"does_not_preserve={', '.join(port.does_not_preserve)}"
            for port in contract.boundary_ports
        ),
    )
    return card


def _render_affordance(affordance: ArtifactAffordance) -> str:
    bindings = "; ".join(
        (
            f"{requirement.parameter}={','.join(requirement.accepted_families)} "
            f"(current_artifact={str(requirement.bindable_from_current_artifact).lower()})"
        )
        for requirement in affordance.input_requirements
    )
    base = (
        f"{affordance.capability_id}: {affordance.public_entrypoint}; "
        f"help={affordance.help_target}; inputs={bindings or 'none'}; "
        f"output={affordance.expected_output_family or 'none'}"
    )
    if not affordance.call_options:
        return base
    options = " | ".join(f"{option.label}: {option.snippet}" for option in affordance.call_options)
    return f"{base}; options={options}"


def _render_precondition(
    capability_id: str,
    precondition: ArtifactPrecondition,
) -> tuple[str, ...]:
    reason = precondition.reason or "none"
    lines = [f"{capability_id}.{precondition.check}: status={precondition.status}; reason={reason}"]
    repairs = (
        (precondition.repair,) if precondition.repair is not None else precondition.repair_options
    )
    for index, repair in enumerate(repairs, start=1):
        snippet = repair.snippet.replace("\n", "\\n") if repair.snippet is not None else "none"
        target = repair.help_target
        help_target = (
            f"{target.surface}:{target.canonical_id}"
            if target.canonical_id is not None
            else target.surface
        )
        option = f" option={index}" if len(repairs) > 1 else ""
        lines.append(
            f"{capability_id}.{precondition.check}{option}: repair={repair.kind}; "
            f"action={repair.action}; help={help_target}; snippet={snippet}"
        )
    return tuple(lines)


def _output_family_str(desc: Any) -> str:
    """Return a string representation of a capability descriptor's output family."""
    model_module = importlib.import_module("marivo.analysis._capabilities.model")
    output_contract = getattr(desc, "output_contract", None)
    if output_contract is not None:
        return str(output_contract.render())
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
    """Call marivo.help(BaseFrame) for its public consumption contract."""

    _df: pd.DataFrame
    meta: BaseFrameMeta
    _auxiliary_frames: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)

    _NEXT_INTENTS: tuple[str, ...] = ()
    _AVAILABLE_ENTRIES: tuple[str, ...] = (
        ".show()",
        ".contract()",
        ".to_pandas()",
    )

    def __post_init__(self) -> None:
        self._assert_row_count_consistent()

    def _assert_row_count_consistent(self) -> None:
        materialized = int(self._df.shape[0])
        if self.meta.row_count != materialized:
            raise ValueError(
                "frame row count mismatch: "
                f"meta.row_count={self.meta.row_count}, materialized={materialized}"
            )

    @property
    def ref(self) -> str:
        return self.meta.ref

    @property
    def id(self) -> str:
        """Alias for the canonical persisted artifact ``ref``."""
        return self.ref

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
    def evidence_status(self) -> Literal["complete", "partial", "unavailable"]:
        return self.meta.evidence_status

    @property
    def evidence_digest(self) -> ArtifactDigest | None:
        return self.meta.evidence_digest

    @property
    def state(self) -> ArtifactState:
        return ArtifactState(
            materialization="materialized",
            content_hash=self.meta.content_hash,
        )

    def _build_schema(self) -> ArtifactSchema:
        """Build the schema descriptor embedded in the artifact contract."""
        public_columns = self._public_column_names()
        columns = [
            ArtifactColumn(
                name=name,
                dtype=str(dtype),
                nullable=bool(self._df.iloc[:, idx].isna().any()) if len(self._df) else True,
                role=_column_role(str(self._df.columns[idx])),
            )
            for idx, (name, dtype) in enumerate(zip(public_columns, self._df.dtypes, strict=True))
        ]
        raw_shape = getattr(self.meta, "semantic_kind", None)
        return ArtifactSchema(
            columns=columns,
            semantic_shape=raw_shape if isinstance(raw_shape, str) else None,
        )

    def _semantic_input_bindings(self) -> tuple[_ArtifactSemanticBinding, ...]:
        """Return exact semantic inputs retained by this artifact family."""
        return ()

    def _build_semantic_inputs(self) -> tuple[ArtifactSemanticInput, ...]:
        """Build bounded acquisition guidance without reading artifact rows."""
        inputs: list[ArtifactSemanticInput] = []
        seen: set[tuple[str, SemanticKind, str, str | None]] = set()
        for binding in self._semantic_input_bindings():
            key = (
                binding.role,
                binding.semantic_kind,
                binding.semantic_path,
                binding.output_column,
            )
            if key in seen:
                continue
            seen.add(key)
            inputs.append(_artifact_semantic_input(binding))
        return tuple(inputs)

    def _bounded_semantic_inputs(self) -> tuple[tuple[ArtifactSemanticInput, ...], int]:
        semantic_inputs = self._build_semantic_inputs()
        retained = semantic_inputs[:_ARTIFACT_SEMANTIC_INPUT_LIMIT]
        return retained, len(semantic_inputs) - len(retained)

    def contract(self) -> ArtifactContract:
        """Return the mechanical consumption contract for the artifact.

        Affordances are mechanical compatibility entries derived from the
        capability registry's reverse edges (``constructor_consumers``),
        not recommendations.

        Returns:
            ArtifactContract listing artifact_schema, issues,
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
        validation_module = importlib.import_module("marivo.analysis._capabilities.validation")

        family = type(self).__name__
        consumer_ids = registry.constructor_consumers.get(family, ())
        affordances: list[ArtifactAffordance] = []
        for cap_id in consumer_ids:
            if cap_id == "boundary.to_pandas":
                continue
            desc = registry.by_id(cap_id)
            assert isinstance(desc, operator_cls)
            artifact_parameters = tuple(
                parameter
                for parameter, families in desc.accepted_inputs.items()
                if family in {str(item) for item in families}
            )
            if artifact_parameters and not any(
                validation_module.evaluate_artifact_admission(
                    desc.id,
                    parameter,
                    self,
                ).allowed
                for parameter in artifact_parameters
            ):
                continue
            hidden_parameters: set[str] = set()
            semantic_kind = getattr(self.meta, "semantic_kind", None)
            if desc.id == "attribute" and family == "DeltaFrame" and semantic_kind != "funnel":
                hidden_parameters.add("target")
            if desc.id == "compare" and family == "EventFrame" and semantic_kind == "funnel":
                hidden_parameters.add("alignment")
            input_requirements = tuple(
                ArtifactInputRequirement(
                    parameter=parameter,
                    accepted_families=tuple(sorted(str(item) for item in families)),
                    bindable_from_current_artifact=family in {str(item) for item in families},
                )
                for parameter, families in sorted(desc.accepted_inputs.items())
                if parameter not in hidden_parameters
            )
            output_family = _output_family_str(desc)
            affordance = ArtifactAffordance(
                capability_id=desc.id,
                public_entrypoint=desc.public_entrypoint,
                help_target=desc.help_target,
                input_requirements=input_requirements,
                expected_output_family=output_family,
            )
            # Suppress affordances with failed preconditions that lack visible repair.
            if _affordance_visible(affordance):
                affordances.append(affordance)
        artifact_schema = self._build_schema()
        semantic_inputs, semantic_inputs_omitted = self._bounded_semantic_inputs()
        return ArtifactContract(
            kind=self.meta.kind,
            ref=self.meta.ref,
            is_canonical=True,
            artifact_schema=artifact_schema,
            semantic_inputs=semantic_inputs,
            semantic_inputs_omitted=semantic_inputs_omitted,
            issues=self.meta.issues,
            affordances=tuple(affordances),
            boundary_ports=tuple(_build_boundary_ports(registry)),
        )

    def _dataframe_copy(self) -> pd.DataFrame:
        """Return an internal defensive copy without public export shaping."""
        return self._df.copy()

    def _auxiliary_tables(self) -> tuple[_FrameAuxiliaryTable, ...]:
        """Return private persisted payloads owned by this frame.

        Base frames own no auxiliary data. Shape-specific frames may override
        this hook, but private tables never participate in public row reads.
        """
        if self._auxiliary_frames:
            raise ValueError(f"{type(self).__name__} does not accept auxiliary tables")
        return ()

    def _bind_auxiliary_receipts(
        self,
        meta: BaseFrameMeta,
        receipts: tuple[_FrameAuxiliaryReceipt, ...],
    ) -> BaseFrameMeta:
        """Bind written auxiliary hashes into shape-specific metadata."""
        if receipts:
            raise ValueError(f"{type(self).__name__} does not accept auxiliary receipts")
        return meta

    def _public_column_names(self) -> list[str]:
        """Return the column names shared by every public frame read path."""
        return _display_column_names(self._df.columns)

    def _public_dataframe_view(self) -> pd.DataFrame:
        """Return a shallow view with public column names for bounded selection."""
        df = self._df.copy(deep=False)
        df.columns = self._public_column_names()
        return df

    def _export_dataframe(self) -> pd.DataFrame:
        """Return the DataFrame shape exposed by the terminal pandas boundary."""
        return self._public_dataframe_view().copy()

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensive copy shaped for terminal pandas consumption."""
        return self._export_dataframe()

    def __getitem__(self, key: Any) -> Any:
        return self._public_dataframe_view()[key].copy()

    @property
    def shape(self) -> tuple[int, int]:
        return self._df.shape

    @property
    def row_count(self) -> int:
        """Return materialized rows and fail closed on persisted metadata drift."""
        self._assert_row_count_consistent()
        return int(self._df.shape[0])

    @property
    def columns(self) -> list[str]:
        return self._public_column_names()

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
        columns = self._public_column_names()
        for row in self._df.itertuples(index=False, name=None):
            yield tuple(str(_preview_cell(value)) for value in row[: len(columns)])

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} ref={self.meta.ref} rows={self.meta.row_count}"

    def _evidence_status_token(self) -> str | None:
        digest_unavailable = any(
            issue.kind == "evidence_digest_unavailable" for issue in self.meta.issues
        )
        if digest_unavailable:
            return f"evidence={self.meta.evidence_status} digest=unavailable"
        if self.meta.evidence_digest is not None:
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
        if self.meta.issues:
            from marivo.analysis.evidence.summary import render_artifact_issue

            card.listing(
                "issues",
                (
                    f"{issue.severity} {issue.kind}: {render_artifact_issue(issue)}"
                    for issue in self.meta.issues
                ),
            )
        digest = self.meta.evidence_digest
        if digest is not None:
            if not digest.items:
                card.field("evidence", "no evidence findings emitted")
            else:
                omitted_items = digest.omissions.omitted_items
                full_rows_hint = "; call .to_pandas() for all rows" if omitted_items else ""
                card.field(
                    "evidence",
                    f"items={len(digest.items)} omitted={omitted_items}{full_rows_hint}",
                )
                from marivo.analysis.evidence.summary import render_digest_item

                card.listing("evidence items", (render_digest_item(item) for item in digest.items))
            if digest.boundaries:
                card.listing(
                    "inference boundaries",
                    (boundary.kind for boundary in digest.boundaries),
                )
        if any(issue.kind == "evidence_digest_unavailable" for issue in self.meta.issues):
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
        self._append_artifact_interface_sections(card)
        self._append_evidence_sections(card)
        return card

    def _append_artifact_interface_sections(self, card: Card) -> Card:
        """Append actual public columns and exact semantic acquisition guidance."""
        card.field("output_columns", repr(self.columns))
        semantic_inputs, semantic_inputs_omitted = self._bounded_semantic_inputs()
        if semantic_inputs:
            card.listing(
                "semantic inputs",
                (
                    f"{item.role}: {item.semantic_kind.value}:{item.semantic_path}"
                    + (
                        f" output_column={item.output_column}"
                        if item.output_column is not None
                        else ""
                    )
                    + f"; acquire={item.acquisition}"
                    for item in semantic_inputs
                ),
            )
        if semantic_inputs_omitted:
            card.field("semantic_inputs_omitted", str(semantic_inputs_omitted))
        return card

    def _card(self) -> Card:
        columns = self._public_column_names()
        return self._base_card().lazy_table(
            columns=columns,
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )
