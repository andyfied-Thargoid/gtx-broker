"""Tests for durable image storage around daemon dispatch."""

import json
from datetime import datetime, timedelta, timezone
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


def test_daemon_requeues_processing_input_after_restart(tmp_path):
    storage_root = tmp_path / "storage"
    config = SchedulerConfig(db_path=str(storage_root / "metadata" / "tasks.db"))
    daemon = SchedulerDaemon(config)
    storage = StorageContract(storage_root)
    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (640, 480), "white").save(source, format="JPEG")
    task_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="2")
    staged = storage.input_path(task_id)
    assert staged is not None
    assert daemon.scheduler.add_task(
        task_id, "vision", {"image_path": str(staged)}, mode="immediate", input_path=str(staged)
    )
    task = daemon.scheduler.get_next_task()
    assert daemon.scheduler.claim_task(task_id)
    assert daemon.scheduler.start_task(task_id, worker_profile="p40-vision")
    assert daemon._claim_staged_input(task)
    assert storage.get_task(task_id)["status"] == "processing"

    restarted = SchedulerDaemon(config)

    assert restarted.scheduler.get_task(task_id)["state"] == "retry_wait"
    recovered = restarted.storage.get_task(task_id)
    assert recovered["status"] == "accepted"
    assert recovered["recovered_after_restart"] is True
    assert restarted.storage.input_path(task_id).parent == restarted.storage.incoming_path / task_id


def test_retention_removes_only_old_processed_inputs(tmp_path):
    storage = StorageContract(tmp_path / "storage")
    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (640, 480), "white").save(source, format="JPEG")

    old_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="old")
    assert storage.claim_for_processing(old_id)
    assert storage.complete_task(old_id, result={"ok": True})
    old_metadata_path = storage.processed_path / old_id / "metadata.json"
    old_metadata = json.loads(old_metadata_path.read_text())
    old_metadata["completed_at"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    old_metadata_path.write_text(json.dumps(old_metadata))

    fresh_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="fresh")
    assert storage.claim_for_processing(fresh_id)
    assert storage.complete_task(fresh_id, result={"ok": True})
    pending_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="pending")
    assert storage.claim_for_processing(pending_id)

    assert storage.cleanup_old_tasks(days=30) == 1
    assert storage.get_task(old_id) is None
    assert storage.get_task(fresh_id) is not None
    assert storage.get_task(pending_id)["status"] == "processing"


def test_retention_removes_old_rejected_and_temporary_inputs(tmp_path):
    storage = StorageContract(tmp_path / "storage")
    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (640, 480), "white").save(source, format="JPEG")
    rejected_id, _ = storage.stage_input(source, source_chat="chat", source_message_id="rejected")
    assert storage.reject_task(rejected_id, "quality")
    rejected_metadata_path = storage.incoming_path / rejected_id / "metadata.json"
    rejected_metadata = json.loads(rejected_metadata_path.read_text())
    rejected_metadata["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    rejected_metadata_path.write_text(json.dumps(rejected_metadata))
    temporary = storage.tmp_path / "stale.tmp"
    temporary.mkdir()
    old = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    import os
    os.utime(temporary, (old, old))

    assert storage.cleanup_old_tasks(days=30) == 2
    assert storage.get_task(rejected_id) is None
    assert not temporary.exists()
