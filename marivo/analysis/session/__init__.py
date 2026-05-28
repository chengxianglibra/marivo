"""Session management for analysis.

The public surface is intentionally narrow:

- ``mv.session.get_or_create(name=...)`` — idempotent: attach if a session
  with that name already exists in the project, otherwise create it. Sets
  the new or attached session as active.
- ``mv.session.current()`` — return a ``SessionSummary`` for the active
  session or ``None`` when there is no active session. Safe probe.
- ``mv.session.list()`` — list non-archived sessions in the project.

Lifecycle helpers ``archive`` / ``delete`` remain on the package for
maintenance scripts. The lower level ``create``, ``attach``, ``switch``,
``active``, ``active_or_create``, and ``history`` functions are no longer
part of the public surface; they continue to exist on
``marivo.analysis.session.attach`` for the test suite and internal use,
but new code should reach for ``get_or_create`` / ``current`` / ``list``.
"""

from importlib import import_module

attach = import_module("marivo.analysis.session.attach")

archive = attach.archive
current = attach.current
delete = attach.delete
get_or_create = attach.get_or_create
list = attach.list_sessions
list_sessions = attach.list_sessions

# Kept for internal/fixture use but not advertised in __all__:
active = attach.active
create = attach.create
history = attach.history
switch = attach.switch

__all__ = [
    "archive",
    "attach",
    "current",
    "delete",
    "get_or_create",
    "list",
    "list_sessions",
]
