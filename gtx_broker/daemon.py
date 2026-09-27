"""Scheduler daemon for GTX Broker.

Background process that polls the scheduler queue, validates worker health,
dispatches tasks to handlers, and sends admin alerts.

Configuration:
  - Environment variables: TELEGRAM_CHAT_ID, STALE_TIMEOUT_MINUTES, etc.
  - Optional config.yaml: /home/andyfied/.config/gtx-broker/config.yaml
"""

import asyncio
import logging
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from gtx_broker.scheduler import Scheduler, SchedulerConfig, WorkerRegistry
from gtx_broker.scheduler.handlers import get_handler_for_task, HandlerResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gtx-scheduler")


class DaemonConfig:
    """Daemon configuration."""

    def __init__(self):
        self.telegram_chat_id: Optional[str] = None
        self.poll_interval: float = 5.0
        self.max_concurrent: int = 1
        self.stale_timeout_minutes: int = 30
        self.health_check_timeout: float = 2.0
        self.worker_latency_threshold: float = 30.0
        self.shutdown_timeout: int = 60

        # Load from config.yaml if exists
        config_path = Path.home() / ".config" / "gtx-broker" / "config.yaml"
        if config_path.exists():
            self._load_from_file(config_path)

        # Override with environment variables
        self._load_from_env()

    def _load_from_file(self, path: Path):
        """Load configuration from YAML file."""
        try:
            with open(path, "r") as f:
                config = yaml.safe_load(f) or {}
                self.telegram_chat_id = config.get("telegram_chat_id")
        except Exception as e:
            logger.warning(f"Failed to load config from {path}: {e}")

    def _load_from_env(self):
        """Load configuration from environment variables."""
        import os

        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.poll_interval = float(os.getenv("POLL_INTERVAL", "5.0"))
        self.max_concurrent = int(os.getenv("MAX_CONCURRENT", "1"))
        self.stale_timeout_minutes = int(os.getenv("STALE_TIMEOUT_MINUTES", "30"))
        self.health_check_timeout = float(
            os.getenv("HEALTH_CHECK_TIMEOUT", "2.0")
        )
        self.worker_latency_threshold = float(
            os.getenv("WORKER_LATENCY_THRESHOLD", "30.0")
        )
        self.shutdown_timeout = int(os.getenv("SHUTDOWN_TIMEOUT", "60"))


class WorkerHealthChecker:
    """Worker health monitoring using HTTP and GPU metrics."""

    def __init__(self):
        self.health_endpoints = {
            "p40-coding": "http://127.0.0.1:11436/health",
            "p40-vision": "http://127.0.0.1:11436/health",
            "gtx-chat": "http://127.0.0.1:11438/health",
        }

    async def is_worker_healthy(self, endpoint: str) -> bool:
        """Check if worker is healthy via HTTP health endpoint."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, timeout=self.health_check_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "ready"
        except Exception:
            pass
        return False

    def get_gpu_status(self) -> dict:
        """Get GPU status from nvidia-smi."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                status = {}
                for line in lines:
                    parts = line.split(",")
                    if len(parts) == 4:
                        status["gpu_util"] = int(parts[0].strip())
                        status["memory_util"] = int(parts[1].strip())
                        status["memory_used"] = int(parts[2].strip())
                        status["memory_total"] = int(parts[3].strip())
                return status
        except Exception as e:
            logger.error(f"Failed to get GPU status: {e}")
        return {}

    def is_gpu_idle_but_loaded(self, gpu_status: dict) -> bool:
        """Detect if GPU memory is allocated but GPU is idle."""
        if not gpu_status:
            return False
        return (
            gpu_status.get("memory_used", 0) > 0
            and gpu_status.get("gpu_util", 0) == 0
        )


