"""Pure dense transition reduction over committed Lifecycle history rows."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import pandas as pd

from marivo.analysis.frames.lifecycle import LifecycleTriggerBinding


@dataclass(frozen=True)
class LifecycleTransitionReduction:
    """Dense modeled transition rows and their reconciled total."""

    rows: pd.DataFrame
    modeled_pairs: tuple[tuple[str, str], ...]
    modeled_transition_count: int


def reduce_lifecycle_transitions(
    history: pd.DataFrame,
    *,
    triggers: tuple[LifecycleTriggerBinding, ...],
) -> LifecycleTransitionReduction:
    """Count completed adjacent state changes over distinct modeled pairs."""

    modeled_pairs = tuple(
        dict.fromkeys(
            (trigger.from_state, trigger.to_state)
            for trigger in triggers
            if trigger.kind == "transition" and trigger.from_state is not None
        )
    )
    counts = dict.fromkeys(modeled_pairs, 0)
    for _subject, subject_rows in history.groupby("subject_identity", sort=False):
        ordered = subject_rows.sort_values("valid_from", kind="stable")
        records = ordered.to_dict("records")
        for current, following in pairwise(records):
            if current["interval_status"] != "completed" or pd.Timestamp(
                current["valid_to"]
            ) != pd.Timestamp(following["valid_from"]):
                continue
            pair = (str(current["model_state"]), str(following["model_state"]))
            if pair in counts:
                counts[pair] += 1
    denominator = sum(counts.values())
    rows = pd.DataFrame(
        [
            {
                "from_model_state": source,
                "to_model_state": target,
                "transition_status": "modeled",
                "transition_count": counts[(source, target)],
                "share_of_modeled_transitions": (
                    counts[(source, target)] / denominator if denominator else None
                ),
            }
            for source, target in modeled_pairs
        ],
        columns=(
            "from_model_state",
            "to_model_state",
            "transition_status",
            "transition_count",
            "share_of_modeled_transitions",
        ),
    )
    return LifecycleTransitionReduction(
        rows=rows,
        modeled_pairs=modeled_pairs,
        modeled_transition_count=denominator,
    )


__all__ = []
