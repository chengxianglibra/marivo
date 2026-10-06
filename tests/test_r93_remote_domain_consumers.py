"""Native independent-read C11/C12/C13/C18 consumers with retained oracles."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from tests.json_support import Json
from tests.lifecycle_r75_fixtures import END, START, build_lifecycle_public
from tests.lifecycle_r75_oracle import expected_histories
from tests.r9_source_cases import SourceData, source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_domain_recovery_worker import snapshot


def _data(table: list[dict[str, object]], backend: str, *, events: bool) -> SourceData:
    def literal(value: object) -> str:
        if isinstance(value, datetime):
            rendered = value.strftime("%Y-%m-%d %H:%M:%S.%f")
            return f"TIMESTAMP '{rendered} UTC'" if backend == "trino" else f"'{rendered}'"
        return repr(value) if isinstance(value, str) else str(value)

    temporal = (
        "TIMESTAMP(6) WITH TIME ZONE"
        if backend == "trino"
        else "TIMESTAMPTZ"
        if backend == "postgres"
        else "TIMESTAMP(6)"
    )
    return SourceData(
        f"oid BIGINT, sid BIGINT, kind VARCHAR(20), seq BIGINT, instant {temporal}"
        if events
        else "sid BIGINT",
        ",".join("(" + ",".join(literal(value) for value in row.values()) + ")" for row in table),
        "oid Int64, sid Int64, kind String, seq Int64, instant DateTime64(6, 'UTC')"
        if events
        else "sid Int64",
        table,
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_native_remote_domain_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, r93_source_trace: SourceTrace
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    rows = [
        (0, "started", 0, 1),
        (0, "finished", 5, 2),
        (1, "started", 2, 1),
        (1, "finished", 7, 2),
    ]
    _, _, window, claims, _ = build_lifecycle_public(tmp_path, rows=rows)
    local = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        subjects = local.table("subjects").to_pyarrow().to_pylist()
        facts = local.table("facts").to_pyarrow().to_pylist()
    finally:
        local.disconnect()
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with (
        source_case(
            backend, profile, tmp_path, monkeypatch, _data(subjects, backend, events=False)
        ) as people,
        source_case(
            backend, profile, tmp_path, monkeypatch, _data(facts, backend, events=True)
        ) as events,
    ):
        assert isinstance(people.source, TableSourceIR) and isinstance(events.source, TableSourceIR)
        arguments = {
            **people.session.datasource.fields,
            **{key + "_env": value for key, value in people.session.datasource.env_refs.items()},
        }
        user = arguments.pop("user", None)
        if user is not None:
            monkeypatch.setenv("MARIVO_R93_DOMAIN_READER", str(user))
            arguments["user_env"] = "MARIVO_R93_DOMAIN_READER"
        (tmp_path / "models/datasources/warehouse.py").write_text(
            "import marivo.datasource as md\n"
            + f'md.{backend}(name="warehouse",'
            + ",".join(f"{key}={value!r}" for key, value in arguments.items())
            + ")\n"
        )
        model = tmp_path / "models/semantic/commerce/objects.py"
        code = model.read_text()
        for logical, case in (("subjects", people), ("facts", events)):
            assert isinstance(case.source, TableSourceIR)
            code = code.replace(
                f"md.table({logical!r})",
                f"md.table({case.source.table!r},database={case.source.database!r})",
            )
        code += "\nscore = ms.measure_column(name='score',entity=facts,column='seq',additivity=ms.additive_all(),unit='1')\nscore_sum = ms.aggregate(name='score_sum',measure=score,agg='sum',time=instant,nulls=ms.nulls.ignore(),empty=ms.empty.zero())\n"
        model.write_text(code)
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("r93-remote-" + backend, report_timezone="UTC")
        population = session.members(ms.ref.entity("commerce.subjects"))
        pattern = mv.EventPattern(
            steps=(
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.started"), name="subject"
                    ),
                    key="start",
                ),
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.finished"), name="subject"
                    ),
                    key="finish",
                ),
            )
        )
        journey = session.events.match(
            pattern,
            population=population,
            cohort_window=window,
            completion_through=END,
            matching=mv.every_start(completion_assignment="exclusive"),
            business_order=ms.ref.business_order("commerce.order"),
            completeness=tuple(
                replace(
                    claim,
                    inputs=tuple(
                        event
                        for event in claim.inputs
                        if event.path in ("commerce.started", "commerce.finished")
                    ),
                )
                for claim in claims
            ),
        )
        fixed = journey.execute()
        assert (
            fixed.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
            .execute()
            .observed_duration.execute()
            .to_pandas()
            .value.tolist()
            == [timedelta(seconds=5)] * 2
        )
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
        # This independently maintained legacy oracle has no type annotations.
        assert records == expected_histories(rows, known=END)  # type: ignore[no-untyped-call]
        logical_anchors = session.anchors(journey, population=population, during=window)
        observation = logical_anchors.observe(
            ms.ref.metric("commerce.score_sum"),
            within=mv.elapsed(mv.duration(seconds=10)),
            via=ms.ref.relationship("commerce.participant"),
        ).execute()
        # The original anchor occurrence is excluded; each later finish contributes seq=2.
        assert observation.to_pandas().value.tolist() == [2, 2]
        event_anchors = session.anchors(
            ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
            population=population,
            during=window,
            business_order=ms.ref.business_order("commerce.order"),
        )
        returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
        retained = event_anchors.retention(
            returning,
            within=mv.elapsed(mv.duration(seconds=10)),
            completeness=tuple(replace(claim, inputs=(returning.event,)) for claim in claims),
        ).execute()
        assert retained.to_pandas().value.tolist() == [True, True]
        anchors = logical_anchors.execute()
        assert anchors._dataset is not None
        parts = anchors._dataset.verified().parts
        anchor = next(part.table for part in parts if part.role == "anchor")
        assert anchor.column("anchor__started_at").to_pylist() == [
            START,
            START + timedelta(seconds=2),
        ]
        assert all(anchor.column("anchor__assignment").to_pylist())
        for artifact in (fixed, history, anchors):
            assert artifact._dataset is not None
            assert artifact._dataset.verified().primary is not None

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("Fixed consumer read its source")

        monkeypatch.setattr(SourceSession, "batches", forbidden)
        assert (
            fixed.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
            .execute()
            .observed_duration.execute()
            .to_pandas()
            .value.tolist()
            == [timedelta(seconds=5)] * 2
        )
        assert history.transitions().count.execute().to_pandas().value.sum() == 2
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        if os.environ.get("MARIVO_R94_DOMAIN_RECOVERY") == "1":
            (tmp_path / "r94-domain.json").write_text(
                json.dumps(
                    {
                        "session": session.id,
                        "sources": {
                            "journey": snapshot(fixed),
                            "history": snapshot(history),
                            "anchors": snapshot(anchors),
                            "retention": snapshot(retained),
                            "observation": snapshot(observation),
                        },
                    },
                    sort_keys=True,
                )
            )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"remote-domain-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "families": ["C11", "C12", "C13", "C18"],
                        "history_oracle": records,
                        "duration_seconds": [5, 5],
                        "anchors": 2,
                        "fixed_source_reads": 0,
                        "resources": 0,
                        "native_sql": r93_source_trace.native_sql,
                    },
                    sort_keys=True,
                )
            )
    if os.environ.get("MARIVO_R94_DOMAIN_RECOVERY") == "1":
        reports: list[Json] = []
        for phase in ("fixed", "cold"):
            report = tmp_path / f"r94-domain-{phase}.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.r94_domain_recovery_worker",
                    str(tmp_path),
                    phase,
                    str(report),
                ],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
            reports.append(json.loads(report.read_text()))
        assert len({row["pid"] for row in reports if isinstance(row, dict)} | {os.getpid()}) == 3
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"r94-native-domain-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "profile": profile,
                        "producer_pid": os.getpid(),
                        "manifest": json.loads((tmp_path / "r94-domain.json").read_text()),
                        "reports": reports,
                    },
                    sort_keys=True,
                )
            )
