"""Core-only cold interpretation reads without any native backend driver."""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.packaging.wheel_probe import assert_installed_origin
from tests.support.json import checked, obj, read


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("core-only interpretation read accessed a source or current Semantic")


def run(root: Path) -> dict[str, object]:
    state = read(root / "interpretation.json")
    with (
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
    ):
        for family in ("history", "statistics"):
            os.environ["MARIVO_PROJECT_ROOT"] = str(root / family)
            session = mv.session.resume(str(state[family + "_session"]), by="id")
            runs = session.runs().items
            for name, artifact_id in obj(state[family + "_ids"]).items():
                value = session.artifact(str(artifact_id))
                if name in ("coefficient", "selected"):
                    assert isinstance(value, mv.MaterializedAssociationResult)
                    value = value.coefficient if name == "coefficient" else value.selected
                assert isinstance(value, (_MaterializedRead, mv.MaterializedTable))
                expected = obj(obj(state["snapshots"])[name])
                facts = (
                    dict(value.contract()._facts) if isinstance(value, _MaterializedRead) else {}
                )
                assert checked(facts) == expected["facts"]
                output = io.StringIO()
                with redirect_stdout(output):
                    value.show(n=0, max_output_bytes=None)
                assert output.getvalue() == expected["card"]
                assert (
                    checked(
                        json.loads(json.dumps(value.to_pandas().to_dict("records"), default=str))
                    )
                    == expected["rows"]
                )
            assert session.runs().items == runs
    assert "duckdb" not in sys.modules
    return {"phase": "base-cold", "pid": os.getpid(), "origin": assert_installed_origin()}


if __name__ == "__main__":
    assert_installed_origin()
    print(json.dumps(run(Path(sys.argv[1]))))
