"""Session management for analysis.

The public surface is intentionally narrow:

- ``mv.session.get_or_create(name=...)`` — idempotent: attach if a session
  with that name already exists in the project, otherwise create it. Sets
  the new or attached session as active.
- ``mv.session.current()`` — return the active ``Session`` or ``None``
  when there is no active session. Safe probe: check and continue work.
- ``mv.session.list()`` — list non-archived sessions in the project.

Lifecycle helpers ``archive`` / ``delete`` remain on the package for
maintenance scripts. The lower level ``create``, ``attach``, ``switch``,
and ``active`` functions are not part of the public surface; new code
should reach for ``get_or_create`` / ``current`` / ``list``.
"""

from importlib import import_module

attach = import_module("marivo.analysis.session.attach")

archive = attach.archive
current = attach.current
delete = attach.delete
get_or_create = attach.get_or_create
list = attach.list_sessions

# Kept for internal/fixture use but not advertised in __all__:
active = attach.active
create = attach.create
switch = attach.switch

__all__ = [
    "archive",
    "attach",
    "current",
    "delete",
    "get_or_create",
    "list",
]
