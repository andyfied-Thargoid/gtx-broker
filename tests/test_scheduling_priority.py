from pathlib import Path
import sys
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gtx_broker.scheduler import (  # noqa: E402
    EpochManager,
    ScheduleWindow,
    Scheduler,
    SchedulerConfig,
)


def make_scheduler(tmp_path):
    return Scheduler(SchedulerConfig(db_path=str(tmp_path / "tasks.db")))


def test_p40_tasks_are_selected_before_non_p40_immediate_work(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("chat", "query", {}, "immediate", priority=100, idempotency_key="chat")
    scheduler.add_task("code", "coding", {}, "immediate", priority=1, idempotency_key="code")

    with patch.object(scheduler._policy, "get_current_window", return_value=ScheduleWindow.RESTRICTED):
        task = scheduler.get_next_task()

    assert task["id"] == "code"
    assert scheduler.select_worker_for_task(task) == "p40-coding"


def test_image_window_blocks_batch_until_images_are_drained(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("image", "vision", {}, "vision", priority=1, idempotency_key="image")
    scheduler.add_task("batch", "coding", {}, "batch", priority=100, idempotency_key="batch")

    with patch.object(scheduler._policy, "get_current_window", return_value=ScheduleWindow.IMAGE_WINDOW):
        task = scheduler.get_next_task()
    assert task["id"] == "image"

    scheduler.cancel_task("image")
    with patch.object(scheduler._policy, "get_current_window", return_value=ScheduleWindow.BATCH_WINDOW):
        task = scheduler.get_next_task()
    assert task["id"] == "batch"
    assert scheduler.select_worker_for_task(task) == "p40-coding"


def test_review_tag_does_not_fall_back_to_p40(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task(
        "review",
        "coding",
        {},
        "batch",
        priority=10,
        idempotency_key="review",
        review_tag=True,
    )
    task = scheduler.get_task("review")
    assert task["review_tag"] is True
    assert task["review_worker"] == "air-review"
    assert scheduler.select_worker_for_task(task) is None


def test_epoch_barrier_and_review_evidence_are_durable(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("one", "coding", {}, "batch", idempotency_key="one")
    scheduler.add_task("two", "coding", {}, "batch", idempotency_key="two")
    epochs = EpochManager(scheduler)

    assert epochs.create_epoch("epoch-1", "nightly", ["one", "two"])
    assert not epochs.barrier_reached("epoch-1")

    for task_id in ("one", "two"):
        scheduler.claim_task(task_id)
        assert scheduler.start_task(task_id, "p40-coding")
        assert scheduler.complete_task(task_id, result={"commit": task_id})

    assert epochs.barrier_reached("epoch-1")
    assert epochs.trigger_review("epoch-1")
    assert epochs.record_review_result("epoch-1", {"findings": []})
    epoch = epochs.get_epoch("epoch-1")
    assert epoch["review_triggered"] == 1
    assert '"findings": []' in epoch["review_result"]
