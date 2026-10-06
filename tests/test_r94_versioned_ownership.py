"""Versioned read families reject foreign Session predicates before I/O or Run."""

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, encode
from tests.r9_source_cases import SourceData, source_case
from tests.r94_domain_recovery_worker import snapshot
from tests.r94_native_domain_k_worker import run_ids
from tests.test_r94_public_refusals import publication_counts


@pytest.mark.runtime
@pytest.mark.parametrize("version", ("snapshot", "validity"))
def test_versioned_read_foreign_predicates_refuse_before_read_or_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    version: Literal["snapshot", "validity"],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, tenant VARCHAR, beginning DATE, ending DATE, amount BIGINT, enabled BOOLEAN",
        "(9007199254740992,'a','2026-08-01',NULL,10,FALSE),"
        "(9007199254740993,'b','2026-08-01',NULL,20,TRUE)",
        "id Int64, tenant String, beginning Date, ending Nullable(Date), amount Int64, enabled Bool",
        [
            {
                "id": identity,
                "tenant": tenant,
                "beginning": date(2026, 8, 1),
                "ending": None,
                "amount": amount,
                "enabled": enabled,
            }
            for identity, tenant, amount, enabled in (
                (9007199254740992, "a", 10, False),
                (9007199254740993, "b", 20, True),
            )
        ],
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        versioning = (
            "ms.snapshot(partition_field=ms.ref.time_dimension('sales.subjects.beginning'),grain='day',timezone='UTC')"
            if version == "snapshot"
            else "ms.validity(valid_from=ms.ref.time_dimension('sales.subjects.beginning'),valid_to=ms.ref.time_dimension('sales.subjects.ending'),interval='closed_open',open_end=(None,),timezone='UTC')"
        )
        fields = case.session.datasource.fields
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + "md.duckdb(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in fields.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id','tenant'],versioning={versioning})\n"
                + "tenant=ms.dimension_column(name='tenant',entity=subjects,column='tenant')\n"
                + "enabled=ms.dimension_column(name='enabled',entity=subjects,column='enabled')\n"
                + "beginning=ms.time_dimension_column(name='beginning',entity=subjects,column='beginning',granularity='day')\n"
                + "ending=ms.time_dimension_column(name='ending',entity=subjects,column='ending',granularity='day')\n"
                + "amount=ms.measure_column(name='amount',entity=subjects,column='amount',additivity=ms.additive_all())\n",
            }
        )
        sessions = [
            mv.session.get_or_create("r94-owner-left", report_timezone="UTC"),
            mv.session.get_or_create("r94-owner-right", report_timezone="UTC"),
        ]
        point = datetime(2026, 8, 1, tzinfo=timezone.utc)
        logical = [
            (
                owner.members(ms.ref.entity("sales.subjects"), at=point).read(
                    ms.ref.measure("sales.subjects.amount"), at=point
                ),
                owner.members(ms.ref.entity("sales.subjects"), at=point).read(
                    ms.ref.dimension("sales.subjects.tenant"), at=point
                ),
                owner.members(ms.ref.entity("sales.subjects"), at=point).read(
                    ms.ref.dimension("sales.subjects.enabled"), at=point
                ),
                owner.members(ms.ref.entity("sales.subjects"), at=point).read(
                    ms.ref.time_dimension("sales.subjects.beginning"), at=point
                ),
            )
            for owner in sessions
        ]
        fixed = [tuple(value.execute() for value in values) for values in logical]
        saved = [[snapshot(value) for value in values] for values in fixed]
        before = [run_ids(owner) for owner in sessions]
        published_before = [publication_counts(owner) for owner in sessions]
        calls: list[str] = []

        def tripwire(name: str) -> Callable[..., None]:
            def forbidden(*args: object, **kwargs: object) -> None:
                calls.append(name)
                raise AssertionError("Foreign predicate attempted " + name)

            return forbidden

        for name in ("__enter__", "bind", "compile", "batches"):
            monkeypatch.setattr(SourceSession, name, tripwire("source." + name))
        for owner in sessions:
            monkeypatch.setattr(owner._runtime.store, "admit", tripwire("store.admit"))
        errors: list[Json] = []
        for mode, pairs in (("logical", logical), ("fixed", fixed)):
            for left, right in ((pairs[0], pairs[1]), (pairs[1], pairs[0])):
                for family, value, foreign in zip(
                    ("numeric", "category", "boolean", "temporal"), left, right, strict=True
                ):
                    with pytest.raises(AnalysisError) as caught:
                        value.where(foreign.value.is_defined()).execute()
                    error = caught.value
                    assert error.expected and error.received and error.repair is not None
                    assert error.repair.action
                    errors.append(
                        {
                            "mode": mode,
                            "family": family,
                            "type": type(error).__name__,
                            "expected": error.expected,
                            "received": error.received,
                            "repair": error.repair.action,
                        }
                    )
        assert len(errors) == 16 and calls == []
        assert [run_ids(owner) for owner in sessions] == before
        assert [publication_counts(owner) for owner in sessions] == published_before
        assert [[snapshot(value) for value in values] for values in fixed] == saved
        assert all(owner._runtime.store.resources(owner.id) == () for owner in sessions)
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "versioned-ownership-" + version + ".json").write_bytes(
                encode(
                    {
                        "version": version,
                        "backend": "duckdb",
                        "errors": errors,
                        "tripwire_calls": list(calls),
                        "run_counts_before": [len(items) for items in before],
                        "run_counts_after": [len(run_ids(owner)) for owner in sessions],
                        "publication_counts_before": checked(published_before),
                        "publication_counts_after": checked(
                            [publication_counts(owner) for owner in sessions]
                        ),
                        "originals": checked(saved),
                        "previous_artifacts_preserved": True,
                        "resources": 0,
                        "boundary": "Shared versioned predicate ownership boundary before source/admission; no backend execution qualification is inferred.",
                    }
                )
            )
