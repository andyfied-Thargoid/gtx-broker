import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from gtx_broker.scheduler import (
    CodingHandler,
    HandlerResult,
    ImageQualityGate,
    ReviewHandler,
    Scheduler,
    SchedulerConfig,
    StorageContract,
)
from gtx_broker.telegram_ingress import TelegramImageIngress


def _image(path: Path, size=(640, 480)) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, size[0] - 20, size[1] - 20), outline="black", width=4)
    image.save(path, format="JPEG")
    return path


def test_quality_gate_records_dimensions_and_flags_duplicate(tmp_path):
    image = _image(tmp_path / "one.jpg")
    gate = ImageQualityGate()
    first = gate.assess(image)
    duplicate = gate.assess(image, [first.metrics["content_hash"]])

    assert first.metrics["width"] == 640
    assert first.metrics["height"] == 480
    assert duplicate.status == "needs_review"
    assert "duplicate content hash" in duplicate.reasons


def test_quality_gate_rejects_corrupt_image(tmp_path):
    image = tmp_path / "broken.jpg"
    image.write_bytes(b"not an image")
    result = ImageQualityGate().assess(image)
    assert result.status == "reject"


def test_telegram_ingress_stages_and_enqueues_idempotently(tmp_path):
    source = _image(tmp_path / "source.jpg")
    storage = StorageContract(tmp_path / "storage")
    scheduler = Scheduler(SchedulerConfig(db_path=str(tmp_path / "storage" / "metadata" / "tasks.db")))
    ingress = TelegramImageIngress(scheduler, storage)
    event = {"source_path": str(source), "chat_id": 12, "message_id": 34, "user_id": 56, "kind": "photo"}

    first = ingress.ingest(event)
    second = ingress.ingest(event)

    assert first.accepted is True
    assert first.status == "queued"
    assert first.task_id == second.task_id
    assert second.status == "queued"
    task = scheduler.get_task(first.task_id)
    assert task["mode"] == "vision"
    assert task["schedule_type"] == "nightly"
    assert Path(task["input_path"]).is_file()


def test_telegram_ingress_persists_explicit_general_image_schema(tmp_path):
    source = _image(tmp_path / "flowers.jpg")
    storage = StorageContract(tmp_path / "storage")
    scheduler = Scheduler(SchedulerConfig(db_path=str(tmp_path / "storage" / "metadata" / "tasks.db")))
    ingress = TelegramImageIngress(scheduler, storage)

    result = ingress.ingest({
        "source_path": str(source),
        "chat_id": 12,
        "message_id": 36,
        "schema": "image_description",
        "prompt": "Describe the visible scene literally.",
    })

    assert result.accepted is True
    task = scheduler.get_task(result.task_id)
    assert task["payload"]["schema"] == "image_description"
    assert task["payload"]["prompt"] == "Describe the visible scene literally."


def test_telegram_ingress_rejects_unknown_schema_before_staging(tmp_path):
    source = _image(tmp_path / "flowers.jpg")
    storage = StorageContract(tmp_path / "storage")
    scheduler = Scheduler(SchedulerConfig(db_path=str(tmp_path / "storage" / "metadata" / "tasks.db")))
    ingress = TelegramImageIngress(scheduler, storage)

    result = ingress.ingest({
        "source_path": str(source),
        "chat_id": 12,
        "message_id": 37,
        "schema": "not-supported",
    })

    assert result.accepted is False
    assert "unsupported vision schema" in result.error
    assert storage.task_ids() == []


def test_telegram_ingress_marks_staged_file_rejected_after_quality_failure(tmp_path):
    source = _image(tmp_path / "source.jpg")
    storage = StorageContract(tmp_path / "storage")
    scheduler = Scheduler(SchedulerConfig(db_path=str(tmp_path / "storage" / "metadata" / "tasks.db")))
    quality_gate = MagicMock()
    quality_gate.assess.side_effect = RuntimeError("quality service failed")
    ingress = TelegramImageIngress(scheduler, storage, quality_gate)

    result = ingress.ingest({"source_path": str(source), "chat_id": 12, "message_id": 35})

    assert result.accepted is False
    assert result.task_id is not None
    assert storage.get_task(result.task_id)["status"] == "rejected"
    assert scheduler.get_task(result.task_id) is None


def test_coding_handler_runs_explicit_executor_and_test(tmp_path):
    worktree = tmp_path / "repo"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=worktree, check=True)
    (worktree / "README").write_text("base")
    subprocess.run(["git", "add", "README"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=worktree, check=True)
    command = [sys.executable, "-c", "open('change.txt', 'w').write('done')"]
    test_command = [sys.executable, "-c", "assert open('change.txt').read() == 'done'"]

    result = CodingHandler().execute({
        "kind": "coding",
        "payload": {
            "worktree_path": str(worktree),
            "executor_command": command,
            "test_command": test_command,
            "require_commit": False,
        },
    })

    assert result is HandlerResult.SUCCESS


def test_review_handler_is_read_only_and_validates_findings(tmp_path):
    worktree = tmp_path / "repo"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    output = {"passed": False, "findings": [{"id": "F-1", "severity": "high"}]}
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(output))})"]

    result = ReviewHandler().execute({
        "kind": "review",
        "payload": {"worktree_path": str(worktree), "review_command": command},
    })

    assert result is HandlerResult.SUCCESS
