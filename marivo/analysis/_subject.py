"""Producer-owned Subject mappings; no arbitrary user-authored maps."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.core.model import DomainSignature, SubjectPart, reject
from marivo.refs import EntityKind, Ref


@dataclass(frozen=True, slots=True, init=False, repr=False)
class SubjectBinding:
    """Exact retained mapping from instances to complete Subject identities."""

    _domain: DomainSignature
    _part: SubjectPart

    def __init__(self) -> None:
        reject(
            "a producer-owned SubjectBinding",
            "direct construction",
            "Acquire the binding from relation.subject_binding.",
            "analysis.subject_binding",
        )

    @property
    def entity(self) -> Ref[EntityKind]:
        """Return the retained Subject Entity.

        Args: None.
        Returns: The exact Entity Ref owning Subject identities.
        Example: ``entity = values.subject_binding.entity``.
        Constraints: The producer owns the mapping; this reads no business rows.
        """
        return self._part.entity_ref

    def __repr__(self) -> str:
        return f"<SubjectBinding kind=subject entity={self.entity.path}; use .show()>"

    def show(self) -> None:
        """Display the retained mapping identity.

        Args: None.
        Returns: None.
        Example: ``relation.subject_binding.show()``.
        Constraints: The producer owns the full instance and Subject key mapping.
        """
        print(
            f"{self!r}\nInstance keys: {len(self._domain.instance_key)}; Subject keys: {len(self._part.subject_key)}; total: {self._part.total}"
        )


def subject_binding(domain: DomainSignature, part: SubjectPart) -> SubjectBinding:
    """Capture an already derived immutable Subject mapping."""
    result = object.__new__(SubjectBinding)
    object.__setattr__(result, "_domain", domain)
    object.__setattr__(result, "_part", part)
    return result
