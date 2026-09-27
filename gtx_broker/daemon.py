"""Scheduler daemon: dispatch tasks to handlers with proper state machine flow."""

import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from gtx_broker.scheduler import Scheduler, SchedulerConfig
from gtx_broker.scheduler.handlers import HandlerResult, get_handler_for_task
from gtx_broker.scheduler.workers import WorkerStatus, WorkerRegistry

logger = logging.getLogger(__name__)


class SchedulerDaemon:
    """Scheduler daemon with proper state machine flow.

    Flow:
    1. claim_task() - atomically claim the task (queued→claimed)
    2. start_task() - atomically start with P40 locking (claimed→running)
    3. Execute handler (blocking call)
    4. Transition to final state (running→succeeded/failed/retry_wait/awaiting_review)
    """

    def __init__(self, config: SchedulerConfig):
        """Initialize daemon.

        Args:
            config: Scheduler config
        """
        self.scheduler = Scheduler(config)
        self._active_tasks: Dict[str, bool] = {}
        self._running = False
        self._setup_signals()

        logger.info("Scheduler daemon initialized")

    def _setup_signals(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self._running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def run(self, poll_interval: int = 5):
        """Run the daemon loop.

        Args:
            poll_interval: Seconds between polls (default 5)
        """
        logger.info(f"Starting daemon with poll interval {poll_interval}s")
        self._running = True

        while self._running:
            try:
                # Try to get a queued task first
                task = self.scheduler.get_next_task()
                
                # If no queued task, try retry_wait tasks
                if not task:
                    task = self.scheduler.get_retry_wait_task()
                
                if task:
                    logger.info(f"Processing task {task['id']} (kind={task['kind']})")
                    self._dispatch_task(task)
                else:
                    logger.debug("No tasks available (queued or retry_wait), waiting...")
            except Exception as e:
                logger.exception(f"Error in daemon loop: {e}")

            time.sleep(poll_interval)

        logger.info("Daemon stopped")

    def _get_worker_for_task(self, task_kind: str) -> Optional[str]:
        """Get appropriate worker name for task kind.

        Returns:
            Worker name (profile) or None
        """
        # P40 coding worker
        if task_kind == "coding":
            return "p40-coding"

        # Vision tasks - let start_task() handle worker validation
        if task_kind == "vision":
            # Just return the profile name; start_task() will validate
            # against WorkerRegistry and check P40 availability
            return "p40-vision"

        # Unknown kind
        return None

    def _dispatch_task(self, task: Dict[str, Any]) -> bool:
        """Execute task with proper state machine flow.

        Flow:
        1. claim_task() - atomically claim the task
        2. start_task() - atomically start with P40 locking
        3. Execute handler
        4. Transition to final state (succeeded/failed/retry_wait/awaiting_review)

        Args:
            task: Task dict from get_next_task()

        Returns:
            True if successfully processed, False on error
        """
        task_id = task['id']
        task_kind = task['kind']

        logger.info(f"Dispatching task {task_id} (kind={task_kind})")

        # Step 1: Claim the task
        claimed_task = self.scheduler.claim_task(task_id)
        if not claimed_task:
            logger.warning(f"Failed to claim task {task_id} - may already be claimed or invalid state")
            return False

        logger.info(f"Task {task_id} claimed")

        # Step 2: Start the task (with P40 atomic locking)
        # Get worker for this task kind
        worker_name = self._get_worker_for_task(task_kind)
        if not worker_name:
            logger.error(f"No suitable worker found for task kind {task_kind}")
            # Task cannot be processed - requeue to retry_wait
            self.scheduler.requeue_claimed_to_retry_wait(task_id)
            logger.info(f"Task {task_id} requeued to retry_wait - no worker available")
            return False

        # Start task with worker profile
        if not self.scheduler.start_task(task_id, worker_profile=worker_name):
            logger.error(f"Failed to start task {task_id} - worker unavailable or resource conflict")
            # Task cannot be started - requeue to retry_wait
            self.scheduler.requeue_claimed_to_retry_wait(task_id)
            logger.info(f"Task {task_id} requeued to retry_wait - worker unavailable or P40 busy")
            return False

        logger.info(f"Task {task_id} started with worker {worker_name}")

        # Mark as active
        self._active_tasks[task_id] = True

        try:
            # Step 3: Execute handler
            handler = get_handler_for_task(task)
            if not handler:
                logger.error(f"No handler found for task kind {task_kind}")
                # No handler - permanently fail
                self.scheduler.complete_task(task_id, error="No handler found for task kind")
                return False

            # Execute the handler (blocking call - runs on existing event loop)
            result = handler.execute(task)

            # Step 4: Transition to final state based on result
            if result == HandlerResult.SUCCESS:
                # Task completed successfully
                self.scheduler.complete_task(task_id)
                logger.info(f"Task {task_id} completed successfully")

            elif result == HandlerResult.FAILED:
                # Task failed
                self.scheduler.complete_task(task_id, error="Handler execution failed")
                logger.warning(f"Task {task_id} failed during handler execution")

            elif result == HandlerResult.RETRY:
                # Task should retry later
                self.scheduler.transition_running_to_retry_wait(task_id)
                logger.info(f"Task {task_id} transitioned to retry_wait")

            elif result == HandlerResult.WORKER_UNAVAILABLE:
                # Worker not available (e.g., model not loaded)
                # Mark as retry_wait to retry later
                self.scheduler.transition_running_to_retry_wait(task_id)
                logger.info(f"Task {task_id} transitioned to retry_wait - worker unavailable")

            elif result == HandlerResult.AWAITING_REVIEW:
                # Task needs review
                self.scheduler.transition_running_to_awaiting_review(task_id)
                logger.info(f"Task {task_id} marked for review")

            else:
                # Unknown result - mark as failed
                self.scheduler.complete_task(task_id, error=f"Unknown handler result: {result}")
                logger.error(f"Task {task_id} resulted in unknown state: {result}")

            return True

        except Exception as e:
            # Unexpected error - mark as failed
            logger.exception(f"Unexpected error during task {task_id} execution: {e}")
            self.scheduler.complete_task(task_id, error=str(e))
            return False

        finally:
            # Clean up active tasks tracking
            if task_id in self._active_tasks:
                del self._active_tasks[task_id]
