"""Independent producer and source-free continuation for native numeric state."""

import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.analysis.numeric.test_native_numeric import _model


def run(root: Path, phase: str) -> None:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    manifest = root / "saved.json"
    if phase == "produce":
        _model(root)
        duckdb.connect(str(root / "source.duckdb")).close()
        model = root / "models/semantic/sales/facts.py"
        model.write_text(
            model.read_text().replace("md.table('facts',", "md.parquet('facts.parquet',")
        )
        pq.write_table(
            pa.table(
                {
                    "id": [1, 2],
                    "day": [date(2026, 7, 1)] * 2,
                    "value": pa.array(
                        [Decimal("1.01"), Decimal("1.02")], type=pa.decimal128(12, 2)
                    ),
                    "weight": [1, 2],
                    "category": ["a", "a"],
                }
            ),
            root / "facts.parquet",
        )
        session = mv.session.get_or_create("native_cold", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        scope = mv.time_scope(start="2026-07-01", end="2026-07-02")
        records = {}
        for name in ("weighted", "mean"):
            fixed = members.observe(
                ms.ref.metric("sales." + name), during=scope, by=(ms.ref.entity("sales.facts"),)
            ).execute()
            records[name] = {
                "ref": fixed.state.artifact_ref.ref,
                "rows": fixed.to_pandas().to_dict(orient="records"),
            }
        manifest.write_text(json.dumps({"session": session.id, "records": records}, default=str))
    else:
        saved = json.loads(manifest.read_text())

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("Native numeric recovery touched a source")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(SourceSession, "bind", forbidden)
            patch.setattr(duckdb, "connect", forbidden)
            patch.setattr(ibis.duckdb, "connect", forbidden)
            session = mv.session.resume(saved["session"], by="id")
            for name, record in saved["records"].items():
                fixed = session.artifact(record["ref"])
                assert isinstance(fixed, mv.MaterializedNumericRelation)
                assert (
                    json.loads(json.dumps(fixed.to_pandas().to_dict(orient="records"), default=str))
                    == record["rows"]
                )
                value = fixed.rollup().execute().to_pandas().value.iloc[0]
                assert float(value) == pytest.approx(3.05 / 3 if name == "weighted" else 1.02)
    print(json.dumps({"phase": phase, "pid": os.getpid()}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
