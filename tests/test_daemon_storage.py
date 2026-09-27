"""Tests for durable image storage around daemon dispatch."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from gtx_broker.daemon import SchedulerDaemon
from gtx_broker.scheduler import SchedulerConfig, StorageContract
from gtx_broker.scheduler.handlers import HandlerResult


def test_daemon_moves_staged_input_through_processing_to_processed(tmp_path):
    storage_root = tmp_path / "storage"
    db_path = storage_root / "metadata" / "tasks.db"
    daemon = SchedulerDaemon(SchedulerConfig(db_path=str(db_path)))
    storage = StorageContract(storage_root)

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (640, 480), "white").save(source, format="JPEG")
    task_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="1")
    staged = storage.input_path(task_id)
    assert staged is not None
    assert daemon.scheduler.add_task(
        task_id,
        "vision",
        {"image_path": str(staged)},
        mode="immediate",
        input_path=str(staged),
    )

    with (
        patch.object(daemon, "_get_worker_for_task", return_value="p40-vision"),
        patch("gtx_broker.daemon.get_handler_for_task") as get_handler,
    ):
        handler = MagicMock()
        handler.execute.return_value = HandlerResult.SUCCESS
        get_handler.return_value = handler
        assert daemon._dispatch_task(daemon.scheduler.get_next_task()) is True

    task = storage.get_task(task_id)
    assert task is not None
    assert task["status"] == "processed"
    processed = storage.input_path(task_id)
    assert processed is not None
    assert processed.parent == storage.processed_path / task_id
    dispatched_path = Path(handler.execute.call_args.args[0]["input_path"])
    assert dispatched_path.parent == storage.processing_path / task_id
