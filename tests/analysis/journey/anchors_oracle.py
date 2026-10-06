"""Independent per-instance identity and numeric oracle; no production reducers."""

from decimal import Decimal
from fractions import Fraction

import pyarrow as pa

from tests.analysis.lifecycle.lifecycle_fixtures import START, keys


def expected(values, subject, occurrence, *, calendar=False, first=False):
    members, occurrences = keys(subject, "sid", 3), keys(occurrence, "oid", len(values))
    result = []
    seen = set()
    for index, (sid, kind, point, sequence) in enumerate(values):
        if kind != "started" or not 0 <= point < 100 or (first and sid in seen):
            continue
        seen.add(sid)
        bound = point + (86400 if calendar else 10)
        active = [
            row[3]
            for j, row in enumerate(values)
            if row[0] == sid
            and j != index
            and point <= row[2] < bound
            and (row[2] > point or row[3] > sequence)
        ]
        other = [row[3] for row in values if row[0] == sid and point <= row[2] + 2 < bound]
        total, integer = sum(active), sum(2**53 + amount for amount in active)
        denominator = sum(2**53 + amount for amount in other)
        result.append(
            (
                (
                    *(column[sid] for column in members.values()),
                    "commerce.started",
                    *(column[index] for column in occurrences.values()),
                ),
                [
                    len(active),
                    integer,
                    float(total),
                    Decimal(total) * Decimal("1.000001"),
                    total,
                    1.0 if active else None,
                    1.0 if active else None,
                    Decimal("1.000000") if active else None,
                    1.0 if active else None,
                    float(total + sum(other)),
                    float(Fraction(integer, denominator)) if denominator else None,
                ],
            )
        )
    return result


def assert_result(result, values, subject, occurrence, index, *, calendar=False, first=False):
    verified = result._dataset.verified()
    primary = verified.primary
    if index == 4:
        primary = primary.set_column(
            primary.schema.get_field_index("value"), "value", primary["value"].cast(pa.int64())
        )
    rows = {tuple(row[k] for k in verified.contract.key_fields): row for row in primary.to_pylist()}
    oracle = expected(values, subject, occurrence, calendar=calendar, first=first)
    assert rows.keys() == {key for key, _ in oracle}
    for key, amounts in oracle:
        wanted = amounts[index]
        assert rows[key]["value"] == wanted
        assert rows[key]["cell_tag"] == ("undefined" if wanted is None else "defined")
        assert rows[key]["cell_reason"] == ("zero_denominator" if wanted is None else None)
    anchors = next(part.table for part in verified.parts if part.role == "anchor")
    assert anchors.num_rows == len(oracle)
    for row in anchors.to_pylist():
        assert row["anchor__started_at"] >= START
        assert row["anchor__deadline"] > row["anchor__started_at"]
        for name in anchors.column_names:
            if name.startswith("anchor__uses_"):
                assert all(
                    row["anchor__started_at"] <= use["event_time"] < row["anchor__deadline"]
                    for use in row[name]
                )
