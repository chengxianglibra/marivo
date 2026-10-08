"""Persisted semantic scope and isolated Store-generation ownership."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import SessionStateError
from tests.support.paths import PROJECT_ROOT


def author_domains(root: Path) -> None:
    for name in ("sales", "finance"):
        directory = root / "models" / "semantic" / name
        directory.mkdir(parents=True)
        (directory / "_domain.py").write_text(
            f"import marivo.semantic as ms\nms.domain(name={name!r}, owner='fixture')\n"
        )
        (directory / "rows.py").write_text(
            "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            "rows = ms.entity(name='rows', datasource=ms.ref.datasource('default'), "
            "source=md.table('rows'), primary_key=['id'])\n"
        )


def test_scope_is_canonical_immutable_and_recovers_in_a_cold_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    author_domains(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    scoped = mv.session.get_or_create("scoped", domains="sales")
    assert scoped.domains == ("sales",)
    assert scoped.catalog.require(ms.ref.entity("sales.rows")).ref.path == "sales.rows"
    with pytest.raises(ms.errors.SemanticRuntimeError):
        scoped.catalog.require(ms.ref.entity("finance.rows"))
    assert mv.session.get_or_create("scoped").domains == ("sales",)
    assert mv.session.get_or_create("scoped", domains=["sales", "sales"]).id == scoped.id
    other = mv.session.get_or_create("other", domains=["sales", "finance", "sales"])
    assert other.domains == ("finance", "sales")
    with pytest.raises(SessionStateError, match="domain"):
        mv.session.get_or_create("scoped", domains="finance")
    current = mv.session.current()
    assert current is not None and current.id == other.id
    code = """import sys
import marivo.analysis as mv
import marivo.semantic as ms
session = mv.session.resume(sys.argv[1], by='id')
assert session.domains == ('sales',)
assert session.catalog.require(ms.ref.entity('sales.rows')).ref.path == 'sales.rows'
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, scoped.id],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT)),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_new_generation_preserves_old_store_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / ".marivo" / "analysis" / "generations" / "v8" / "session_store.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"existing-generation-bytes")
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("new")
    assert session.domains is None
    assert session._runtime.store.layout.generation == 9
    assert old.read_bytes() == b"existing-generation-bytes"


@pytest.mark.parametrize("domains", ["", " sales", [], ["sales", ""], ["sales "]])
def test_invalid_domain_scope_fails_before_store_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domains: str | list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SessionStateError):
        mv.session.get_or_create("invalid", domains=domains)
    assert not (tmp_path / ".marivo" / "analysis").exists()
