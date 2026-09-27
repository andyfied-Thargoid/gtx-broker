"""Bounded batch epoch and review-barrier coordination."""

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Iterable, Optional


class EpochManager:
    """Keep dependent batch work behind a durable review barrier.

    The manager records epoch membership in the scheduler database but does
    not run a reviewer itself.  An unavailable review worker therefore leaves
    the barrier explicit instead of silently dispatching repairs to the P40.
    """

    TERMINAL_STATES = {"succeeded", "failed_terminal", "awaiting_review", "cancelled"}

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def create_epoch(
        self,
        epoch_id: str,
        name: str,
        task_ids: Iterable[str] = (),
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> bool:
        """Create an epoch and attach its initial tasks."""
        start_time = start_time or datetime.now(timezone.utc)
        end_time = end_time or start_time + timedelta(hours=6)
        conn = self.scheduler._get_connection()
        try:
            conn.execute(
                """INSERT INTO batch_epochs
                   (id, name, start_time, end_time, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (epoch_id, name, start_time.isoformat(), end_time.isoformat(),
                 datetime.now(timezone.utc).isoformat()),
            )
            for task_id in task_ids:
                if not self._attach_in_connection(conn, task_id, epoch_id):
                    conn.rollback()
                    return False
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def add_task_to_epoch(self, task_id: str, epoch_id: str) -> bool:
        """Attach an existing task to an existing batch epoch."""
        conn = self.scheduler._get_connection()
        try:
            ok = self._attach_in_connection(conn, task_id, epoch_id)
            if ok:
                conn.commit()
            return ok
        finally:
            conn.close()

    @staticmethod
    def _attach_in_connection(conn, task_id: str, epoch_id: str) -> bool:
        epoch = conn.execute("SELECT id FROM batch_epochs WHERE id = ?", (epoch_id,)).fetchone()
        task = conn.execute("SELECT id, state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if epoch is None or task is None or task["state"] not in {"accepted", "queued"}:
            return False
        conn.execute(
            "UPDATE tasks SET batch_epoch_id = ?, schedule_type = 'batch', updated_at = ? WHERE id = ?",
            (epoch_id, datetime.now(timezone.utc).isoformat(), task_id),
        )
        return True

    def get_epoch(self, epoch_id: str) -> Optional[Dict[str, Any]]:
        conn = self.scheduler._get_connection()
        try:
            row = conn.execute("SELECT * FROM batch_epochs WHERE id = ?", (epoch_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_epoch_tasks(self, epoch_id: str):
        conn = self.scheduler._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE batch_epoch_id = ? ORDER BY created_at, id",
                (epoch_id,),
            ).fetchall()
            return [self.scheduler._row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def barrier_reached(self, epoch_id: str) -> bool:
        """Return true only when every member reached a terminal state."""
        tasks = self.get_epoch_tasks(epoch_id)
        return bool(tasks) and all(task["state"] in self.TERMINAL_STATES for task in tasks)

    def trigger_review(self, epoch_id: str) -> bool:
        """Mark a reached epoch as ready for the independent reviewer."""
        if not self.barrier_reached(epoch_id):
            return False
        conn = self.scheduler._get_connection()
        try:
            updated = conn.execute(
                """UPDATE batch_epochs
                   SET review_triggered = 1, review_barrier = ?, updated_at = ?
                   WHERE id = ? AND review_triggered = 0""",
                (datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), epoch_id),
            ).rowcount
            conn.commit()
            return updated == 1
        finally:
            conn.close()

    def record_review_result(self, epoch_id: str, result: Dict[str, Any]) -> bool:
        """Persist reviewer evidence without changing task outcomes."""
        conn = self.scheduler._get_connection()
        try:
            updated = conn.execute(
                "UPDATE batch_epochs SET review_result = ?, updated_at = ? WHERE id = ? AND review_triggered = 1",
                (json.dumps(result), datetime.now(timezone.utc).isoformat(), epoch_id),
            ).rowcount
            conn.commit()
            return updated == 1
        finally:
            conn.close()
