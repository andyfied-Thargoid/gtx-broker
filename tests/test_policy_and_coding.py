"""Additional tests for coding task support."""
import pytest
import sys
from pathlib import Path

# Get repository root
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gtx_broker.scheduler import (
    Scheduler, SchedulerConfig,
    WorkerRegistry, WorkerStatus, initialize_workers,
)


class TestCodingTaskStartup:
    """Test that coding tasks can use p40-coding worker."""

    @pytest.fixture
    def scheduler(self, tmp_path):
        db_path = tmp_path / "tasks.db"
        config = SchedulerConfig(db_path=str(db_path))
        return Scheduler(config)

    def test_coding_task_uses_p40_coding_worker(self, scheduler):
        """Test that kind='coding' tasks can start with p40-coding worker.
        
        p40-coding has capability='text,code'.
        The partial matching should allow 'code' to match 'coding'.
        """
        task_id = "coding-task-001"
        
        # Add a coding task
        success = scheduler.add_task(task_id, "coding", {"code": "example"}, "batch", 10, "key-coding")
        assert success, "Coding task should be added"
        
        # Claim the task
        claimed = scheduler.claim_task(task_id)
        assert claimed is not None, "Task should be claimable"
        
        # Start with p40-coding worker (capability='text,code')
        # This should succeed due to partial matching: 'code' in 'coding'
        start_success = scheduler.start_task(task_id, "p40-coding", "p40-coding-qwen35")
        assert start_success, "Coding task should start with p40-coding worker"

    def test_coding_task_partial_matching_directions(self, scheduler):
        """Test both directions of partial matching."""
        # Test "code" in "coding" (cap in task)
        task_id_1 = "coding-task-002"
        success = scheduler.add_task(task_id_1, "coding", {}, "batch", 10, "key-2")
        assert success
        claimed = scheduler.claim_task(task_id_1)
        assert claimed
        start_success = scheduler.start_task(task_id_1, "p40-coding", "p40-coding-qwen35")
        assert start_success, "'code' should match 'coding'"
        
        # Complete task_id_1 so it doesn't block subsequent tests
        scheduler.complete_task(task_id_1, result={"ok": True}, error=None)
        
        # Test "vision" in "computer_vision" (task in cap)
        task_id_2 = "vision-task-003"
        success = scheduler.add_task(task_id_2, "computer_vision", {}, "vision", 10, "key-3")
        assert success
        claimed = scheduler.claim_task(task_id_2)
        assert claimed
        start_success = scheduler.start_task(task_id_2, "p40-vision", "p40-vision-qwen35")
        assert start_success, "'vision' capability should match 'computer_vision'"

    def test_vision_task_cannot_use_p40_coding_worker(self, scheduler):
        """Test that kind='vision' tasks cannot use p40-coding worker.
        
        p40-coding has capability='text,code'.
        'vision' does not match 'text' or 'code'.
        """
        task_id = "vision-task-001"
        
        # Add a vision task
        success = scheduler.add_task(task_id, "vision", {"image": "test"}, "vision", 10, "key-vision")
        assert success, "Vision task should be added"
        
        # Claim the task
        claimed = scheduler.claim_task(task_id)
        assert claimed is not None, "Task should be claimable"
        
        # Try to start with p40-coding worker (should fail)
        start_success = scheduler.start_task(task_id, "p40-coding", "p40-coding-qwen35")
        assert not start_success, "Vision task should NOT start with p40-coding worker"

    def test_vision_task_uses_p40_vision_worker(self, scheduler):
        """Test that kind='vision' tasks can start with p40-vision worker."""
        task_id = "vision-task-002"
        
        # Add a vision task
        success = scheduler.add_task(task_id, "vision", {"image": "test"}, "vision", 10, "key-vision-2")
        assert success, "Vision task should be added"
        
        # Claim the task
        claimed = scheduler.claim_task(task_id)
        assert claimed is not None, "Task should be claimable"
        
        # Start with p40-vision worker (capability='vision')
        start_success = scheduler.start_task(task_id, "p40-vision", "p40-vision-qwen35")
        assert start_success, "Vision task should start with p40-vision worker"
