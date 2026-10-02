"""Raw-event History projections and exact fraction statistics, independent of Runtime."""

from datetime import datetime, timedelta
from fractions import Fraction

from tests.lifecycle_r75_fixtures import END, START
from tests.lifecycle_r75_oracle import expected_histories

STATES = ("open", "paid", "done")
PAIRS = (("open", "open"), ("open", "paid"), ("open", "done"), ("paid", "done"))


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def checkpoint(history, at):
    known = history["known_through"]
    if known is None:
        return None
    evaluations = [
        e
        for e in history["evaluations"]
        if instant(e["occurrence"]["occurred_at"]) <= at
        and instant(e["occurrence"]["occurred_at"]) < END
    ]
    state = next((e["after"] for e in reversed(evaluations) if e["after"] is not None), None)
    if at >= instant(known) and at != END and state != "done":
        return None
    if at == END and instant(known) < END and state != "done":
        return None
    return state or "not_started"


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    index = position.numerator // position.denominator
    value = values[index]
    if index + 1 < len(values):
        value += (values[index + 1] - value) * (position - index)
    return timedelta(microseconds=round(value))


def views(values, subject="i", occurrence="i", known=END, empty=False):
    histories = [] if empty else expected_histories(values, subject, occurrence, known)
    transitions = [
        (t["from_state"], t["to_state"])
        for h in histories
        for t in h["transitions"]
        if START <= instant(t["occurrence"]["occurred_at"]) < END
    ]
    intervals = [i for h in histories for i in h["intervals"]]
    output = {}
    output["in_state"] = [
        (
            tuple(h["subject"]),
            None if checkpoint(h, END) is None else checkpoint(h, END) == "done",
            "unknown" if checkpoint(h, END) is None else "defined",
        )
        for h in histories
    ]
    output["distribution"] = []
    for at in (START, END):
        statuses = [checkpoint(h, at) for h in histories]
        seeded = sum(v in STATES for v in statuses)
        for state in STATES:
            count = statuses.count(state)
            output["distribution"].append(
                (
                    (at, state),
                    count,
                    seeded,
                    statuses.count(None),
                    float(Fraction(count, seeded)) if seeded else None,
                )
            )
    output["transitions"] = [
        (
            pair,
            transitions.count(pair),
            float(Fraction(transitions.count(pair), len(transitions))) if transitions else None,
        )
        for pair in PAIRS
    ]
    output["intervals"] = [
        (
            (*h["subject"], i["ordinal"]),
            i["state"],
            instant(i["start"]),
            instant(i["end"]),
            None if i["observed_ticks"] is None else timedelta(microseconds=i["observed_ticks"]),
            i["left_clipped"],
            i["status"],
        )
        for h in histories
        for i in h["intervals"]
    ]
    output["violations"] = [
        (
            (e["occurrence"]["event"], *e["occurrence"]["key"]),
            e["occurrence"]["event"],
            instant(e["occurrence"]["occurred_at"]),
            e["before"],
            e["disposition"],
        )
        for h in histories
        for e in h["evaluations"]
        if e["disposition"] in ("illegal_transition", "transition_from_terminal")
        and START <= instant(e["occurrence"]["occurred_at"]) < END
    ]
    output["dwell"] = []
    for state in STATES:
        selected = [i for i in intervals if i["state"] == state]
        complete = [i for i in selected if i["status"] == "completed"]
        ticks = [i["observed_ticks"] for i in complete]
        output["dwell"].append(
            (
                (state,),
                len(selected),
                len(complete),
                sum(i["status"] == "right_censored" for i in selected),
                sum(i["status"] == "coverage_censored" for i in selected),
                sum(i["left_clipped"] for i in complete),
                timedelta(microseconds=round(Fraction(sum(ticks), len(ticks)))) if ticks else None,
                quantile(ticks, Fraction(1, 2)),
                quantile(ticks, Fraction(9, 10)),
            )
        )
    return output


def assert_view(result, name, expected):
    verified = result._dataset.verified()
    rows = verified.primary.to_pylist()
    keys = verified.contract.key_fields
    fields = {
        "in_state": ("value", "cell_tag"),
        "distribution": (
            "known_state_count",
            "seeded_subject_count",
            "coverage_censored_count",
            "share_among_seeded",
        ),
        "transitions": ("count", "share_of_modeled_transitions"),
        "intervals": ("state", "start", "end", "observed_duration", "left_clipped", "status"),
        "violations": ("trigger", "occurred_at", "state_at_event", "kind"),
        "dwell": (
            "interval_count",
            "completed_count",
            "right_censored_count",
            "coverage_censored_count",
            "left_clipped_completed_count",
            "mean_duration",
            "median_duration",
            "p90_duration",
        ),
    }[name]
    actual = [(tuple(row[k] for k in keys), *(row[field] for field in fields)) for row in rows]
    assert sorted(actual, key=repr) == sorted(expected[name], key=repr)
    assert len(result.to_pandas()) == len(actual)
