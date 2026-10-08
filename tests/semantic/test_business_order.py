"""Native counterfactual for explicit same-instant Event ordering."""

import json
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.lifecycle.lifecycle_fixtures import END, build_lifecycle_public
from tests.analysis.lifecycle.lifecycle_oracle import expected_histories
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
def test_same_time_business_order_changes_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_trace: SourceTrace
) -> None:
    transition_counts: list[int] = []
    for finished_first in (False, True):
        root = tmp_path / ("finished-first" if finished_first else "paid-first")
        root.mkdir()
        monkeypatch.chdir(root)
        monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
        rows = [
            (0, "started", 0, 1),
            (0, "paid", 10, 3 if finished_first else 2),
            (0, "finished", 10, 2 if finished_first else 3),
        ]
        session, population, window, claims, facts = build_lifecycle_public(
            root, backend_name="sqlite", rows=rows
        )
        before = len(source_trace.native_sql)
        history = session.lifecycle.replay(
            ms.ref.state_model("commerce.model"),
            population=population,
            window=window,
            seed=mv.from_inception(),
            completeness=claims,
        ).execute()
        assert history._dataset is not None
        records = [
            json.loads(row["history__record"])
            for row in history._dataset.verified().parts[0].table.to_pylist()
        ]
        # Reuse the independent scalar oracle without changing its contract.
        assert records == expected_histories(facts, known=END)  # type: ignore[no-untyped-call]
        assert len(source_trace.native_sql) > before
        count = sum(len(record["transitions"]) for record in records)
        transition_counts.append(count)
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
    assert transition_counts == [2, 1]
