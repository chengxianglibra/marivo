"""Closed owned method tables; source-free records with exact logical digests."""

from __future__ import annotations

from hashlib import sha256

import pandas as pd

from marivo.analysis.core.artifact_data import (
    METHOD_PAYLOAD_SCHEMA,
    OutputAttachment,
    SavedAttachment,
    SavedFrameMethodFacts,
)
from marivo.analysis.core.frame_method_facts import (
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodParent,
)
from marivo.analysis.materialization.contracts import canonical_json, parse_json
from marivo.analysis.materialization.terminal_protocol import (
    decode_method_record,
    encode_method_record,
    invalid,
    method_facts_header,
)


def _records(facts: FrameMethodFacts) -> tuple[str, ...]:
    return tuple(encode_method_record(parent) for parent in facts.parents) + tuple(
        encode_method_record(coordinate) for coordinate in facts.coordinates
    )


def _digest(facts: FrameMethodFacts) -> str:
    value = canonical_json(
        {
            "header": parse_json(method_facts_header(facts)),
            "records": [parse_json(record) for record in _records(facts)],
        }
    )
    return sha256(value.encode()).hexdigest()


def method_payload(facts: FrameMethodFacts) -> OutputAttachment:
    """Encode already complete facts into the fixed typed owned table."""
    if type(facts) is not FrameMethodFacts:
        raise invalid("expected complete immutable method facts")
    records = _records(facts)
    rows = pd.DataFrame(
        {
            "entry": pd.Series(range(len(records)), dtype="int64[pyarrow]"),
            "kind": pd.Series(
                ["parent"] * len(facts.parents) + ["coordinate"] * len(facts.coordinates),
                dtype="string[pyarrow]",
            ),
            "record": pd.Series(records, dtype="string[pyarrow]"),
        }
    )
    return OutputAttachment("method." + facts.origin_result_id, METHOD_PAYLOAD_SCHEMA, rows)


def saved_method_facts(facts: FrameMethodFacts, payload: SavedAttachment) -> SavedFrameMethodFacts:
    """Describe one newly owned method receipt without reading its physical files."""
    return SavedFrameMethodFacts(
        "attribute",
        facts.origin_result_id,
        facts.output_schema,
        facts.parent_axis_count,
        len(facts.parents),
        len(facts.coordinates),
        sum(len(item.rows) for item in facts.coordinates),
        _digest(facts),
        payload,
    )


def decode_method_payload(saved: SavedFrameMethodFacts, rows: pd.DataFrame) -> FrameMethodFacts:
    """Decode checked physical rows and validate their entire closed logical value."""
    if (
        type(saved) is not SavedFrameMethodFacts
        or not isinstance(rows, pd.DataFrame)
        or tuple(rows.columns) != ("entry", "kind", "record")
        or len(rows) != saved.parent_count + saved.coordinate_count
    ):
        raise invalid("method payload table differs from its exact descriptor")
    parents: list[FrameMethodParent] = []
    coordinates: list[FrameMethodCoordinate] = []
    for index, (entry, kind, record) in enumerate(rows.itertuples(index=False, name=None)):
        if type(entry) is not int or entry != index or type(record) is not str:
            raise invalid("method entries must be exact contiguous integers with closed records")
        if index < saved.parent_count:
            if kind != "parent":
                raise invalid("method parent rows must precede coordinate rows")
            parents.append(decode_method_record(record, FrameMethodParent))
        else:
            if kind != "coordinate":
                raise invalid("method coordinate rows must follow all parent rows")
            coordinates.append(decode_method_record(record, FrameMethodCoordinate))
    if sum(len(item.rows) for item in coordinates) != saved.coordinate_row_count:
        raise invalid("method coordinate row count differs from its descriptor")
    facts = FrameMethodFacts(
        kind="attribute",
        origin_result_id=saved.origin_result_id,
        output_schema=saved.output_schema,
        coordinates=tuple(coordinates),
        parent_axis_count=saved.parent_axis_count,
        parents=tuple(parents),
    )
    if _digest(facts) != saved.facts_digest:
        raise invalid("method logical digest differs from its exact descriptor")
    return facts
