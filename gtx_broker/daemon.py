"""Scheduler daemon: dispatch tasks to handlers with proper state machine flow."""

import logging
import signal
import time
from pathlib import Path
from typing import Any, Dict, Optional

from gtx_broker.scheduler import Scheduler, SchedulerConfig
from gtx_broker.scheduler.handlers import HandlerResult, get_handler_for_task

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
        self.storage = self.scheduler._storage
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
                
                # If no queued task, check for retry_wait tasks and promote them
                if not task:
                    retry_task = self.scheduler.get_retry_wait_task()
                    if retry_task:
                        # Promote retry_wait → queued using existing requeue_retry_wait()
                        promoted = self.scheduler.requeue_retry_wait(retry_task['id'])
                        if promoted:
                            logger.debug(f"Promoted task {retry_task['id']} from retry_wait to queued")
                            # Now get the promoted task
                            task = self.scheduler.get_next_task()
                        else:
                            logger.debug(f"Failed to promote task {retry_task['id']}, skipping")
                    else:
                        logger.debug("No tasks available (queued or retry_wait), waiting...")
                else:
                    logger.info(f"Processing task {task['id']} (kind={task['kind']})")
                    self._dispatch_task(task)
                    continue  # Skip the no-task check below
                
            except Exception as e:
                logger.exception(f"Error in daemon loop: {e}")

            time.sleep(poll_interval)

        logger.info("Daemon stopped")

    def _get_worker_for_task(self, task_or_kind: Any) -> Optional[str]:
        """Get an available worker for a task, including review routing.

        Returns:
            Worker name (profile) or None
        """
        task = task_or_kind if isinstance(task_or_kind, dict) else {"kind": task_or_kind}
        return self.scheduler.select_worker_for_task(task)

    def _claim_staged_input(self, task: Dict[str, Any]) -> bool:
        """Move an ingress file to processing before a worker can read it."""
        input_path = task.get("input_path")
        if not input_path:
            return False
        try:
            Path(input_path).resolve().relative_to(self.storage.incoming_path.resolve())
        except ValueError:
            return False
        metadata = self.storage.claim_for_processing(task["id"])
        if metadata is None:
            return False
        processing_path = self.storage.input_path(task["id"])
        if processing_path is None:
            return False
        task["input_path"] = str(processing_path)
        payload = dict(task.get("payload") or {})
        payload["image_path"] = str(processing_path)
        task["payload"] = payload
        return True

    def _requeue_staged_input(self, task_id: str) -> None:
        self.storage.requeue_for_retry(task_id)

    def _complete_staged_input(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        self.storage.complete_task(task_id, result=result)

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

        # Step 2: Claim durable image storage before a worker can read it.
        storage_claimed = self._claim_staged_input(task)
        if task.get("input_path") and not storage_claimed:
            try:
                Path(task["input_path"]).resolve().relative_to(self.storage.incoming_path.resolve())
                self.scheduler.requeue_claimed_to_retry_wait(task_id)
                return False
            except ValueError:
                pass

        # Step 3: Start the task (with P40 atomic locking)
        # Get worker for this task kind
        worker_name = self._get_worker_for_task(task)
        if not worker_name:
            logger.error(f"No suitable worker found for task kind {task_kind}")
            if storage_claimed:
                self._requeue_staged_input(task_id)
            # Task cannot be processed - requeue to retry_wait
            self.scheduler.requeue_claimed_to_retry_wait(task_id)
            logger.info(f"Task {task_id} requeued to retry_wait - no worker available")
            return False

        # Start task with worker profile
        if not self.scheduler.start_task(task_id, worker_profile=worker_name):
            if storage_claimed:
                self._requeue_staged_input(task_id)
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
                if storage_claimed:
                    self._complete_staged_input(task_id, {"error": "no handler"})
                return False

            # Execute the handler (blocking call - runs on existing event loop)
            result = handler.execute(task)

            # Step 4: Transition to final state based on result
            if result == HandlerResult.SUCCESS:
                # Task completed successfully
                handler_result = getattr(handler, "last_result", None)
                result_payload = handler_result if isinstance(handler_result, dict) else None
                self.scheduler.complete_task(task_id, result=result_payload)
                if storage_claimed:
                    self._complete_staged_input(task_id, result_payload)
                logger.info(f"Task {task_id} completed successfully")

            elif result == HandlerResult.FAILED:
                # Task failed
                self.scheduler.complete_task(task_id, error="Handler execution failed")
                if storage_claimed:
                    self._complete_staged_input(task_id, {"error": "handler execution failed"})
                logger.warning(f"Task {task_id} failed during handler execution")

            elif result == HandlerResult.RETRY:
                # Task should retry later
                self.scheduler.transition_running_to_retry_wait(task_id)
                if storage_claimed:
                    self._requeue_staged_input(task_id)
                logger.info(f"Task {task_id} transitioned to retry_wait")

            elif result == HandlerResult.WORKER_UNAVAILABLE:
                # Worker not available (e.g., model not loaded)
                # Mark as retry_wait to retry later
                self.scheduler.transition_running_to_retry_wait(task_id)
                if storage_claimed:
                    self._requeue_staged_input(task_id)
                logger.info(f"Task {task_id} transitioned to retry_wait - worker unavailable")

            elif result == HandlerResult.AWAITING_REVIEW:
                # Task needs review
                self.scheduler.transition_running_to_awaiting_review(task_id)
                if storage_claimed:
                    self._complete_staged_input(task_id, {"status": "awaiting_review"})
                logger.info(f"Task {task_id} marked for review")

            else:
                # Unknown result - mark as failed
                self.scheduler.complete_task(task_id, error=f"Unknown handler result: {result}")
                if storage_claimed:
                    self._complete_staged_input(task_id, {"error": f"unknown result: {result}"})
                logger.error(f"Task {task_id} resulted in unknown state: {result}")

            return True

        except Exception as e:
            # Unexpected error - mark as failed
            logger.exception(f"Unexpected error during task {task_id} execution: {e}")
            self.scheduler.complete_task(task_id, error=str(e))
            if storage_claimed:
                self._complete_staged_input(task_id, {"error": str(e)})
            return False

        finally:
            # Clean up active tasks tracking
            if task_id in self._active_tasks:
                del self._active_tasks[task_id]
