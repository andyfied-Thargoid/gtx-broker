"""Safe command-line handoff from Hermes Telegram media to the scheduler."""

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .scheduler import Scheduler, SchedulerConfig, StorageContract
from .scheduler.quality import ImageQualityGate


@dataclass(frozen=True)
class IngressResult:
    accepted: bool
    status: str
    task_id: str | None = None
    path: str | None = None
    quality: dict | None = None
    error: str | None = None


class TelegramImageIngress:
    """Validate, persist, and enqueue one authorized Telegram image event."""

    def __init__(self, scheduler: Scheduler, storage: StorageContract,
                 quality_gate: ImageQualityGate | None = None):
        self.scheduler = scheduler
        self.storage = storage
        self.quality_gate = quality_gate or ImageQualityGate()

    def ingest(self, event: Mapping[str, Any]) -> IngressResult:
        source_value = event.get("source_path")
        if not source_value:
            return IngressResult(False, "rejected", error="source_path is required")
        source_path = Path(str(source_value)).expanduser()
        valid, error, metadata = self.storage.validate_input(source_path)
        if not valid or metadata is None:
            return IngressResult(False, "rejected", error=error)

        source_chat = event.get("chat_id")
        source_message = event.get("message_id")
        key = event.get("idempotency_key") or self.storage.generate_idempotency_key(
            str(source_chat or ""), str(source_message or ""), metadata["content_hash"]
        )
        task_id = self.storage.generate_task_id(idempotency_key=key)
        existing = self.scheduler.get_task(task_id)
        if existing is not None:
            path = existing.get("input_path") or self.storage.input_path(task_id)
            return IngressResult(True, existing.get("state", "queued"), task_id,
                                 str(path) if path else None)

        try:
            task_id, _staged_metadata = self.storage.stage_input(
                source_path,
                source_chat=str(source_chat) if source_chat is not None else None,
                source_message_id=(
                    str(source_message) if source_message is not None else None
                ),
                idempotency_key=key,
            )
            staged_path = self.storage.input_path(task_id)
            if staged_path is None:
                return IngressResult(False, "rejected", task_id=task_id,
                                     error="staged image path is missing")
            quality = self.quality_gate.assess(staged_path)
            self.storage.update_metadata(task_id, {
                "quality": quality.as_dict(),
                "caption": event.get("caption"),
                "media_group_id": event.get("media_group_id"),
                "source_kind": event.get("kind"),
            })
            if quality.status == "reject":
                self.storage.reject_task(task_id, "; ".join(quality.reasons))
                return IngressResult(False, "rejected", task_id, str(staged_path),
                                     quality.as_dict(), "; ".join(quality.reasons))

            payload = {
                "image_path": str(staged_path),
                "source": {
                    "chat_id": source_chat,
                    "message_id": source_message,
                    "user_id": event.get("user_id"),
                    "caption": event.get("caption"),
                    "media_group_id": event.get("media_group_id"),
                },
                "quality": quality.as_dict(),
            }
            added = self.scheduler.add_task(
                task_id,
                "vision",
                payload,
                mode="vision",
                schedule_type="nightly",
                priority=50,
                idempotency_key=key,
                input_path=str(staged_path),
            )
            if not added:
                self.storage.reject_task(task_id, "scheduler rejected duplicate task")
                return IngressResult(False, "rejected", task_id, str(staged_path),
                                     quality.as_dict(), "scheduler rejected task")
            self.storage.update_metadata(task_id, {"status": "queued"})
            return IngressResult(
                True, "queued", task_id, str(staged_path), quality.as_dict()
            )
        except (OSError, ValueError, RuntimeError) as exc:
            return IngressResult(False, "rejected", task_id=task_id, error=str(exc))


def main(argv: list[str] | None = None) -> int:
    """Read one Hermes event from stdin and emit one JSON result."""
    if argv is None:
        argv = sys.argv[1:]
    if argv != ["--json-stdin"]:
        print("usage: gtx-image-ingress --json-stdin", file=sys.stderr)
        return 2
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise TypeError("event must be a JSON object")
        db_path = os.environ.get(
            "GTX_SCHEDULER_DB", "/mnt/scratch/gtx-images/metadata/tasks.db"
        )
        config = SchedulerConfig(db_path=db_path)
        scheduler = Scheduler(config)
        storage = StorageContract(Path(db_path).parent.parent)
        result = TelegramImageIngress(scheduler, storage).ingest(event)
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.accepted else 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"accepted": False, "status": "rejected", "error": str(exc)}))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
