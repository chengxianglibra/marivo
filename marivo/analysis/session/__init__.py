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

from importlib import import_module as _import_module

_attach_module = _import_module("marivo.analysis.session.attach")

# Python auto-registers the submodule as a package attribute on import;
# remove it so mv.session.attach is not publicly reachable.
if "attach" in globals():
    del globals()["attach"]

archive = _attach_module.archive
current = _attach_module.current
delete = _attach_module.delete
get_or_create = _attach_module.get_or_create
list = _attach_module.list_sessions

# Kept for internal/fixture use but not advertised in __all__:
active = _attach_module.active
create = _attach_module.create
switch = _attach_module.switch

__all__ = [
    "archive",
    "current",
    "delete",
    "get_or_create",
    "list",
]
