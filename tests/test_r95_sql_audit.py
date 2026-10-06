"""Static ownership regressions use small modules without opening datasources."""

from pathlib import Path

from scripts.r95_sql_audit import RETIRED_MODULES, ROOT, audit


def _module(root: Path, name: str, source: str) -> None:
    path = root / "marivo" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_unknown_driver_submission_is_rejected_across_the_product_tree(tmp_path: Path) -> None:
    _module(tmp_path, "new_family/hidden.py", "def read(cursor):\n    cursor.execute('SELECT 1')\n")
    report = audit(tmp_path)
    assert report.scanned_files == ("marivo/new_family/hidden.py",)
    assert [(row.symbol, row.call, row.line) for row in report.failures] == [
        ("read", "cursor.execute", 2)
    ]


def test_renamed_and_getattr_driver_submissions_are_rejected(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "hidden.py",
        "def read(connection):\n    send = getattr(connection, 'execute', None)\n    send('SELECT 1')\n    again = connection.executemany\n    again('SELECT ?', [(1,)])\n",
    )
    assert [row.call for row in audit(tmp_path).failures] == [
        "connection.execute",
        "connection.executemany",
    ]


def test_compiled_sql_rewrite_and_sql_ast_import_are_rejected(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "compiler_patch.py",
        "import sqlglot\ndef lower(backend, expression):\n    compiled = backend.compile(expression)\n    return compiled.replace('SELECT', 'SELECT DISTINCT')\n",
    )
    surfaces = {row.surface for row in audit(tmp_path).failures}
    assert surfaces == {"sql_ast_or_compiler_import", "compilation", "sql_text_rewrite"}


def test_annotated_renamed_compilation_and_compiler_hooks_are_audited(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "compiler_patch.py",
        "from ibis import to_sql as render\n"
        "def lower(backend, expression, replacement):\n"
        "    compiled: str = render(expression)\n"
        "    backend.compiler.visit_Select = replacement\n"
        "    return compiled.replace('SELECT', 'SELECT DISTINCT')\n",
    )
    failures = audit(tmp_path).failures
    assert {row.surface for row in failures} == {
        "compilation",
        "compiler_patch",
        "sql_text_rewrite",
    }
    assert failures[0].call == "ibis.to_sql"


def test_provenance_text_does_not_become_submission_authority(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "provenance.py",
        "import marivo.semantic as ms\nvalue = ms.from_sql(sql='SELECT 1', dialect='duckdb')\n",
    )
    report = audit(tmp_path)
    assert not report.failures
    assert {row.category for row in report.records} == {
        "non_submit_provenance",
        "non_submit_text_clue",
    }


def test_approved_module_does_not_whitelist_another_submitter(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "datasource/adapters.py",
        "def another_reader(cursor):\n    cursor.execute('SELECT 1')\n",
    )
    assert audit(tmp_path).failures[0].symbol == "another_reader"


def test_legacy_statement_and_retired_import_are_rejected(tmp_path: Path) -> None:
    _module(
        tmp_path,
        "hidden.py",
        "from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter\ndef read(adapter):\n    return adapter.statement('SELECT 1')\n",
    )
    assert {row.surface for row in audit(tmp_path).failures} == {
        "retired_entry_import",
        "sql_statement_constructor",
    }


def test_current_product_has_only_classified_submitters_and_no_retired_entries() -> None:
    report = audit()
    assert not report.failures, [
        (row.path, row.symbol, row.surface, row.call) for row in report.failures
    ]
    categories = {row.category for row in report.records}
    assert {
        "approved_provider_statement",
        "terminal_user_raw_sql",
        "store_sqlite_persistence",
        "ibis_governed_read_check_probe",
    } <= categories
    materialization = ROOT / "marivo" / "analysis" / "materialization"
    assert not [name for name in RETIRED_MODULES if (materialization / f"{name}.py").exists()]
    assert len(report.scanned_files) > 300
