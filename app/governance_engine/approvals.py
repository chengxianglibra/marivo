"""Approval runtime aligned with governance repository and audit trail."""

from __future__ import annotations

from app.governance_engine.repository import GovernanceRepository


class ApprovalRuntime:
    def __init__(self, repository: GovernanceRepository) -> None:
        self.repository = repository

    def request_approval(self, session_id: str, rec_id: str) -> dict[str, object]:
        self.repository.get_recommendation(rec_id)
        existing = self.repository.find_pending_approval_request(rec_id)
        if existing is not None:
            return existing

        request = self.repository.create_approval_request(session_id, rec_id)
        self.repository.record_event(
            session_id=session_id,
            subject_type="approval_request",
            subject_id=request["request_id"],
            event_type="approval_requested",
            detail={"rec_id": rec_id},
        )
        return request

    def list_requests(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        return self.repository.list_approval_requests(session_id=session_id, status=status)

    def get_request(self, request_id: str) -> dict[str, object]:
        return self.repository.get_approval_request(request_id)

    def approve(self, request_id: str, reviewer: str, reason: str = "") -> dict[str, object]:
        request = self.get_request(request_id)
        if request["status"] != "pending":
            raise ValueError(f"Cannot approve request in '{request['status']}' status")
        decided = self.repository.set_approval_decision(request_id, "approved", reviewer, reason)
        self.repository.record_event(
            session_id=request["session_id"],
            subject_type="approval_request",
            subject_id=request_id,
            actor=reviewer,
            event_type="approval_approved",
            detail={"reason": reason, "rec_id": request["rec_id"]},
        )
        return decided

    def reject(self, request_id: str, reviewer: str, reason: str = "") -> dict[str, object]:
        request = self.get_request(request_id)
        if request["status"] != "pending":
            raise ValueError(f"Cannot reject request in '{request['status']}' status")
        decided = self.repository.set_approval_decision(request_id, "rejected", reviewer, reason)
        self.repository.record_event(
            session_id=request["session_id"],
            subject_type="approval_request",
            subject_id=request_id,
            actor=reviewer,
            event_type="approval_rejected",
            detail={"reason": reason, "rec_id": request["rec_id"]},
        )
        return decided

    def auto_flag_recommendations(
        self,
        session_id: str,
        risk_threshold: str = "P0",
    ) -> list[dict[str, object]]:
        risk_levels = ["P0", "P1", "P2", "P3"]
        try:
            threshold_idx = risk_levels.index(risk_threshold)
        except ValueError:
            threshold_idx = 0
        flaggable = set(risk_levels[: threshold_idx + 1])

        created: list[dict[str, object]] = []
        for recommendation in self.repository.list_session_recommendations(session_id):
            if recommendation["risk"] in flaggable:
                created.append(self.request_approval(session_id, recommendation["rec_id"]))
        return created

    def get_request_audit_trail(self, request_id: str) -> list[dict[str, object]]:
        self.get_request(request_id)
        return self.repository.list_events(subject_type="approval_request", subject_id=request_id)