class AdminAlertSender:
    """Send admin alerts via Telegram."""

    def __init__(self, config: DaemonConfig):
        self.config = config
        self._bot = None
        self._bot_lock = asyncio.Lock()

    async def get_bot(self):
        """Get or create Telegram bot instance."""
        if not self.config.telegram_chat_id:
            return None

        if self._bot is None:
            async with self._bot_lock:
                if self._bot is None:
                    from telegram import Bot

                    # Get token from environment
                    import os

                    token = os.getenv("TELEGRAM_BOT_TOKEN")
                    if not token:
                        logger.error("TELEGRAM_BOT_TOKEN not set")
                        return None
                    self._bot = Bot(token=token)
        return self._bot

    async def send_alert(self, message: str, severity: str = "info"):
        """Send an alert to admin via Telegram."""
        if not self.config.telegram_chat_id:
            logger.warning("Telegram alert configured but chat_id not set")
            return

        bot = await self.get_bot()
        if not bot:
            return

        # Add severity emoji
        severity_emojis = {
            "high": "🔴",
            "medium": "🟡",
            "info": "🟢",
        }
        emoji = severity_emojis.get(severity, "ℹ️")
        formatted_message = f"{emoji} *{severity.upper()}*:\n\n{message}"

        try:
            await bot.send_message(
                chat_id=self.config.telegram_chat_id,
                text=formatted_message,
                parse_mode="Markdown",
            )
            logger.info(f"Sent {severity} alert to admin")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")


