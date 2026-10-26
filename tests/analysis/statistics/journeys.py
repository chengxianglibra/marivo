"""Shared source fixtures for terminal-export statistical journeys."""

from __future__ import annotations

from pathlib import Path

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    seed_analysis_dsl_database,
)


def prepare(case: DslCase, form: str) -> mv.Session:
    prepare_profiles(case, "KC", form, "us", "UTC", False, followup=True, profile_indices=(0,))
    ms.load(workspace_dir=case.root)
    return mv.session.get_or_create("statistics-journey", timezone="UTC")


def create(root: Path, form: str) -> mv.Session:
    root.mkdir(parents=True, exist_ok=True)
    database = root / "warehouse.duckdb"
    seed_analysis_dsl_database(database, DSL_NAMES, analysis_dsl_rows("j2"), float_amount=False)
    (root / "marivo.toml").write_text('[project]\nname = "statistics"\n')
    for relative, content in analysis_dsl_project_files(DSL_NAMES, database).items():
        path = (
            root
            / "models"
            / (relative if relative.startswith("datasources/") else f"semantic/{relative}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    catalog = ms.load(workspace_dir=root)
    session = mv.session.get_or_create("bootstrap-statistics", timezone="UTC")
    return prepare(DslCase("j2", DSL_NAMES, root, database, catalog, session), form)
