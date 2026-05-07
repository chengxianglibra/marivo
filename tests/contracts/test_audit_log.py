from __future__ import annotations

import pytest

from app.adapters.local.file_audit_log import FileAuditLog
from app.contracts.ids import UserId
from app.contracts.values import AuditEntry

file_audit_log_factories = [
    ("FileAuditLog", lambda p: FileAuditLog(p / "audit.jsonl")),
]


@pytest.mark.parametrize("name,factory", file_audit_log_factories)
def test_record_appends_line(name, factory, tmp_path):
    log = factory(tmp_path)
    entry = AuditEntry(
        actor=UserId("user1"),
        action="test_action",
        resource_type="session",
        resource_id="sess-001",
    )
    log.record(entry)
    content = (tmp_path / "audit.jsonl").read_text()
    assert "test_action" in content
    assert "user1" in content


@pytest.mark.parametrize("name,factory", file_audit_log_factories)
def test_multiple_records(name, factory, tmp_path):
    log = factory(tmp_path)
    for i in range(3):
        log.record(
            AuditEntry(
                actor=UserId(f"user{i}"),
                action=f"action_{i}",
                resource_type="test",
                resource_id=f"r{i}",
            )
        )
    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3
