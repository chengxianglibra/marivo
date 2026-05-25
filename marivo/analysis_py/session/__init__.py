"""Session management for analysis_py."""

from importlib import import_module

attach = import_module("marivo.analysis_py.session.attach")

active = attach.active
active_or_create = attach.active_or_create
archive = attach.archive
create = attach.create
current = attach.current
delete = attach.delete
history = attach.history
list = attach.list_sessions
list_sessions = attach.list_sessions
switch = attach.switch

__all__ = [
    "active",
    "active_or_create",
    "archive",
    "attach",
    "create",
    "current",
    "delete",
    "history",
    "list",
    "list_sessions",
    "switch",
]
