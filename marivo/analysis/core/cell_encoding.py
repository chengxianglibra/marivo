"""Closed physical Cell encodings; semantic reasons remain method-owned."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, TypeAlias

from marivo.analysis.core.model import reject

CellTag: TypeAlias = Literal["defined", "null", "undefined", "unknown"]
NonDefinedTag: TypeAlias = Literal["null", "undefined", "unknown"]
_TAGS: tuple[CellTag, ...] = ("defined", "null", "undefined", "unknown")


def invalid(received: str) -> NoReturn:
    reject(
        "one exact method-bound compact Cell encoding",
        received,
        "Re-execute the producing method with its complete Cell binding.",
        "analysis.cell_encoding",
    )
    raise AssertionError("unreachable")


@dataclass(frozen=True, slots=True, order=True)
class CellReason:
    tag: NonDefinedTag
    reason: str

    def __post_init__(self) -> None:
        if self.tag not in _TAGS[1:] or not self.reason:
            invalid("invalid non-Defined tag or empty reason")


@dataclass(frozen=True, slots=True)
class CellBook:
    entries: tuple[CellReason, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(set(self.entries), key=lambda p: (_TAGS.index(p.tag), p.reason)))
        if self.entries != ordered or len(self.entries) > 8191:
            invalid("reason dictionary is not canonical or exceeds int16 capacity")

    @classmethod
    def from_reasons(cls, reasons: tuple[tuple[str, tuple[str, ...]], ...]) -> CellBook:
        pairs: set[CellReason] = set()
        for tag, values in reasons:
            native: NonDefinedTag
            if tag == "null":
                native = "null"
            elif tag == "undefined":
                native = "undefined"
            elif tag == "unknown":
                native = "unknown"
            else:
                invalid("invalid method-owned reason tag")
                continue
            pairs.update(CellReason(native, value) for value in values)
        return cls(tuple(sorted(pairs, key=lambda p: (_TAGS.index(p.tag), p.reason))))

    def code(self, tag: str, reason: str | None) -> int:
        if tag == "defined" and reason is None:
            return 0
        for index, pair in enumerate(self.entries, 1):
            if (pair.tag, pair.reason) == (tag, reason):
                return index * 4 + _TAGS.index(pair.tag)
        invalid(f"undeclared Cell ({tag!r}, {reason!r})")
        raise AssertionError("unreachable")

    def decode(self, code: int) -> tuple[CellTag, str | None]:
        if type(code) is not int or code < 0 or code > 32767:
            invalid(f"invalid int16 Cell code {code!r}")
        if code == 0:
            return "defined", None
        ordinal, tag = divmod(code, 4)
        if ordinal < 1 or ordinal > len(self.entries) or tag == 0:
            invalid(f"undeclared Cell code {code}")
        pair = self.entries[ordinal - 1]
        if _TAGS[tag] != pair.tag:
            invalid(f"Cell code {code} disagrees with its reason dictionary")
        return pair.tag, pair.reason

    def codes(self) -> tuple[int, ...]:
        return tuple(
            index * 4 + _TAGS.index(pair.tag) for index, pair in enumerate(self.entries, 1)
        )

    def remap(self, code: int, target: CellBook) -> int:
        return target.code(*self.decode(code))


@dataclass(frozen=True, slots=True)
class CellFields:
    value: str
    tag: str
    reason: str

    def __post_init__(self) -> None:
        if (
            not all((self.value, self.tag, self.reason))
            or len({self.value, self.tag, self.reason}) != 3
        ):
            invalid("Cell fields must have three distinct logical names")


@dataclass(frozen=True, slots=True)
class KnownCell:
    fields: CellFields
    book: CellBook
    code: int
    kind: Literal["known"] = "known"

    def __post_init__(self) -> None:
        self.book.decode(self.code)


@dataclass(frozen=True, slots=True)
class ValidityCell:
    fields: CellFields
    book: CellBook
    missing_code: int
    kind: Literal["validity"] = "validity"

    def __post_init__(self) -> None:
        if self.book.decode(self.missing_code)[0] == "defined":
            invalid("validity encoding needs one non-Defined state")


@dataclass(frozen=True, slots=True)
class EncodedCell:
    fields: CellFields
    book: CellBook
    state: str
    optional: bool = False
    kind: Literal["encoded"] = "encoded"

    def __post_init__(self) -> None:
        if (
            type(self.optional) is not bool
            or not self.state
            or self.state in (self.fields.value, self.fields.tag, self.fields.reason)
        ):
            invalid("encoded Cell needs a distinct physical state field")


CellEncoding: TypeAlias = KnownCell | ValidityCell | EncodedCell


@dataclass(frozen=True, slots=True)
class CellTable:
    cells: tuple[CellEncoding, ...]
    logical_columns: tuple[str, ...]
    schema: Literal["marivo.analysis.cell_table/v1"] = "marivo.analysis.cell_table/v1"

    def __post_init__(self) -> None:
        names = tuple(
            name
            for cell in self.cells
            for name in (cell.fields.value, cell.fields.tag, cell.fields.reason)
        )
        if len(set(names)) != len(names) or not set(names) <= set(self.logical_columns):
            invalid("Cell slots differ from their exact logical table fields")
        states = tuple(cell.state for cell in self.cells if isinstance(cell, EncodedCell))
        if len(set(states)) != len(states) or set(states) & set(self.logical_columns):
            invalid("physical state fields overlap logical fields or other Cells")
        if len(set(self.logical_columns)) != len(self.logical_columns):
            invalid("duplicate logical table fields")


def physical_fields(cell: CellEncoding) -> tuple[str, ...]:
    return (
        (cell.fields.value, cell.state) if isinstance(cell, EncodedCell) else (cell.fields.value,)
    )
