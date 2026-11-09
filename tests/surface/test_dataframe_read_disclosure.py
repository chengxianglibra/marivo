"""Native persisted-table reads have bounded, reachable Python and CLI Help."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
from marivo._help.render import render_help_text
from marivo.analysis.errors import AnalysisError
from tests.support.typing import check


def test_native_reader_and_result_help_routes(capsys: pytest.CaptureFixture[str]) -> None:
    marivo.help(mv.read_dataframe)
    python = capsys.readouterr().out
    cli = subprocess.check_output(
        [sys.executable, "-m", "marivo", "help", "analysis.read_dataframe"], text=True
    )
    normalized_cli = re.sub(r"marivo help ([a-z0-9_.]+)", r"marivo.help('\1')", cli)
    assert normalized_cli == python
    assert "Target: analysis.read_dataframe" in python and "Kind: callable" in python
    assert "Returns: DataFrameResult" in python
    assert "to_dict()" in python and "project_root" in python and "complete" in python
    signature = next(line for line in python.splitlines() if line.startswith("Signature:"))
    assert "project_root" not in signature
    assert "analysis.dataframe_result.to_pandas" in python
    assert len(python.encode()) < 3000

    results = render_help_text("analysis.results")[0]
    reference = render_help_text("analysis.dataframe_reference")[0]
    assert "analysis.read_dataframe" in results and "analysis.read_dataframe" in reference
    assert "analysis.dataframe_reference.project_root" in reference
    assert "Kind: attribute" in render_help_text("analysis.dataframe_reference.project_root")[0]
    marivo.help(mv.DataFrameResult)
    result = capsys.readouterr().out
    assert "Target: analysis.dataframe_result" in result
    assert "Arrow schema" in result and "freshness" in result
    assert "analysis.read_dataframe" in result
    assert "analysis.dataframe_result.to_pandas" in result


@pytest.mark.parametrize("name", ["kind", "ref", "committed_at", "row_count", "schema"])
def test_result_field_targets_are_static_attributes(name: str) -> None:
    text = render_help_text("analysis.dataframe_result." + name)[0]
    assert "Kind: attribute" in text
    assert "analysis.dataframe_result" in text


def test_result_cards_are_bounded_without_exporting_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = mv.persist_dataframe(
        pd.DataFrame({f"column_{index:03}": ["private cell value"] for index in range(60)}),
        project_root=tmp_path,
    )
    result = mv.read_dataframe(reference)
    identity = repr(result)
    assert len(identity) <= 200 and "\n" not in identity
    assert (
        "kind=dataframe" in identity and reference.result_id in identity and ".show()" in identity
    )
    result.show()
    card = capsys.readouterr().out
    assert "private cell value" not in card and "column_059" not in card
    assert "row_count: 1" in card and "column_count: 60" in card and ".schema" in card
    assert len(card.encode()) <= 8192
    assert result.schema.names == [f"column_{index:03}" for index in range(60)]
    assert len(result.render(max_output_bytes=1024).encode()) <= 1024


def test_read_errors_link_a_resolvable_reader_repair(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError) as failure:
        mv.read_dataframe(
            {
                "schema": "marivo.dataframe_ref/v2",
                "project_root": str(tmp_path),
                "result_id": "df_" + "0" * 32,
            },
        )
    repair = failure.value.repair
    assert repair is not None and repair.help_target.canonical_id == "read_dataframe"
    assert "Target: analysis.read_dataframe" in render_help_text("analysis.read_dataframe")[0]
    assert list(tmp_path.iterdir()) == []


def test_dataframe_reader_has_concrete_input_and_output_types(tmp_path: Path) -> None:
    identity = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    source = tmp_path / f"dataframe_typing_{identity}.py"
    program = """from datetime import datetime
from pathlib import Path
from typing import Literal
from typing_extensions import assert_type
import pandas as pd
import marivo.analysis as mv

def accepted(table: pd.DataFrame) -> None:
    ref = mv.persist_dataframe(table)
    result = mv.read_dataframe(ref)
    assert_type(result, mv.DataFrameResult)
    assert_type(mv.read_dataframe(ref.to_dict()), mv.DataFrameResult)
    assert_type(mv.read_dataframe({"schema": "marivo.dataframe_ref/v2", "project_root": "/saved", "result_id": "df_" + "0" * 32}), mv.DataFrameResult)
    assert_type(result.ref, mv.DataFrameReference)
    assert_type(result.ref.project_root, Path)
    assert_type(result.kind, Literal["dataframe"])
    assert_type(result.row_count, int)
    assert_type(result.committed_at, datetime)
    assert_type(result.to_pandas(), pd.DataFrame)

def rejected(frame: mv.FrameReference, result: mv.DataFrameResult) -> None:
    mv.read_dataframe("df_" + "0" * 32)  # invalid: bare ID
    mv.read_dataframe(frame)  # invalid: AnalysisFrame reference
    mv.read_dataframe({"result_id": "df_" + "0" * 32})  # invalid: missing schema
    mv.read_dataframe({"schema": "wrong", "project_root": "/saved", "result_id": "df_" + "0" * 32})  # invalid: schema literal
    mv.read_dataframe({"schema": "marivo.dataframe_ref/v1", "project_root": "/saved", "result_id": "df_" + "0" * 32})  # invalid: retired reference schema
    mv.read_dataframe({"schema": "marivo.dataframe_ref/v2", "result_id": "df_" + "0" * 32})  # invalid: missing location
    mv.read_dataframe(result.ref, project_root=result.ref.project_root)  # invalid: location override
    result.row_count = 0  # invalid: immutable result
"""
    source.write_text(program)
    completed = check(source)
    output = completed.stdout + completed.stderr
    expected = {
        str(line) for line, text in enumerate(program.splitlines(), 1) if "# invalid:" in text
    }
    actual = set(re.findall(re.escape(str(source)) + r":(\d+): error:", output))
    assert completed.returncode != 0 and actual == expected, output
