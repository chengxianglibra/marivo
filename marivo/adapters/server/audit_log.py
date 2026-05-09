from __future__ import annotations

import logging

from marivo.contracts.values import AuditEntry


class FileAuditLogAdapter:
    """Logs to the Python ``logging`` module."""

    def __init__(self, logger_name: str = "marivo.audit") -> None:
        self._logger = logging.getLogger(logger_name)

    def record(self, entry: AuditEntry) -> None:
        self._logger.info(
            "audit actor=%s action=%s resource_type=%s resource_id=%s detail=%s",
            entry.actor,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            entry.detail,
        )
