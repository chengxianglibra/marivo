"""Complete validation precedes the single registered numerical invocation."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.materialization import local_execution
from marivo.analysis.materialization.local_execution import (
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalStage,
    StreamInput,
)
from marivo.analysis.operators import forecast_values
from marivo.analysis.operators.errors import ForecastError
from marivo.analysis.operators.forecast_contracts import ForecastSpecV1, ForecastTrainingSummary
from marivo.analysis.operators.forecast_values import PreparedHistory
from tests.lazy_forecast_fixtures import forecast_spec, history_frame


@pytest.mark.parametrize("invalid", [False, True])
def test_model_invoked_once_only_after_every_series_validates(
    monkeypatch: pytest.MonkeyPatch, invalid: bool
) -> None:
    spec = forecast_spec(panel=True)
    frame = pd.concat(
        [
            history_frame([1.0, 2.0, 3.0], channel="a"),
            history_frame([4.0, 5.0, None if invalid else 6.0], channel="b"),
        ],
        ignore_index=True,
    )
    frame = frame.loc[:, [f.name for f in spec.input_row.schema.columns]]
    table = pa.Table.from_pandas(frame, preserve_index=False)
    calls = 0
    original = forecast_values.execute_forecast

    def execute(
        prepared: PreparedHistory, received: ForecastSpecV1
    ) -> tuple[pd.DataFrame, ForecastTrainingSummary]:
        nonlocal calls
        calls += 1
        assert len(prepared.groups) == 2
        return original(prepared, received)

    monkeypatch.setattr(forecast_values, "execute_forecast", execute)
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.input_row, spec.input_rows)),),
        (LocalStage(1, (0,), spec),),
        1,
    )
    parent = (LocalInputStreams(table.to_batches()),)
    if invalid:
        with pytest.raises(ForecastError):
            local_execution._execute_graph(parent, request)
        assert calls == 0
    else:
        output = local_execution._execute_graph(parent, request)
        assert calls == 1 and len(output.frames.frame) == 8
