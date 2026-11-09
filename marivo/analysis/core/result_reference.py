"""Immutable identities of explicitly persisted results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from marivo.render import Card, RenderableResult


class FrameReferenceWire(TypedDict):
    schema: Literal["marivo.frame_ref/v2"]
    store_id: str
    session_id: str
    artifact_id: str


class DataFrameReferenceWire(TypedDict):
    schema: Literal["marivo.dataframe_ref/v2"]
    project_root: str
    result_id: str


@dataclass(frozen=True, init=False, repr=False)
class FrameReference(RenderableResult):
    """Exact persisted Frame identity, acquired from ``saved.ref``.

    Fields: store_id, session_id and artifact_id identify one saved result.
    Example: ``saved.ref.to_dict()`` exports the reference for a consumer.
    Constraints: Immutable; contains no service URL, credentials or producer.
    """

    store_id: str
    session_id: str
    artifact_id: str

    def __new__(cls) -> FrameReference:
        raise TypeError("Acquire a FrameReference from a persisted AnalysisFrame.ref.")

    @classmethod
    def _create(cls, store_id: str, session_id: str, artifact_id: str) -> FrameReference:
        value = object.__new__(cls)
        object.__setattr__(value, "store_id", store_id)
        object.__setattr__(value, "session_id", session_id)
        object.__setattr__(value, "artifact_id", artifact_id)
        return value

    @property
    def kind(self) -> Literal["analysis_frame"]:
        """The fixed result kind. Example: ``saved.ref.kind``. Reads no data."""
        return "analysis_frame"

    def to_dict(self) -> FrameReferenceWire:
        """Return a fresh serializable identity dictionary.

        Returns: Store, Session and Artifact identities with their schema version.
        Example: ``reference.to_dict()``. No arguments or I/O; edits to the
        returned dictionary do not change this immutable reference.
        """
        return {
            "schema": "marivo.frame_ref/v2",
            "store_id": self.store_id,
            "session_id": self.session_id,
            "artifact_id": self.artifact_id,
        }

    def _repr_identity(self) -> str:
        return f"FrameReference kind={self.kind} artifact_id={self.artifact_id!r}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=("to_dict()",))
        for name, value in self.to_dict().items():
            card.field(name, str(value))
        return card


@dataclass(frozen=True, init=False, repr=False)
class DataFrameReference(RenderableResult):
    """Exact local table reference returned by ``persist_dataframe``.

    Fields: project_root is the captured absolute project directory; result_id
    identifies its fixed snapshot. Together they locate one saved table.
    Example: ``mv.persist_dataframe(table).to_dict()``.
    Constraints: Immutable local locator; no inferred Session or semantic metadata.
    Read with mv.read_dataframe(reference) on a filesystem containing that path.
    """

    project_root: Path
    result_id: str

    def __new__(cls) -> DataFrameReference:
        raise TypeError("Acquire a DataFrameReference from persist_dataframe().")

    @classmethod
    def _create(cls, project_root: Path, result_id: str) -> DataFrameReference:
        value = object.__new__(cls)
        object.__setattr__(value, "project_root", project_root)
        object.__setattr__(value, "result_id", result_id)
        return value

    @property
    def kind(self) -> Literal["dataframe"]:
        """The fixed result kind. Example: ``reference.kind``. Reads no data."""
        return "dataframe"

    def to_dict(self) -> DataFrameReferenceWire:
        """Return a fresh serializable table reference.

        Returns: The absolute project location, result identity and schema version.
        Example: ``reference.to_dict()``. No arguments or I/O; edits to the
        returned dictionary do not change this immutable reference.
        """
        return {
            "schema": "marivo.dataframe_ref/v2",
            "project_root": str(self.project_root),
            "result_id": self.result_id,
        }

    def _repr_identity(self) -> str:
        return f"DataFrameReference kind={self.kind} result_id={self.result_id!r}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=("to_dict()",))
        for name, value in self.to_dict().items():
            card.field(name, str(value))
        return card
