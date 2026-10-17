"""Complete identity observation keeps members outside its contribution window."""

import pandas as pd
import pytest

import marivo.semantic as ms
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCaseFactory


def test_public_identity_observation_executes_and_retains_outside_window_members(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    reads: list[str] = []
    original = SourceSession.execute

    def traced(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
        if read.purpose.startswith("query."):
            reads.append(read.sql)
        return original(self, read)

    monkeypatch.setattr(SourceSession, "execute", traced)
    amount = session.catalog.fields.get(
        f"{case.names.domain}.{case.names.order}.{case.names.amount}"
    )
    time = session.catalog.fields.get(
        f"{case.names.domain}.{case.names.order}.{case.names.ordered_at}"
    )
    members = session.members(ms.ref.entity(f"{case.names.domain}.{case.names.order}"))
    observed = session.observe(
        {"value": amount.sum(time=time, empty="null")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
    )
    result = observed.execute()
    frame = result.to_pandas()
    actual = frame.set_index(frame.columns[0])[("value", "value")]
    assert {key: None if pd.isna(value) else value for key, value in actual.items()} == {
        "j1_a": 450,
        "j1_b": 150,
        "j1_c": 400,
        "j1_july": None,
        "j1_september": None,
    }
    assert reads
    assert not {"subject", "original_state", "coverage"}.intersection(
        part.name for part in result._manifest.data.attachments
    )
    total = observed.reduce({"value": observed["value"].sum()}).execute()
    assert total.to_pandas()[("value", "value")].tolist() == [1000]
    assert session.artifact(result.id).to_pandas().equals(frame)
    assert session._runtime.store.resources(session.id) == ()
