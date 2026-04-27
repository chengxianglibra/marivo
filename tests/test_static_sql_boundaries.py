from pathlib import Path

FORBIDDEN_PATTERNS = (
    "INSERT OR IGNORE",
    "ON CONFLICT",
    "datetime('now')",
    "PRAGMA",
    "sqlite_master",
    "pragma_table_info",
)

ALLOWED_APP_FILES = {
    Path("app/storage/dialect.py"),
    Path("app/storage/schema.py"),
    Path("app/storage/sqlite_metadata.py"),
}


def test_shared_app_code_does_not_use_sqlite_specific_sql() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root)
        if relative in ALLOWED_APP_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in text:
                offenders.append(f"{relative}: {pattern}")

    assert offenders == []


def test_intent_seed_paths_do_not_use_sqlite_idempotent_insert() -> None:
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        Path("tests/semantic_test_helpers.py"),
        Path("tests/test_intent_api.py"),
        Path("tests/test_intent_attribute.py"),
        Path("tests/test_intent_validate.py"),
        Path("tests/test_intent_detect.py"),
        Path("tests/test_intent_test.py"),
        Path("tests/test_intent_diagnose.py"),
        Path("tests/test_regression_8_5.py"),
        Path("tests/test_step_metadata.py"),
        Path("tests/test_semantic_typed_end_to_end.py"),
    ]
    offenders = [
        str(path)
        for path in checked_files
        if "INSERT OR IGNORE" in (root / path).read_text(encoding="utf-8")
    ]

    assert offenders == []
