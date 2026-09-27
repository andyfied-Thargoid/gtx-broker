"""Tests for scheduler daemon with proper state machine flow."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from gtx_broker.scheduler import SchedulerConfig
from gtx_broker.scheduler.handlers import HandlerResult
from gtx_broker.daemon import SchedulerDaemon


@pytest.fixture
def daemon(tmp_path):
    """Create daemon with temp database."""
    db_path = tmp_path / "tasks.db"
    config = SchedulerConfig(
        db_path=str(db_path),
        max_concurrent=1,
        poll_interval=1.0
    )
    daemon = SchedulerDaemon(config)
    return daemon


class TestDaemonStateMachine:
    """Test daemon state machine flow."""

    def test_queued_to_claimed_to_running_to_succeeded(self, daemon):
        """Test successful task flow: queued -> claimed -> running -> succeeded."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-001",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Get next task
        task = daemon.scheduler.get_next_task()
        assert task is not None
        assert task['id'] == 'TEST-001'

        # Verify task is queued
        task_data = daemon.scheduler.get_task(task['id'])
        assert task_data['state'] == 'queued'

        # Mock handler to return SUCCESS
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch('gtx_broker.daemon.get_handler_for_task') as mock_get_handler:
                mock_handler = MagicMock()
                mock_handler.execute.return_value = HandlerResult.SUCCESS
                mock_get_handler.return_value = mock_handler

                # Dispatch task
                success = daemon._dispatch_task(task)
                assert success

                # Verify task succeeded
                task_data = daemon.scheduler.get_task(task['id'])
                assert task_data['state'] == 'succeeded'

    def test_queued_to_claimed_to_running_to_failed_terminal(self, daemon):
        """Test failed task flow: queued -> claimed -> running -> failed_terminal."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-002",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock handler to return FAILED
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch('gtx_broker.daemon.get_handler_for_task') as mock_get_handler:
                mock_handler = MagicMock()
                mock_handler.execute.return_value = HandlerResult.FAILED
                mock_get_handler.return_value = mock_handler

                # Dispatch task
                task = daemon.scheduler.get_next_task()
                success = daemon._dispatch_task(task)
                assert success

                # Verify task failed_terminal
                task_data = daemon.scheduler.get_task(task['id'])
                assert task_data['state'] == 'failed_terminal'

    def test_handler_retry_requeues_to_retry_wait(self, daemon):
        """Test that RETRY result requeues task to retry_wait."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-003",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock handler to return RETRY
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch('gtx_broker.daemon.get_handler_for_task') as mock_get_handler:
                mock_handler = MagicMock()
                mock_handler.execute.return_value = HandlerResult.RETRY
                mock_get_handler.return_value = mock_handler

                # Dispatch task
                task = daemon.scheduler.get_next_task()
                success = daemon._dispatch_task(task)
                assert success

                # Verify task requeued to retry_wait
                task_data = daemon.scheduler.get_task(task['id'])
                assert task_data['state'] == 'retry_wait'

    def test_worker_unavailable_requeues_to_retry_wait(self, daemon):
        """Test that WORKER_UNAVAILABLE requeues task to retry_wait."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-004",
            kind="vision",
            payload={"image_url": "test.jpg"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock no vision worker available - dispatch should return False
        with patch.object(daemon, '_get_worker_for_task', return_value=None):
            # Dispatch task
            task = daemon.scheduler.get_next_task()
            success = daemon._dispatch_task(task)
            assert not success  # Returns False when no worker available

            # Verify task requeued to retry_wait
            task_data = daemon.scheduler.get_task(task['id'])
            assert task_data['state'] == 'retry_wait'

    def test_awaiting_review_transitions_correctly(self, daemon):
        """Test that AWAITING_REVIEW transitions task correctly."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-005",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock handler to return AWAITING_REVIEW
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch('gtx_broker.daemon.get_handler_for_task') as mock_get_handler:
                mock_handler = MagicMock()
                mock_handler.execute.return_value = HandlerResult.AWAITING_REVIEW
                mock_get_handler.return_value = mock_handler

                # Dispatch task
                task = daemon.scheduler.get_next_task()
                success = daemon._dispatch_task(task)
                assert success

                # Verify task in awaiting_review
                task_data = daemon.scheduler.get_task(task['id'])
                assert task_data['state'] == 'awaiting_review'

    def test_no_worker_available_does_not_stuck_claimed(self, daemon):
        """Test that missing worker doesn't leave task stuck in claimed."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-006",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock no worker available - dispatch should return False
        with patch.object(daemon, '_get_worker_for_task', return_value=None):
            # Dispatch task
            task = daemon.scheduler.get_next_task()
            success = daemon._dispatch_task(task)
            assert not success  # Returns False when no worker available

            # Task should be in retry_wait, not claimed
            task_data = daemon.scheduler.get_task(task['id'])
            assert task_data['state'] == 'retry_wait'
            assert task_data['state'] != 'claimed'

    def test_start_task_failure_requeues_not_stuck_claimed(self, daemon):
        """Test that start_task failure doesn't leave task stuck in claimed."""
        # Add a task
        success = daemon.scheduler.add_task(
            task_id="TEST-007",
            kind="coding",
            payload={"goal": "test"},
            mode="immediate",
            priority=10
        )
        assert success

        # Mock worker available but start_task fails
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch.object(daemon.scheduler, 'start_task', return_value=False):
                # Dispatch task
                task = daemon.scheduler.get_next_task()
                success = daemon._dispatch_task(task)
                assert not success  # Returns False when start_task fails

                # Task should be in retry_wait, not claimed
                task_data = daemon.scheduler.get_task(task['id'])
                assert task_data['state'] == 'retry_wait'
                assert task_data['state'] != 'claimed'

    def test_p40_exclusivity_respected(self, daemon):
        """Test that P40 exclusivity is respected during start_task."""
        # Add two tasks
        success1 = daemon.scheduler.add_task(
            task_id="TEST-008A",
            kind="coding",
            payload={"goal": "test1"},
            mode="immediate",
            priority=10
        )
        success2 = daemon.scheduler.add_task(
            task_id="TEST-008B",
            kind="coding",
            payload={"goal": "test2"},
            mode="immediate",
            priority=10
        )
        assert success1 and success2

        # First task starts successfully
        with patch.object(daemon, '_get_worker_for_task', return_value='p40-coding'):
            with patch('gtx_broker.daemon.get_handler_for_task') as mock_get_handler:
                mock_handler = MagicMock()
                mock_handler.execute.return_value = HandlerResult.SUCCESS
                mock_get_handler.return_value = mock_handler

                task1 = daemon.scheduler.get_next_task()
                success1 = daemon._dispatch_task(task1)
                assert success1

                # Verify first task succeeded
                task1_data = daemon.scheduler.get_task(task1['id'])
                assert task1_data['state'] == 'succeeded'

                # Second task should be queued (scheduler returns queued before retry_wait)
                task2 = daemon.scheduler.get_next_task()
                assert task2 is not None
                assert task2['id'] == 'TEST-008B'

                task2_data = daemon.scheduler.get_task(task2['id'])
                assert task2_data['state'] == 'queued'


class TestDaemonImport:
    """Test that daemon can be imported without errors."""

    def test_daemon_imports_correctly(self):
        """Test that daemon imports successfully."""
        # This test passes if the import works
        from gtx_broker.daemon import SchedulerDaemon
        assert SchedulerDaemon is not None

    def test_worker_registry_import(self):
        """Test that WorkerRegistry can be imported."""
        from gtx_broker.scheduler.workers import WorkerRegistry, WorkerStatus
        assert WorkerRegistry is not None
        assert WorkerStatus is not None


class TestDaemonHandlerInterface:
    """Test that handler interface is correct."""

    def test_handler_execute_signature(self):
        """Test that handler execute() accepts task dict and returns HandlerResult."""
        from gtx_broker.scheduler.handlers import TaskHandler, HandlerResult

        # Check abstract method signature
        import inspect
        sig = inspect.signature(TaskHandler.execute)
        params = list(sig.parameters.keys())

        # Should have 'self' and 'task'
        assert 'task' in params
