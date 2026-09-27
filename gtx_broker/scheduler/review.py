"""Durable operator review workflow for vision results."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class VisionReviewService:
    """List and decide vision tasks held for human review.

    The storage metadata is written before the scheduler transition. If the
    process stops between those operations, repeating the same decision is
    safe: the task remains awaiting review until the scheduler transition
    succeeds, while the decision is retained for recovery/audit.
    """

    def __init__(self, scheduler, storage):
        self.scheduler = scheduler
        self.storage = storage

    def pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return awaiting-review tasks with their durable image metadata."""
        tasks = self.scheduler.get_tasks_by_state("awaiting_review", limit)
        for task in tasks:
            task["storage"] = self.storage.get_task(task["id"]) or {}
        return tasks

    def approve(
        self, task_id: str, reviewer: str, notes: Optional[str] = None
    ) -> bool:
        """Record an approval and make the task terminally successful."""
        return self._decide(task_id, reviewer, "approved", notes)

    def reject(
        self, task_id: str, reviewer: str, reason: str
    ) -> bool:
        """Record a rejection and make the task terminally failed."""
        return self._decide(task_id, reviewer, "rejected", reason)

    def _decide(
        self,
        task_id: str,
        reviewer: str,
        status: str,
        notes: Optional[str],
    ) -> bool:
        if not isinstance(reviewer, str) or not reviewer.strip():
            return False
        task = self.scheduler.get_task(task_id)
        if not task or task.get("state") != "awaiting_review":
            return False
        decision = {
            "status": status,
            "reviewer": reviewer.strip(),
            "notes": notes,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if not self.storage.update_metadata(task_id, {"review": decision}):
            return False
        if status == "approved":
            return self.scheduler.approve_awaiting_review(task_id)
        return self.scheduler.transition_awaiting_review_to_failed(
            task_id, error=notes or "vision result rejected during review"
        )
