"""Read-only stage metadata shared by compilation and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol

from typing_extensions import Never, TypeVar

from marivo._kernel.input_context import Owner
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.errors import reject


class CheckedStageInput(Protocol):
    """Checked columns and keys of a retained invocation input."""

    @property
    def columns(self) -> tuple[DataColumn, ...]: ...

    @property
    def keys(self) -> tuple[str, ...]: ...


# An empty plan carries no inputs. When populated, the type tracks the
# original carrier through compilation so execution receives it unchanged.
CheckedStageInputT = TypeVar(
    "CheckedStageInputT", bound=CheckedStageInput, covariant=True, default=Never
)


@dataclass(frozen=True, slots=True)
class CheckReceipt:
    """One completed check tied to its exact Query stage."""

    check_id: str
    stage_id: str


class FrameIdentity:
    """Nominal marker for admitted AnalysisFrame handles."""

    __slots__ = ()


class StoredFrameIdentity:
    """Nominal marker for an exact, independently reopenable Frame descriptor."""

    __slots__ = ()


class _FrameOwner(Protocol):
    @property
    def owner(self) -> Owner: ...


SchemaT_co = TypeVar("SchemaT_co", covariant=True)
BackingT_co = TypeVar("BackingT_co", covariant=True)
LocalT_co = TypeVar("LocalT_co", bound=_FrameOwner, covariant=True)
LineageT_co = TypeVar("LineageT_co", covariant=True)
DerivationT_co = TypeVar("DerivationT_co", covariant=True)


class FrameNodeLike(Protocol[SchemaT_co, BackingT_co, LocalT_co, LineageT_co, DerivationT_co]):
    """Read-only Frame shape supplied to invocation capture and lineage."""

    @property
    def _input_id(self) -> str: ...
    @property
    def _schema(self) -> SchemaT_co: ...
    @property
    def _backing(self) -> BackingT_co: ...
    @property
    def _local(self) -> LocalT_co: ...
    @property
    def lineage(self) -> LineageT_co: ...
    @property
    def _derivation(self) -> DerivationT_co: ...


_KeyFrame = FrameNodeLike[object, object, _FrameOwner, object, object]
KeyFrameT_co = TypeVar("KeyFrameT_co", bound=_KeyFrame, covariant=True)
KeyFrameT = TypeVar("KeyFrameT", bound=_KeyFrame)


@dataclass(frozen=True, slots=True)
class FrameKeyInput(Generic[KeyFrameT_co]):
    """The exact selected Frame, retained until the consuming source is evaluated."""

    input_id: str
    frame: KeyFrameT_co

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameIdentity) or self.input_id != self.frame._input_id:
            reject(
                "a Frame key input with its exact input identity",
                repr(self.input_id),
                "Retain the selected Frame with its own input ID.",
                "frame.population",
            )


def _compatible(left: _KeyFrame, right: _KeyFrame) -> bool:
    if left._local.owner != right._local.owner or left._schema != right._schema:
        return False
    if left is right:
        return True
    if isinstance(left._backing, StoredFrameIdentity) and isinstance(
        right._backing, StoredFrameIdentity
    ):
        # Stored binding IDs are stable across metadata-only opens. Their full
        # committed descriptors, not Python handle identity, authorize sharing.
        return left._backing == right._backing
    return False


def merge_frame_key_inputs(
    *groups: tuple[FrameKeyInput[KeyFrameT], ...], owner: Owner | None = None
) -> tuple[FrameKeyInput[KeyFrameT], ...]:
    """Retain compatible exact inputs once, without reading stored payloads."""
    result: dict[str, FrameKeyInput[KeyFrameT]] = {}
    expected = owner
    for group in groups:
        for item in group:
            if expected is None:
                expected = item.frame._local.owner
            if item.frame._local.owner != expected:
                reject(
                    "Frame key inputs owned by one Store and Session",
                    item.input_id,
                    "Build the Population and its consumer in the Frame's owning Session.",
                    "frame.population",
                )
            previous = result.get(item.input_id)
            if previous is not None and not _compatible(previous.frame, item.frame):
                reject(
                    "one compatible exact descriptor per Frame input identity",
                    item.input_id,
                    "Reopen the same committed Artifact descriptor or use distinct input bindings.",
                    "frame.population",
                )
            result.setdefault(item.input_id, item)
    return tuple(result.values())