class SchedulerDaemon:
    """Main scheduler daemon loop."""

    def __init__(self):
        self.config = DaemonConfig()
        self.scheduler = Scheduler(
            SchedulerConfig(
                db_path="/mnt/scratch/gtx-images/metadata/tasks.db",
                max_concurrent=self.config.max_concurrent,
                poll_interval=self.config.poll_interval,
            )
        )
        self.worker_registry = WorkerRegistry(self.scheduler.db_path)
        self.health_checker = WorkerHealthChecker()
        self.alert_sender = AdminAlertSender(self.config)
        self.running = False
        self.active_tasks = set()

    async def get_active_task_count(self) -> int:
        """Get count of currently active (claimed but not completed) tasks."""
        try:
            conn = self.scheduler._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM task_attempts 
                WHERE end_at IS NULL
            """
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def get_active_task_count_sync(self) -> int:
        """Synchronous version for use in async context."""
        return asyncio.get_event_loop().run_in_executor(
            None, self.get_active_task_count
        )

    async def detect_stale_claims(self):
        """Detect and handle stale task claims."""
        stale_timeout = timedelta(minutes=self.config.stale_timeout_minutes)
        cutoff_time = datetime.now(timezone.utc) - stale_timeout

        try:
            conn = self.scheduler._get_connection()
            cursor = conn.cursor()
            
            # Use correct column names from schema
            cursor.execute(
                """
                SELECT ta.task_id, ta.start_at, ta.worker_profile, t.kind 
                FROM task_attempts ta
                JOIN tasks t ON ta.task_id = t.id
                WHERE ta.end_at IS NULL 
                  AND ta.start_at < ?
            """,
                (cutoff_time.isoformat(),),
            )
            stale_claims = cursor.fetchall()
            
            # Update stale claims
            for claim in stale_claims:
                task_id, start_at, worker, kind = claim
                message = (
                    f"Task `{task_id}` ({kind}) claimed by worker `{worker}` at "
                    f"{start_at} but never completed (>{self.config.stale_timeout_minutes}m ago)."
                )
                await self.alert_sender.send_alert(message, severity="high")

                # Mark task as failed_terminal
                cursor.execute(
                    """
                    UPDATE task_attempts 
                    SET end_at = ?, result = ? 
                    WHERE task_id = ? AND end_at IS NULL
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    '{"error": "stale claim detected"}',
                    task_id,
                ),
            )
            conn.commit()
            conn.close()

            if stale_claims:
                logger.warning(f"Detected {len(stale_claims)} stale claims")

        except Exception as e:
            logger.error(f"Failed to detect stale claims: {e}")

    async def check_worker_health(self, endpoint: str) -> bool:
        """Check if a worker endpoint is healthy."""
        http_healthy = await self.health_checker.is_worker_healthy(endpoint)
        if not http_healthy:
            return False

        # Check GPU status
        gpu_status = self.health_checker.get_gpu_status()
        if self.health_checker.is_gpu_idle_but_loaded(gpu_status):
            await self.alert_sender.send_alert(
                f"GPU stall detected: memory allocated but GPU idle", severity="medium"
            )
            return False

        return True

    async def dispatch_task(self, task: dict):
        """Dispatch a single task to its handler."""
        task_id = task["id"]
        task_kind = task["kind"]
        task_payload = task["payload"]
        metadata_path = task.get("input_path", "")
        
        # Add kind to payload for handler routing
        task_payload["handler_type"] = task_kind
        task_payload["kind"] = task_kind

        logger.info(f"Dispatching task {task_id} ({task_kind})")

        # Get handler
        handler = get_handler_for_task(task_payload)
        if not handler:
            logger.error(f"No handler found for task kind: {task_kind}")
            await self.scheduler.set_state(task_id, "failed_terminal", error="no handler")
            return

        try:
            # Execute handler
            result, output, error = handler.execute(task_payload, metadata_path)

            if result == HandlerResult.SUCCESS:
                await self.scheduler.set_state(task_id, "succeeded")
                logger.info(f"Task {task_id} succeeded")
            elif result == HandlerResult.FAILED:
                await self.scheduler.set_state(
                    task_id, "retry_wait", retry_at=datetime.now(timezone.utc) + timedelta(minutes=5)
                )
                logger.warning(f"Task {task_id} failed: {error}")
            elif result == HandlerResult.WORKER_UNAVAILABLE:
                await self.scheduler.set_state(
                    task_id, "retry_wait", retry_at=datetime.now(timezone.utc) + timedelta(minutes=5)
                )
                logger.warning(f"Task {task_id} worker unavailable")
            elif result == HandlerResult.AWAITING_REVIEW:
                await self.scheduler.set_state(task_id, "awaiting_review")
                logger.info(f"Task {task_id} awaiting review")

        except asyncio.TimeoutError:
            await self.scheduler.set_state(task_id, "failed_terminal", error="handler timeout")
            await self.alert_sender.send_alert(
                f"Handler timeout: task {task_id}", severity="high"
            )
            logger.error(f"Task {task_id} timeout")
        except Exception as e:
            await self.scheduler.set_state(
                task_id, "retry_wait", retry_at=datetime.now(timezone.utc) + timedelta(minutes=5), error=str(e)
            )
            logger.error(f"Task {task_id} error: {e}")

    async def run(self):
        """Main daemon loop."""
        self.running = True
        logger.info("Scheduler daemon starting")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(self._shutdown(s))
            )

        while self.running:
            try:
                # Check for stale claims periodically
                await self.detect_stale_claims()

                # Check concurrent task limit
                active_count = await self.get_active_task_count()
                if active_count >= self.config.max_concurrent:
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                # Get next queued task
                task = self.scheduler.get_next_task()
                if not task:
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                # Dispatch task
                await self.dispatch_task(task)

            except Exception as e:
                logger.error(f"Error in dispatch loop: {e}")
                await asyncio.sleep(self.config.poll_interval)

        logger.info("Scheduler daemon stopped")

    async def _shutdown(self, signal_num):
        """Handle shutdown signal."""
        logger.info(f"Received signal {signal_num}, initiating shutdown...")
        self.running = False

        # Wait for active tasks to complete
        if self.active_tasks:
            logger.info(f"Waiting for {len(self.active_tasks)} active tasks to complete...")
            await asyncio.sleep(self.config.shutdown_timeout)

            # Mark remaining tasks as failed if they didn't complete
            for task_id in self.active_tasks.copy():
                await self.scheduler.set_state(
                    task_id, "failed_terminal", error="shutdown timeout"
                )
                self.active_tasks.discard(task_id)


async def main():
    """Entry point."""
    daemon = SchedulerDaemon()
    await daemon.run()


if __name__ == "__main__":
    asyncio.run(main())
