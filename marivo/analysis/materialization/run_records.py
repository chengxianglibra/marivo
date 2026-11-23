"""Decode a Run and its terminal record within one Store transaction."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from marivo.analysis.materialization.contracts import RunFailure, decode_failure, parse_timestamp
from marivo.analysis.materialization.sqlite_values import _one, _optional, _text
from marivo.analysis.materialization.terminal_protocol import InvocationInput, decode
from marivo.analysis.model.execution_errors import terminal_output_integrity_error as invalid


@dataclass(frozen=True, slots=True)
class TerminalRun:
    run_ref: str
    session_ref: str
    invocation_id: str
    dataset_input: InvocationInput
    lifecycle: Literal["incomplete", "succeeded", "failed"]
    admitted_at: str
    terminal_at: str | None
    output_artifact_ref: str | None
    failure: RunFailure | None


def load_run(conn: sqlite3.Connection, ref: str) -> TerminalRun | None:
    row = _one(conn, "SELECT * FROM analysis_action_runs WHERE run_ref=?", (ref,))
    if row is None:
        return None
    selected = decode(_text(row, "dataset_input_payload"), InvocationInput)
    session = _text(row, "session_ref")
    terminal = _one(conn, "SELECT * FROM analysis_action_run_terminals WHERE run_ref=?", (ref,))
    lifecycle: Literal["incomplete", "succeeded", "failed"] = "incomplete"
    output = None
    failure = None
    time = None
    if terminal is not None:
        time = _text(terminal, "terminal_at")
        if _text(terminal, "session_ref") != session or parse_timestamp(time) < parse_timestamp(
            _text(row, "admitted_at")
        ):
            raise invalid("terminal Run ownership or time differs")
        output = _optional(terminal, "output_artifact_ref")
        text = _optional(terminal, "failure_payload")
        status = _text(terminal, "outcome")
        if status == "succeeded" and output is not None and text is None:
            lifecycle = "succeeded"
        elif status == "failed" and output is None and text is not None:
            lifecycle, failure = "failed", decode_failure(text)
        else:
            raise invalid("contradictory terminal Run")
    return TerminalRun(
        ref,
        session,
        _text(row, "invocation_id"),
        selected,
        lifecycle,
        _text(row, "admitted_at"),
        time,
        output,
        failure,
    )
