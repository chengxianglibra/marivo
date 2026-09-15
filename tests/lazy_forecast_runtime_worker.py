"""Independent producer, source-offline continuation and cold Forecast binding."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.evidence._dataset_codec import encode_finding_body
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.operators.forecast_contracts import drift, naive, periods, seasonal_naive
from marivo.analysis.operators.forecast_dataset import MaterializedForecastDataset
from tests.lazy_distinct_runtime_worker import rows
from tests.lazy_forecast_fixtures import history, setup_forecast
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold Forecast binding attempted a new execution")


def run(mode: str, model: str, kind: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    method = (
        naive() if model == "naive" else drift() if model == "drift" else seasonal_naive(periods=2)
    )
    if mode == "produce":
        runtime, source, database = setup_forecast(project)
        runtime.target = LocalTarget()
        metric = history(source).execute()
        runtime.target = LocalTarget()
        result = metric.forecast(horizon=periods(4), model=method).execute()
        database.rename(project / "origin.offline")
        refs = {
            "session": runtime.session_ref,
            "metric": metric.state.artifact_ref.ref,
            "forecast": result.state.artifact_ref.ref,
        }
        before = snapshot(runtime)
    else:
        runtime = DatasetRuntime.open(project, refs["session"], target=LocalTarget())
        before = snapshot(runtime)
        recovered = runtime.artifact(refs["forecast"])
        assert isinstance(recovered, MaterializedForecastDataset)
        result = recovered
    selected_rows: object = None
    if mode != "produce":
        recovered_metric = runtime.artifact(refs["metric"])
        assert isinstance(recovered_metric, MaterializedMetricDataset)
        metric = recovered_metric
        with ExitStack() as guards:
            if mode == "cold":
                for name in (
                    "place",
                    "compile_dataset",
                    "_build_backend_from_effective",
                    "execute_local",
                ):
                    guards.enter_context(patch.object(admission, name, forbidden))
            rebound = metric.forecast(horizon=periods(4), model=method).execute()
            assert rebound.state.artifact_ref == result.state.artifact_ref
            selected = result.rank(result.fields.get("forecast_value")).limit(2).execute()
        refs = {**refs, "selected": selected.state.artifact_ref.ref}
        selected_rows = rows(selected)
        if mode == "cold":
            assert snapshot(runtime) == before
    record = runtime.store.artifact(refs["forecast"])
    assert record is not None
    return {
        "pid": os.getpid(),
        "refs": refs,
        "rows": rows(result),
        "selected_rows": selected_rows,
        "artifact": record_evidence(record),
        "findings": [encode_finding_body(f) for f in result.findings().items],
        "before": before,
        "after": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
        "origin_removed": not (project / "warehouse.duckdb").exists(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("model", choices=("naive", "drift", "seasonal_naive"))
    parser.add_argument("kind", choices=("local", "engine"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.model, args.kind, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
