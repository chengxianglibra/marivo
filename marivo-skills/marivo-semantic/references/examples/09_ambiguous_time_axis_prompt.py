"""Ambiguous time axes require a user question before authoring."""

from __future__ import annotations

candidate_time_fields = ("created_at", "paid_at", "cancelled_at")

if len(candidate_time_fields) > 1:
    print("ask_user: Which business time axis should define the metric window?")
    print("choices: created_at, paid_at, cancelled_at")
else:
    print(f"chosen_time_axis: {candidate_time_fields[0]}")
