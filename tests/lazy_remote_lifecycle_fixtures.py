"""Independent expected history and cold continuation checks for source ports."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from marivo.analysis.domains.lifecycle import MaterializedLifecycleDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from tests.lazy_lifecycle_fixtures import END, START


def assert_complete_history(runtime: DatasetRuntime, result: MaterializedLifecycleDataset) -> None:
    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(1,), (1,), (2,)]
    assert frame.model_state.tolist() == ["open", "done", "open"]
    assert frame.valid_from.tolist() == [
        START,
        START + timedelta(hours=3),
        START + timedelta(hours=2),
    ]
    assert frame.valid_to.tolist() == [START + timedelta(hours=3), END, END]
    assert frame.left_clipped.tolist() == [True, False, False]
    assert frame.interval_status.tolist() == ["completed", "right_censored", "right_censored"]
    assert frame.exited_by_event_ref.isna().tolist() == [False, True, True]
    assert frame.exited_by_event_identity.isna().tolist() == [False, True, True]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    evidence = record.descriptor.lifecycle_evidence
    assert isinstance(evidence, LifecycleEvidenceSummary)
    assert (
        evidence.row_count,
        evidence.subject_count,
        evidence.seeded_count,
        evidence.not_incepted_count,
        evidence.coverage_censored_count,
        evidence.transition_count,
        evidence.violation_count,
        evidence.left_clipped_count,
    ) == (3, 3, 2, 1, 0, 1, 1, 1)
    assert len(record.descriptor.retained_parts) == 3


def assert_cold_history(project: Path, session_ref: str, artifact_ref: str) -> None:
    """Run after disposable source tables have been removed."""
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; import sys; "
            "from datetime import datetime,timedelta,timezone; "
            "from marivo.analysis.materialization.admission import DatasetRuntime; "
            "runtime=DatasetRuntime.open(Path(sys.argv[1]),sys.argv[2]); "
            "history=runtime.artifact(sys.argv[3]); "
            "assert history.to_pandas().model_state.tolist()==['open','done','open']; "
            "assert not runtime.statistics.submissions; "
            "from marivo.analysis.domains.lifecycle_reducers import in_state; "
            "from marivo.semantic.state_model import ModelStateHandle; "
            "from marivo.refs import ref; "
            "end=datetime(2026,2,2,tzinfo=timezone.utc); "
            "assert history.distribution(at=(end,)).execute().to_pandas().subject_count.sum()==2; "
            "assert history.select_subjects(in_state(ModelStateHandle(ref.state_model('sales.purchase'),'done'),at=end)).execute().to_pandas().entity_identity.tolist()==[(1,)]; "
            "assert history.transitions().execute().to_pandas().transition_count.tolist()==[1]; "
            "assert history.violations().execute().to_pandas().violation_kind.tolist()==['transition_from_terminal']; "
            "dwell=history.dwell().execute().to_pandas().set_index('model_state'); "
            "assert dwell.loc['open','mean_duration']==timedelta(hours=3); "
            "assert not any(item.domain=='source' for item in runtime.statistics.submissions)",
            str(project),
            session_ref,
            artifact_ref,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
