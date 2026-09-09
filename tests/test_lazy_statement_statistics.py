"""Statement diagnostics preserve SQL without re-entering the compiler."""

from pathlib import Path

import pytest

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime


@pytest.mark.parametrize("kind", ["primary", "engine_check.primary_schema"])
def test_recording_preserves_raw_sql_without_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    events: list[str] = []
    runtime = DatasetRuntime.create(tmp_path, "statement-statistics", event=events.append)
    events.clear()
    sql = "SELECT 'literal-value', 42 /* original formatting */\n"

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Recording a diagnostic statement must not parse SQL")

    monkeypatch.setattr(admission.sqlglot, "parse_one", forbidden)
    runtime._record_statement(kind, sql)

    assert runtime.statistics.statements == [(kind, sql)]
    expected_checks = int(kind.startswith("engine_check."))
    assert runtime.statistics.validation_queries == expected_checks
    assert events == ["source_statement"] * expected_checks
