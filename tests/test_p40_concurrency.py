"""Test concurrent P40 task startup to verify atomic resource locking."""
import pytest
import threading
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gtx_broker.scheduler import Scheduler, SchedulerConfig


class TestP40Concurrency:
    """Test that P40 exclusive resource locking prevents concurrent execution."""

    @pytest.fixture
    def scheduler(self, tmp_path):
        db_path = tmp_path / "tasks.db"
        config = SchedulerConfig(db_path=str(db_path))
        return Scheduler(config)

    def test_p40_resource_lock_serializes_access(self, scheduler):
        """Test that p40-vision and p40-coding are serialized by BEGIN IMMEDIATE.
        
        Both workers share exclusive_resource="p40". When one task starts with
        BEGIN IMMEDIATE, the other must wait. With retry logic, exactly ONE
        task should eventually succeed.
        
        This verifies the atomic P40 resource locking implementation.
        """
        results = {"p40_vision_started": False, "p40_coding_started": False}
        results_lock = threading.Lock()
        vision_task_id = "p40-concurrent-vision"
        coding_task_id = "p40-concurrent-coding"
        
        # Add both tasks
        scheduler.add_task(vision_task_id, "vision", {}, "vision", 10, "key-vision")
        scheduler.add_task(coding_task_id, "coding", {}, "batch", 10, "key-coding")
        
        # Claim both tasks
        scheduler.claim_task(vision_task_id)
        scheduler.claim_task(coding_task_id)
        
        def try_start_vision():
            success = scheduler.start_task(vision_task_id, "p40-vision", "p40-vision-qwen35")
            with results_lock:
                results["p40_vision_started"] = success
        
        def try_start_coding():
            success = scheduler.start_task(coding_task_id, "p40-coding", "p40-coding-qwen35")
            with results_lock:
                results["p40_coding_started"] = success
        
        # Start both threads simultaneously
        thread_vision = threading.Thread(target=try_start_vision)
        thread_coding = threading.Thread(target=try_start_coding)
        
        thread_vision.start()
        thread_coding.start()
        
        # Give threads time to complete (should succeed within retries)
        thread_vision.join(timeout=10)
        thread_coding.join(timeout=10)
        
        # Verify threads completed
        assert not thread_vision.is_alive(), "Vision thread timed out (possible deadlock)"
        assert not thread_coding.is_alive(), "Coding thread timed out (possible deadlock)"
        
        # Exactly one task should have started (not zero, not both)
        with results_lock:
            started_count = sum([results["p40_vision_started"], results["p40_coding_started"]])
        
        assert started_count == 1, (
            f"Expected exactly 1 task to start, but {started_count} started. "
            f"vision={results['p40_vision_started']}, coding={results['p40_coding_started']}"
        )
