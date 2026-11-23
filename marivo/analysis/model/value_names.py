"""Validation of exact user and transported output names."""

from typing import TypeAlias
from unicodedata import category

from marivo.analysis.model.errors import reject

ValuePath: TypeAlias = tuple[str, ...]


def _segment(name: str, location: str, *, canonical_id: str | None = None) -> None:
    if (
        type(name) is not str
        or not name
        or "." in name
        or "\\" in name
        or any(category(character) == "Cc" for character in name)
    ):
        reject(
            "a nonempty name segment without dots, backslashes or control characters",
            repr(name),
            "Rename the value with one nonempty name segment.",
            location,
            canonical_id=canonical_id,
        )


def value_name(name: str, *, location: str, canonical_id: str | None = None) -> ValuePath:
    """Validate one user declaration; generated paths use validate_paths."""
    _segment(name, location, canonical_id=canonical_id)
    if name in ("left_present", "right_present"):
        reject(
            "a value name outside the reserved presence names",
            repr(name),
            "Rename the value; left_present and right_present belong to presence.",
            location,
            canonical_id=canonical_id,
        )
    return (name,)


def validate_paths(
    paths: tuple[ValuePath, ...], *, location: str, canonical_id: str | None = None
) -> None:
    if type(paths) is not tuple:
        reject(
            "immutable ordered name paths",
            repr(paths),
            "Freeze the name paths.",
            location,
            canonical_id=canonical_id,
        )
    for path in paths:
        if type(path) is not tuple or not path:
            reject(
                "a nonempty immutable path",
                repr(path),
                "Preserve each name segment.",
                location,
                canonical_id=canonical_id,
            )
        for segment in path:
            _segment(segment, location, canonical_id=canonical_id)
    if len(set(paths)) != len(paths):
        reject(
            "distinct name paths",
            repr(paths),
            "Remove the repeated name path.",
            location,
            canonical_id=canonical_id,
        )
