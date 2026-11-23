"""Checks on persisted analysis state and generation boundaries."""

from __future__ import annotations

from pathlib import Path

from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.prior_generations import (
    discover_prior_generations,
    prior_generation_guidance,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.cli.doctor.report import DoctorCheck, DoctorSection


def state_section(root: Path) -> DoctorSection:
    analysis_dir = root / ".marivo" / "analysis"
    layout = MaterializationLayout(root)
    db_path = layout.store_db
    checks: list[DoctorCheck] = []
    if not analysis_dir.exists():
        checks.append(
            DoctorCheck(
                id="state.analysis_dir",
                label=".marivo/analysis",
                status="skipped",
                summary=f"{analysis_dir} does not exist",
            )
        )
        return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))
    checks.append(
        DoctorCheck(
            id="state.analysis_dir",
            label=".marivo/analysis",
            status="ok",
            summary=f"{analysis_dir} exists",
        )
    )
    prior = discover_prior_generations(layout)
    if prior:
        checks.append(
            DoctorCheck(
                id="state.prior_generations",
                label="Older Session Stores",
                status="warning",
                summary=prior_generation_guidance(prior),
                details={
                    "current_generation": layout.generation,
                    "stores": [
                        {
                            "generation": item.generation,
                            "path": str(item.path),
                            "file_mtime": item.file_mtime.isoformat(),
                        }
                        for item in prior
                    ],
                },
            )
        )
    try:
        db_path.lstat()
    except FileNotFoundError:
        checks.append(
            DoctorCheck(
                id="state.session_store",
                label="session_store.db",
                status="skipped",
                summary=f"{db_path} does not exist",
            )
        )
        return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))
    except OSError:
        # Let the owning Store validator report unavailable current state below.
        pass
    try:
        SessionStore.open_existing(root)
    except (AnalysisError, OSError) as exc:
        checks.append(
            DoctorCheck(
                id="state.session_store",
                label="session_store.db",
                status="fail",
                summary=f"could not read session store: {type(exc).__name__}: {exc}",
                details={"path": str(db_path)},
            )
        )
    else:
        checks.append(
            DoctorCheck(
                id="state.session_store",
                label="session_store.db",
                status="ok",
                summary=f"current v{layout.generation} analysis store is readable",
                details={"path": str(db_path)},
            )
        )
    return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))
