from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

AgentName = Literal["codex", "claude", "opencode", "openclaw", "hermes"]

SUPPORTED_AGENT_NAMES: tuple[AgentName, ...] = (
    "codex",
    "claude",
    "opencode",
    "openclaw",
    "hermes",
)

MARKER_FILENAME = ".marivo-skill-sync.json"
MANAGED_BY = "marivo"


class SkillAction(TypedDict, total=False):
    skill: str
    status: str
    source: str
    target: str
    reason: str
    backup: str


class AgentSyncReport(TypedDict):
    agent: str
    target_root: str
    actions: list[SkillAction]


class SyncReport(TypedDict):
    status: str
    dry_run: bool
    marivo_version: str
    results: list[AgentSyncReport]


class SkillSyncError(Exception):
    """Raised when bundled Marivo skills cannot be located or synced."""


def resolve_default_target(
    agent: AgentName,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default skill root for a supported agent."""
    values = os.environ if env is None else env
    home_dir = home if home is not None else Path(values.get("HOME", str(Path.home())))

    if agent == "codex":
        root = values.get("CODEX_HOME")
        return _path(root, home_dir / ".codex") / "skills"
    if agent == "claude":
        root = values.get("CLAUDE_CONFIG_DIR")
        return _path(root, home_dir / ".claude") / "skills"
    if agent == "opencode":
        root = values.get("OPENCODE_CONFIG_DIR")
        if root:
            return Path(root).expanduser() / "skill"
        xdg = values.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg).expanduser() / "opencode" / "skill"
        return home_dir / ".config" / "opencode" / "skill"
    if agent == "openclaw":
        root = values.get("OPENCLAW_HOME")
        return _path(root, home_dir / ".openclaw") / "skills" / "marivo"
    if agent == "hermes":
        root = values.get("HERMES_HOME")
        return _path(root, home_dir / ".hermes") / "skills" / "marivo"
    raise AssertionError(f"unsupported agent: {agent}")


def sync_skills(
    *,
    agent: AgentName | str,
    target_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    source_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """Sync bundled Marivo skills into one agent skill root."""
    if agent in SUPPORTED_AGENT_NAMES:
        resolved_target = target_root or resolve_default_target(agent, env=env)
    elif target_root is not None:
        resolved_target = target_root
    else:
        supported = ", ".join(SUPPORTED_AGENT_NAMES)
        raise SkillSyncError(f"Unsupported agent {agent!r}; expected one of: {supported}")

    source = find_bundled_skills_root(source_root)
    version = current_marivo_version()
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
    actions: list[SkillAction] = []

    for skill_dir in iter_skill_dirs(source):
        action = _sync_one_skill(
            agent=agent,
            source_dir=skill_dir,
            target_dir=resolved_target.expanduser() / skill_dir.name,
            marivo_version=version,
            dry_run=dry_run,
            force=force,
            timestamp=timestamp,
        )
        actions.append(action)

    status = "ok"
    if any(action["status"].endswith("conflict") for action in actions):
        status = "conflict"
    return {
        "status": status,
        "dry_run": dry_run,
        "marivo_version": version,
        "results": [
            {
                "agent": agent,
                "target_root": str(resolved_target.expanduser()),
                "actions": actions,
            }
        ],
    }


def merge_reports(reports: list[SyncReport]) -> SyncReport:
    """Merge per-agent sync reports into one CLI result."""
    if not reports:
        return {
            "status": "ok",
            "dry_run": False,
            "marivo_version": current_marivo_version(),
            "results": [],
        }
    status = "conflict" if any(report["status"] == "conflict" for report in reports) else "ok"
    return {
        "status": status,
        "dry_run": all(report["dry_run"] for report in reports),
        "marivo_version": reports[0]["marivo_version"],
        "results": [result for report in reports for result in report["results"]],
    }


def find_bundled_skills_root(explicit_root: Path | None = None) -> Path:
    """Locate bundled skills, preferring package resources over source checkout fallback."""
    candidates: list[Path] = []
    if explicit_root is not None:
        candidates.append(explicit_root)
    candidates.append(Path(__file__).resolve().parent / "bundled")
    candidates.append(Path(__file__).resolve().parents[2] / "marivo-skill")

    for candidate in candidates:
        if list(iter_skill_dirs(candidate)):
            return candidate
    checked = ", ".join(str(path) for path in candidates)
    raise SkillSyncError(f"Bundled Marivo skills were not found; checked: {checked}")


def iter_skill_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def directory_hash(root: Path) -> str:
    """Return a stable hash for a skill directory, excluding the Marivo sync marker."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == MARKER_FILENAME:
            continue
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def current_marivo_version() -> str:
    try:
        return importlib.metadata.version("marivo")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text())
            version = data.get("project", {}).get("version")
            if isinstance(version, str):
                return version
        return "0+unknown"


