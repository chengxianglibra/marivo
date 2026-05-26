"""User-scope datasource profile registry for ``marivo.analysis_py``.

The profile registry persists *non-secret* connection metadata for each
declared datasource at ``$MARIVO_HOME/profiles/profiles.json`` (defaults to
``~/.marivo/profiles/profiles.json``). Sensitive credentials are referenced
via ``<field>_env="VAR_NAME"`` and read from ``os.environ`` at backend-build
time; they are never written to disk.

Profiles are isolated from semantic models, sessions, and any
``<project>/.marivo/`` state — they are user-scope rather than project-scope
and are reused across every project on the same machine.

Public API (callable as ``mv.profiles.<name>``):

- :func:`set`
- :func:`list`
- :func:`describe`
- :func:`remove`
- :func:`test`
- :func:`build_backend` (used by ``mv.session.create / attach``; rarely called directly)
- :func:`audit_project` (cross-check a SemanticProject against the registry)
"""

from __future__ import annotations

from marivo.analysis_py.profiles.audit import ProfileAuditResult, audit_project
from marivo.analysis_py.profiles.registry import (
    ProfileDescription,
    ProfileSummary,
    ProfileTestResult,
    build_backend,
    describe,
    list,
    remove,
    set,
    test,
)

__all__ = [
    "ProfileAuditResult",
    "ProfileDescription",
    "ProfileSummary",
    "ProfileTestResult",
    "audit_project",
    "build_backend",
    "describe",
    "list",
    "remove",
    "set",
    "test",
]
