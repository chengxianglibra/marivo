"""Public History consumer operations shared by qualification and A10 processes."""

from datetime import timedelta

import marivo.analysis as mv
from tests.analysis.lifecycle.history_fixtures import observe, selected


def transport(history):
    intervals = history.intervals()
    binding = intervals.subjects()
    completed = intervals.where(intervals.status.value.eq("completed"))
    empty = intervals.where(intervals.status.value.eq("absent"))
    return {
        "completed_members": completed.members(through=binding),
        "empty_members": empty.members(through=binding),
        "state_members": selected(history, "state"),
        "violation_members": selected(history, "violation"),
        "state_field": completed.state,
    }


def mean(history):
    intervals = history.intervals()
    return intervals.where(intervals.status.value.eq("completed")).observed_duration.aggregate(
        mv.mean()
    )


def expected(name, result):
    frame = result.to_pandas()
    if name == "empty_members":
        assert frame.empty
    elif name.endswith("members"):
        assert len(frame) == 1
    elif name == "state_field":
        assert frame["value"].tolist() == ["open", "open"]
    elif name == "mean":
        assert frame["value"].tolist() == [timedelta(seconds=5)]
        part = next(p.table for p in result._dataset.verified().parts if p.role == "row_state")
        assert part["row_state__sum"].to_pylist() == [10_000_000]
        assert part["row_state__count"].to_pylist() == [2]
    elif name.endswith("revenue"):
        assert frame["value"].tolist() == [33.0]
    elif name.endswith("fact_count"):
        assert frame["value"].tolist() == [6]
    else:
        raise AssertionError(name)


def observations(history):
    return {
        kind + "_" + metric: observe(history, kind, metric)
        for kind in ("state", "interval", "violation")
        for metric in ("revenue", "fact_count")
    }
