"""Session management for analysis_py."""

from importlib import import_module

attach = import_module("marivo.analysis_py.session.attach")

active = attach.active
archive = attach.archive
current = attach.current
delete = attach.delete
get_or_create = attach.get_or_create
history = attach.history
list = attach.list_sessions
list_sessions = attach.list_sessions
switch = attach.switch

__all__ = [
    "active",
    "archive",
    "attach",
    "current",
    "delete",
    "get_or_create",
    "history",
    "list",
    "list_sessions",
    "switch",
]
