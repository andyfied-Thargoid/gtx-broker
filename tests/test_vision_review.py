from pathlib import Path

from PIL import Image

from gtx_broker.scheduler import (
    Scheduler,
    SchedulerConfig,
    StorageContract,
    VisionReviewService,
)


def _awaiting_review(tmp_path: Path):
    storage_root = tmp_path / "storage"
    scheduler = Scheduler(
        SchedulerConfig(db_path=str(storage_root / "metadata" / "tasks.db"))
    )
    storage = StorageContract(storage_root)
    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (640, 480), "white").save(source, format="JPEG")
    task_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="review")
    input_path = storage.input_path(task_id)
    assert input_path is not None
    assert scheduler.add_task(
        task_id,
        "vision",
        {"image_path": str(input_path), "requires_review": True},
        mode="immediate",
        input_path=str(input_path),
    )
    assert scheduler.claim_task(task_id)
    assert scheduler.start_task(task_id, worker_profile="p40-vision")
    assert scheduler.transition_running_to_awaiting_review(task_id)
    return scheduler, storage, task_id


def test_review_service_lists_and_approves_durable_result(tmp_path):
    scheduler, storage, task_id = _awaiting_review(tmp_path)
    service = VisionReviewService(scheduler, storage)

    pending = service.pending()
    assert [task["id"] for task in pending] == [task_id]
    assert service.approve(task_id, "andy", "receipt checked") is True
    assert scheduler.get_task(task_id)["state"] == "succeeded"
    assert storage.get_task(task_id)["review"]["status"] == "approved"


def test_review_service_rejects_without_approving_other_state(tmp_path):
    scheduler, storage, task_id = _awaiting_review(tmp_path)
    service = VisionReviewService(scheduler, storage)

    assert service.reject(task_id, "andy", "duplicate lines") is True
    assert scheduler.get_task(task_id)["state"] == "failed_terminal"
    assert storage.get_task(task_id)["review"]["status"] == "rejected"
    assert service.approve(task_id, "andy") is False
