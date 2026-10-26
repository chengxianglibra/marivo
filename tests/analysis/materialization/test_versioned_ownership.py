"""Versioned read families reject foreign Session predicates before I/O or Run."""

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_store
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value, run_ids
from tests.support.json import Json, checked, encode


@pytest.mark.parametrize("version", ("snapshot", "validity"))
def test_versioned_read_foreign_predicates_refuse_before_read_or_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str], bool], SemanticProject],
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
            "ms.snapshot(partition_field=ms.ref.time_dimension('sales.subjects.beginning'),timezone='UTC')"
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
                + "beginning=ms.time_dimension_column(name='beginning',entity=subjects,column='beginning')\n"
                + "ending=ms.time_dimension_column(name='ending',entity=subjects,column='ending')\n"
                + "amount=ms.measure_column(name='amount',entity=subjects,column='amount',additivity=ms.additive_all())\n",
            },
            False,  # Each real Session owns loading the semantic project.
        )
        sessions = [
            mv.session.get_or_create("r94-owner-left", timezone="UTC"),
            mv.session.get_or_create("r94-owner-right", timezone="UTC"),
        ]
        point = datetime(2026, 8, 1, tzinfo=timezone.utc)
        families = (
            ("numeric", "amount"),
            ("category", "tenant"),
            ("boolean", "enabled"),
            ("temporal", "beginning"),
        )
        logical = []
        for owner in sessions:
            at = owner.instant(point)
            members = owner.members(owner.catalog.get("entity:sales.subjects").value, at=at)
            logical.append(
                owner.read(
                    members,
                    {
                        name: catalog_value(
                            owner.catalog, "sales.subjects." + name, kind="field"
                        ).at(at)
                        for _, name in families
                    },
                )
            )
        fixed = [value.persist() for value in logical]
        frames = [value.to_pandas() for value in fixed]
        for frame in frames:
            assert frame.iloc[:, 0].tolist() == [
                (9007199254740992, "a"),
                (9007199254740993, "b"),
            ]
            assert frame["amount"].tolist() == [10, 20]
            assert frame["tenant"].tolist() == ["a", "b"]
            assert frame["enabled"].tolist() == [False, True]
            assert frame["beginning"].tolist() == [date(2026, 8, 1)] * 2
        saved = [snapshot(value, frame=frame) for value, frame in zip(fixed, frames, strict=True)]
        before = [run_ids(owner) for owner in sessions]
        published_before = [publication_counts(owner) for owner in sessions]
        calls: list[str] = []

        def tripwire(name: str) -> Callable[..., None]:
            def forbidden(*args: object, **kwargs: object) -> None:
                calls.append(name)
                raise AssertionError("Foreign predicate attempted " + name)

            return forbidden

        for name in ("__enter__", "bind", "compile", "execute"):
            monkeypatch.setattr(SourceSession, name, tripwire("source." + name))
        monkeypatch.setattr(terminal_store, "admit", tripwire("terminal_store.admit"))
        errors: list[Json] = []
        for left, right in ((logical[0], logical[1]), (logical[1], logical[0])):
            for family, name in families:
                with pytest.raises(AnalysisError) as caught:
                    left.where(~right[name].is_null())
                error = caught.value
                assert error.expected and error.received and error.repair is not None
                assert error.repair.action
                errors.append(
                    {
                        "operation": "predicate",
                        "family": family,
                        "type": type(error).__name__,
                        "expected": error.expected,
                        "received": error.received,
                        "repair": error.repair.action,
                    }
                )
            with pytest.raises(AnalysisError) as caught:
                left.compare({"reference": mv.baseline(right)})
            error = caught.value
            assert error.expected and error.received and error.repair is not None
            assert error.repair.action
            errors.append(
                {
                    "operation": "comparison",
                    "family": "all",
                    "type": type(error).__name__,
                    "expected": error.expected,
                    "received": error.received,
                    "repair": error.repair.action,
                }
            )
        assert len(errors) == 10 and calls == []
        assert [run_ids(owner) for owner in sessions] == before
        assert [publication_counts(owner) for owner in sessions] == published_before
        assert [snapshot(value) for value in fixed] == saved
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
