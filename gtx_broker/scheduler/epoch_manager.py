"""Epoch manager for batch processing and review barriers."""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import sqlite3

from gtx_broker.scheduler import Scheduler

logger = logging.getLogger(__name__)


class EpochManager:
    """Manages batch epochs and review barriers."""

    def __init__(self, scheduler: Scheduler):
        """Initialize epoch manager.

        Args:
            scheduler: Scheduler instance
        """
        self.scheduler = scheduler
        self._db_path = scheduler.db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def create_epoch(self, epoch_id: str, task_ids: List[str]) -> bool:
        """Create a new batch epoch with member tasks.

        Args:
            epoch_id: Unique epoch identifier
            task_ids: List of task IDs to include in epoch

        Returns:
            True if epoch created successfully
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Insert epoch record
            cursor.execute("""
                INSERT OR REPLACE INTO batch_epochs (id, task_count, status, created_at)
                VALUES (?, ?, 'active', ?)
            """, (epoch_id, len(task_ids), datetime.now(timezone.utc).isoformat()))

            # Update all tasks to belong to this epoch
            for task_id in task_ids:
                cursor.execute("""
                    UPDATE tasks SET batch_epoch_id = ?, updated_at = ?
                    WHERE id = ?
                """, (epoch_id, datetime.now(timezone.utc).isoformat(), task_id))

            conn.commit()
            conn.close()
            logger.info(f"Created epoch {epoch_id} with {len(task_ids)} tasks")
            return True

        except sqlite3.OperationalError as e:
            logger.error(f"Failed to create epoch {epoch_id}: {e}")
            return False

    def add_task_to_epoch(self, task_id: str, epoch_id: str) -> bool:
        """Add a task to an existing epoch.

        Args:
            task_id: Task ID to add
            epoch_id: Epoch ID

        Returns:
            True if task added successfully
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Update task
            cursor.execute("""
                UPDATE tasks SET batch_epoch_id = ?, updated_at = ?
                WHERE id = ?
            """, (epoch_id, datetime.now(timezone.utc).isoformat(), task_id))

            # Update epoch task count
            cursor.execute("""
                UPDATE batch_epochs SET task_count = task_count + 1, updated_at = ?
                WHERE id = ?
            """, (datetime.now(timezone.utc).isoformat(), epoch_id))

            conn.commit()
            conn.close()
            return True

        except sqlite3.OperationalError as e:
            logger.error(f"Failed to add task {task_id} to epoch {epoch_id}: {e}")
            return False

    def check_epoch_barrier(self, epoch_id: str) -> bool:
        """Check if all tasks in epoch have reached terminal state.

        Returns:
            True if epoch barrier reached (all tasks complete)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Count tasks not in terminal state
            cursor.execute("""
                SELECT COUNT(*) FROM tasks
                WHERE batch_epoch_id = ?
                  AND state NOT IN ('succeeded', 'failed_terminal', 'awaiting_review')
            """, (epoch_id,))

            incomplete = cursor.fetchone()[0]
            conn.close()

            if incomplete == 0:
                logger.info(f"Epoch barrier reached for {epoch_id}")
                return True
            else:
                logger.debug(f"Epoch {epoch_id} has {incomplete} incomplete tasks")
                return False

        except sqlite3.OperationalError as e:
            logger.error(f"Failed to check epoch barrier {epoch_id}: {e}")
            return False

    def trigger_review(self, epoch_id: str) -> bool:
        """Trigger Air Review for completed epoch.

        Args:
            epoch_id: Epoch ID to review

        Returns:
            True if review triggered successfully
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Update epoch status
            cursor.execute("""
                UPDATE batch_epochs SET status = 'review_pending', updated_at = ?
                WHERE id = ?
            """, (datetime.now(timezone.utc).isoformat(), epoch_id))

            # Mark all tasks in epoch as awaiting_review
            cursor.execute("""
                UPDATE tasks SET state = 'awaiting_review', updated_at = ?
                WHERE batch_epoch_id = ? AND state != 'awaiting_review'
            """, (datetime.now(timezone.utc).isoformat(), epoch_id))

            conn.commit()
            conn.close()

            logger.info(f"Triggered review for epoch {epoch_id}")
            return True

        except sqlite3.OperationalError as e:
            logger.error(f"Failed to trigger review for epoch {epoch_id}: {e}")
            return False

    def record_review_result(self, epoch_id: str, findings: List[Dict[str, Any]]) -> bool:
        """Record Air Review findings for an epoch.

        Args:
            epoch_id: Epoch ID
            findings: List of review findings

        Returns:
            True if result recorded successfully
        """
        try:
            import json
            conn = self._get_connection()
            cursor = conn.cursor()

            # Update epoch with review result
            review_result = json.dumps(findings)
            cursor.execute("""
                UPDATE batch_epochs SET review_result = ?, status = 'completed', updated_at = ?
                WHERE id = ?
            """, (review_result, datetime.now(timezone.utc).isoformat(), epoch_id))

            conn.commit()
            conn.close()
            logger.info(f"Recorded review result for epoch {epoch_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to record review result for epoch {epoch_id}: {e}")
            return False

    def get_epoch_tasks(self, epoch_id: str) -> List[Dict[str, Any]]:
        """Get all tasks in an epoch.

        Args:
            epoch_id: Epoch ID

        Returns:
            List of task dictionaries
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, kind, priority, payload, state, review_tag, schedule_type, batch_epoch_id
                FROM tasks WHERE batch_epoch_id = ?
            """, (epoch_id,))

            tasks = []
            for row in cursor.fetchall():
                tasks.append({
                    "id": row["id"],
                    "kind": row["kind"],
                    "priority": row["priority"],
                    "payload": self.scheduler._json_load(row["payload"]),
                    "state": row["state"],
                    "review_tag": row["review_tag"],
                    "schedule_type": row["schedule_type"],
                })

            conn.close()
            return tasks

        except sqlite3.OperationalError as e:
            logger.error(f"Failed to get epoch tasks for {epoch_id}: {e}")
            return []
