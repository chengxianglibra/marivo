"""Separate producer and source-offline temporal continuation processes."""

import json
import os
import sys
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import encode_descriptor
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic.ir import StrptimeParse, TimestampParse
from tests.lazy_materialization_crash_worker import record_evidence, snapshot
from tests.lazy_temporal_fixtures import AXIS, temporal_fixture


def setup(
    project: Path, *, invalid_parse: bool = False
) -> tuple[DatasetRuntime, LogicalMetricDataset]:
    with temporal_fixture(
        project,
        physical="VARCHAR" if invalid_parse else "TIMESTAMP",
        declared="string" if invalid_parse else "timestamp(6)",
        parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC")
        if invalid_parse
        else TimestampParse(timezone="UTC"),
        values=("invalid-time-canary",)
        if invalid_parse
        else ("2026-07-01 15:59:00", "2026-07-01 16:01:00"),
    ) as fixture:
        registry, sidecar = fixture.registry, fixture.sidecar
    store = SessionStore(project)
    session = store.create_session("temporal", report_timezone_name="Asia/Shanghai")
    runtime = DatasetRuntime(store, session.session_ref)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(
                start="2026-07-01",
                end="2026-07-03",
            ),
        )
        .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
        .aggregate()
    )
    return runtime, logical


def produce(project: Path) -> dict[str, object]:
    runtime, logical = setup(project)
    store = runtime.store
    materialized = logical.execute()
    assert isinstance(materialized, MaterializedMetricDataset)
    frame = materialized.to_pandas()
    assert frame["revenue"].tolist() == [1, 2]
    assert [str(value)[:10] for value in frame["order_time"]] == ["2026-07-01", "2026-07-02"]
    record = store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None and record.descriptor.temporal_execution
    return {
        "session": runtime.session_ref,
        "artifact": record.artifact_ref,
        "descriptor": json.loads(encode_descriptor(record.descriptor)),
        "statistics": asdict(runtime.statistics),
        "record": record_evidence(record),
        "store": snapshot(runtime),
        "pid": os.getpid(),
    }


def recover(project: Path, session: str, reference: str) -> dict[str, object]:
    attempts: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        attempts.append("source_access")
        raise AssertionError("cold temporal continuation accessed the source")

    with ExitStack() as stack:
        for name in (
            "_effective_kwargs",
            "_build_backend_from_effective",
            "compile_dataset",
            "probe_engine_timezone",
        ):
            stack.enter_context(patch.object(admission, name, forbidden))
        runtime = DatasetRuntime.open(project, session)
        materialized = runtime.artifact(reference)
        assert isinstance(materialized, MaterializedMetricDataset)
        record = runtime.store.artifact(reference)
        assert record is not None
        frame = materialized.to_pandas()
        assert frame["revenue"].tolist() == [1, 2]
        assert [str(value)[:10] for value in frame["order_time"]] == ["2026-07-01", "2026-07-02"]
        selected = materialized.where(gt(ref.metric("sales.revenue"), 1))
        ranked = (
            selected.rank(selected.fields.metric(ref.metric("sales.revenue"))).limit(1).execute()
        )
        assert ranked.to_pandas()["revenue"].tolist() == [2]
        folded = materialized.rollup(drop_time=True).execute()
        assert folded.to_pandas()["revenue"].tolist() == [3]
        monthly = materialized.rollup(grain=grain("month")).execute()
        assert monthly.to_pandas()["revenue"].tolist() == [3]
        for result in (ranked, folded, monthly):
            saved = runtime.store.artifact(result.state.artifact_ref.ref)
            assert saved is not None
            assert saved.descriptor.temporal_execution == record.descriptor.temporal_execution
        return {
            "descriptor": json.loads(encode_descriptor(record.descriptor)),
            "pid": os.getpid(),
            "source_attempts": attempts,
        }


if __name__ == "__main__":
    mode, project, *rest = sys.argv[1:]
    result = produce(Path(project)) if mode == "produce" else recover(Path(project), *rest)
    print(json.dumps(result, sort_keys=True))
