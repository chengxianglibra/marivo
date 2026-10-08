"""Independent scalar oracle; no graph, registry or replay implementation imports."""

from datetime import timedelta

from tests.analysis.lifecycle.lifecycle_fixtures import END, START, keys


def expected_histories(values, subject="i", occurrence="i", known=END, cycle=False):
    members = keys(subject, "sid", 3)
    identities = keys(occurrence, "oid", len(values))
    output = []
    for member in range(3):
        current = None
        inception = None
        trace, transitions, entries = [], [], []
        events = sorted(
            (
                (index, value)
                for index, value in enumerate(values)
                if value[0] == member and value[2] < 100
            ),
            key=lambda item: (item[1][2], item[1][3]),
        )
        for index, (_, event, offset, sequence) in events:
            fact = {
                "event": "commerce." + event,
                "key": [column[index] for column in identities.values()],
                "occurred_at": (START + timedelta(seconds=offset))
                .isoformat()
                .replace("+00:00", "Z"),
                "sequence": sequence,
            }
            cutoff = None if known is None else (known - START).total_seconds()
            uncertain = cutoff is None or (
                offset >= cutoff and not (current == "done" and not cycle)
            )
            if uncertain:
                current = None
            before = current
            if uncertain:
                disposition = "unknown_origin" if known is None else "unknown_followup"
            elif current is None:
                disposition = "inception" if event == "started" else "pre_inception"
                if event == "started":
                    current = "open"
                    inception = fact
                    entries.append((current, fact, offset))
            elif current == "done" and not cycle:
                disposition = "transition_from_terminal"
            elif (current, event) in {
                ("open", "paid"),
                ("open", "pulse"),
                ("open", "finished"),
                ("paid", "finished"),
                *((("done", "finished"),) if cycle else ()),
            }:
                disposition = "legal_transition"
                current = (
                    "open"
                    if cycle and current == "done"
                    else {"paid": "paid", "pulse": "open", "finished": "done"}[event]
                )
                transitions.append(
                    {
                        "ordinal": len(transitions) + 1,
                        "occurrence": fact,
                        "from_state": before,
                        "to_state": current,
                    }
                )
                entries.append((current, fact, offset))
            else:
                disposition = "illegal_transition"
            trace.append(
                {
                    "occurrence": fact,
                    "before": before,
                    "after": current,
                    "disposition": disposition,
                    "terminal_set": False,
                }
            )
        intervals = []
        for index, (state, entered, offset) in enumerate(entries):
            exited = entries[index + 1] if index + 1 < len(entries) else None
            left, right = max(0, offset), min(100, exited[2] if exited else 100)
            if left >= right:
                continue
            boundary = None if known is None else (known - START).total_seconds()
            proved = (
                boundary is not None
                and offset < boundary
                and right <= boundary
                and (exited is None or exited[2] < boundary)
            )
            intervals.append(
                {
                    "ordinal": index + 1,
                    "state": state,
                    "entered": entered,
                    "exited": None if exited is None else exited[1],
                    "start": (START + timedelta(seconds=left)).isoformat().replace("+00:00", "Z"),
                    "end": (START + timedelta(seconds=right)).isoformat().replace("+00:00", "Z"),
                    "status": ("completed" if exited else "right_censored")
                    if proved
                    else "coverage_censored",
                    "left_clipped": offset < 0,
                    "observed_ticks": None
                    if boundary is None or left >= boundary
                    else int((min(right, boundary) - left) * 1000000),
                }
            )
        trusted = (
            inception
            if inception is not None
            and known is not None
            and next(value[2] for _, value in events if value[1] == "started")
            < (known - START).total_seconds()
            else None
        )
        output.append(
            {
                "subject": [column[member] for column in members.values()],
                "classification": "coverage_censored"
                if known != END
                else "seeded"
                if trusted
                else "not_started",
                "inception": trusted,
                "known_through": None
                if known is None
                else known.isoformat().replace("+00:00", "Z"),
                "evaluations": trace,
                "transitions": transitions,
                "violations": [
                    entry
                    for entry in trace
                    if entry["disposition"] in ("illegal_transition", "transition_from_terminal")
                ],
                "pre_inception": [
                    entry["occurrence"]
                    for entry in trace
                    if entry["disposition"] == "pre_inception"
                ],
                "intervals": intervals,
            }
        )
    return output