def _sync_one_skill(
    *,
    agent: str,
    source_dir: Path,
    target_dir: Path,
    marivo_version: str,
    dry_run: bool,
    force: bool,
    timestamp: str,
) -> SkillAction:
    source_hash = directory_hash(source_dir)
    base: SkillAction = {
        "skill": source_dir.name,
        "source": str(source_dir),
        "target": str(target_dir),
    }

    if not target_dir.exists():
        if not dry_run:
            _copy_skill(source_dir, target_dir, agent, marivo_version, source_hash)
        return {**base, "status": "would_create" if dry_run else "created"}

    marker = _read_marker(target_dir)
    target_hash = directory_hash(target_dir)
    managed = _is_managed_marker(marker, source_dir.name)
    unchanged_managed = managed and marker.get("content_hash") == target_hash

    if unchanged_managed and target_hash == source_hash:
        if marker.get("marivo_version") == marivo_version:
            return {**base, "status": "skipped", "reason": "already up to date"}
        if not dry_run:
            _write_marker(target_dir, agent, source_dir, marivo_version, source_hash)
        return {
            **base,
            "status": "would_update" if dry_run else "updated",
            "reason": "refreshed version marker",
        }

    if not unchanged_managed and not force:
        status = "would_conflict" if dry_run else "conflict"
        reason = "local modifications detected" if managed else "existing directory is unmanaged"
        return {**base, "status": status, "reason": reason}

    backup = _backup_path(target_dir, timestamp)
    if not dry_run:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            target_dir.rename(backup)
        _copy_skill(source_dir, target_dir, agent, marivo_version, source_hash)

    action_status = "would_update" if dry_run else "updated"
    return {**base, "status": action_status, "backup": str(backup)}


def _copy_skill(
    source_dir: Path,
    target_dir: Path,
    agent: str,
    marivo_version: str,
    content_hash: str,
) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    _write_marker(target_dir, agent, source_dir, marivo_version, content_hash)


def _write_marker(
    target_dir: Path,
    agent: str,
    source_dir: Path,
    marivo_version: str,
    content_hash: str,
) -> None:
    marker = {
        "managed_by": MANAGED_BY,
        "agent": agent,
        "skill_name": source_dir.name,
        "marivo_version": marivo_version,
        "content_hash": content_hash,
        "synced_at": datetime.now(UTC).isoformat(),
        "source": str(source_dir),
    }
    (target_dir / MARKER_FILENAME).write_text(json.dumps(marker, indent=2) + "\n")


def _read_marker(target_dir: Path) -> dict[str, Any]:
    marker_path = target_dir / MARKER_FILENAME
    if not marker_path.is_file():
        return {}
    try:
        value = json.loads(marker_path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _is_managed_marker(marker: Mapping[str, Any], skill_name: str) -> bool:
    return marker.get("managed_by") == MANAGED_BY and marker.get("skill_name") == skill_name


def _backup_path(target_dir: Path, timestamp: str) -> Path:
    base = target_dir.with_name(f"{target_dir.name}.bak-{timestamp}")
    candidate = base
    index = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{index}")
        index += 1
    return candidate


def _path(value: str | None, fallback: Path) -> Path:
    if value:
        return Path(value).expanduser()
    return fallback.expanduser()
