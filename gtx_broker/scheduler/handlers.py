"""Handler interfaces for task processing.

Defines the handler contract that vision.py and coding.py will implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
import base64
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


class HandlerResult(Enum):
    """Result of handler execution."""
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    AWAITING_REVIEW = "awaiting_review"
    WORKER_UNAVAILABLE = "worker_unavailable"


@dataclass
class HandlerAttempt:
    """Record of a handler attempt."""
    attempt_number: int
    worker_profile: str
    model_profile: Optional[str]
    start_at: str
    end_at: Optional[str]
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    failure_class: Optional[str]
    tokens_used: Optional[int] = None


class TaskHandler(ABC):
    """Abstract base class for task handlers.

    Each handler type (vision, coding, data, maintenance) implements this interface.
    """

    @property
    @abstractmethod
    def handler_type(self) -> str:
        """Return the handler type (e.g., 'vision', 'coding')."""
        pass

    @abstractmethod
    def can_handle(self, task_payload: Dict[str, Any]) -> bool:
        """Check if this handler can process the task.

        Args:
            task_payload: Task payload from database

        Returns:
            True if handler can process this task
        """
        pass

    @abstractmethod
    def execute(self, task: Dict[str, Any]) -> HandlerResult:
        """Execute the handler on the task.

        Args:
            task: Task dict from scheduler (has 'kind', 'payload', etc.)

        Returns:
            HandlerResult indicating outcome
        """
        pass

    @abstractmethod
    def validate_output(self, output: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate handler output.

        Args:
            output: Handler output data

        Returns:
            Tuple of (is_valid, error_message)
        """
        pass


class VisionHandler(TaskHandler):
    """Vision task handler (implements TaskHandler).

    Processes images using P40 vision worker with approved projector.
    """

    SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
    DEFAULT_PROMPT = """Extract this image into JSON with exactly these keys:
merchant, date, currency, totals, line_items.
Use totals as an object with subtotal, vat, total, and savings numeric values or null.
Use line_items as an array of objects with description, quantity, and price.
Do not infer quantities. Treat discounts and savings as line items only when visibly
shown, with negative prices when appropriate. Return JSON only."""

    def __init__(self, endpoint: Optional[str] = None, model: Optional[str] = None,
                 timeout: Optional[float] = None, max_image_bytes: int = 25 * 1024 * 1024):
        self.endpoint = (endpoint or os.getenv("P40_VISION_ENDPOINT", "http://127.0.0.1:11436/v1")).rstrip("/")
        self.model = model or os.getenv("P40_VISION_MODEL", "Qwen3.5-35B-A3B-UD-Q2_K_XL.gguf")
        self.timeout = timeout or float(os.getenv("P40_VISION_TIMEOUT", "120"))
        self.max_image_bytes = max_image_bytes
        self.last_result: Optional[Dict[str, Any]] = None

    @property
    def handler_type(self) -> str:
        return "vision"

    def can_handle(self, task: Dict[str, Any]) -> bool:
        """Check if this handler can process the task.

        Args:
            task: Task dict from scheduler (has 'kind' key)

        Returns:
            True if handler can process this task
        """
        return task.get("kind") == "vision"

    def execute(self, task: Dict[str, Any]) -> HandlerResult:
        """Execute vision task.

        Args:
            task: Task dict from scheduler (has 'kind', 'payload', 'input_path', etc.)

        Returns:
            HandlerResult indicating success, failure, retry, or worker_unavailable
        """
        self.last_result = None
        image_path = self._image_path(task)
        if image_path is None:
            logger.error("Vision task %s has no usable image path", task.get("id"))
            return HandlerResult.FAILED
        valid, error = self._validate_image(image_path)
        if not valid:
            logger.error("Vision task %s rejected: %s", task.get("id"), error)
            return HandlerResult.FAILED
        if not self._model_available():
            logger.warning("Vision model unavailable at %s", self.endpoint)
            return HandlerResult.WORKER_UNAVAILABLE

        payload = task.get("payload") or {}
        prompt = payload.get("prompt", self.DEFAULT_PROMPT)
        try:
            raw = self._call_endpoint(image_path, prompt)
            result = self._parse_output(raw)
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            logger.warning("Vision endpoint request failed: %s", exc)
            return HandlerResult.RETRY
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Vision output was invalid: %s", exc)
            return HandlerResult.FAILED

        valid, error = self.validate_output(result)
        if not valid:
            logger.error("Vision output failed validation: %s", error)
            return HandlerResult.FAILED
        self.last_result = result
        return HandlerResult.SUCCESS

    @staticmethod
    def _image_path(task: Dict[str, Any]) -> Optional[Path]:
        payload = task.get("payload") or {}
        candidate = task.get("input_path") or payload.get("input_path") or payload.get("image_path")
        if not candidate:
            return None
        path = Path(str(candidate)).expanduser()
        if path.is_dir():
            images = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in {
                ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"
            })
            return images[0] if images else None
        return path

    def _validate_image(self, image_path: Path) -> tuple[bool, str]:
        if not image_path.exists() or not image_path.is_file():
            return False, "image does not exist"
        if image_path.is_symlink():
            return False, "symlink images are not allowed"
        if image_path.stat().st_size > self.max_image_bytes:
            return False, "image exceeds configured size limit"
        mime, _ = mimetypes.guess_type(image_path.name)
        if mime not in self.SUPPORTED_MIME_TYPES:
            return False, f"unsupported image type: {mime or 'unknown'}"
        return True, ""

    def _model_available(self) -> bool:
        request = Request(f"{self.endpoint}/models", method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout, 10)) as response:
                data = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            return False
        models = data.get("data", []) if isinstance(data, dict) else []
        configured_names = {self.model, Path(self.model).name}
        for item in models:
            if not isinstance(item, dict):
                continue
            advertised = [item.get("id"), item.get("name")]
            advertised.extend(item.get("aliases", []))
            if any(
                isinstance(name, str)
                and (name in configured_names or Path(name).name in configured_names)
                for name in advertised
            ):
                return True
        return False

    def _call_endpoint(self, image_path: Path, prompt: str) -> str:
        mime, _ = mimetypes.guess_type(image_path.name)
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]}],
            "temperature": 0,
            "max_tokens": 1200,
        }
        request = Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty vision response")
        return content.strip()

    @staticmethod
    def _parse_output(raw: str) -> Dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("response is not JSON")
            value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("vision response must be an object")
        return value

    def validate_output(self, output: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate vision output.

        Args:
            output: Vision extraction output

        Returns:
            Tuple of (is_valid, error_message)
        """
        required = {"merchant", "date", "currency", "totals", "line_items"}
        missing = required - output.keys()
        if missing:
            return False, f"missing fields: {sorted(missing)}"
        if not isinstance(output["merchant"], str) or not output["merchant"].strip():
            return False, "merchant must be a non-empty string"
        if not isinstance(output["date"], (str, type(None))):
            return False, "date must be a string or null"
        if not isinstance(output["currency"], (str, type(None))) or (
                output["currency"] is not None and len(output["currency"]) != 3):
            return False, "currency must be a three-letter code or null"
        totals = output["totals"]
        if not isinstance(totals, dict):
            return False, "totals must be an object"
        for name in ("subtotal", "vat", "total", "savings"):
            value = totals.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                return False, f"totals.{name} must be numeric or null"
        line_items = output["line_items"]
        if not isinstance(line_items, list):
            return False, "line_items must be an array"
        for index, item in enumerate(line_items):
            if not isinstance(item, dict) or not isinstance(item.get("description"), str):
                return False, f"line_items[{index}] has no description"
            for name in ("quantity", "price"):
                value = item.get(name)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    return False, f"line_items[{index}].{name} must be numeric or null"
        return True, None


class CodingHandler(TaskHandler):
    """Coding task handler (implements TaskHandler).

    Processes coding tasks using P40 coding worker.
    """

    @property
    def handler_type(self) -> str:
        return "coding"

    def can_handle(self, task: Dict[str, Any]) -> bool:
        """Check if this handler can process the task.

        Args:
            task: Task dict from scheduler (has 'kind' key)

        Returns:
            True if handler can process this task
        """
        return task.get("kind") == "coding"

    def execute(self, task: Dict[str, Any]) -> HandlerResult:
        """Execute coding task.

        Args:
            task: Task dict from scheduler (has 'kind', 'payload', 'goal', etc.)

        Returns:
            HandlerResult indicating success, failure, retry, or worker_unavailable
        """
        # TODO: Implement coding execution
        # 1. Clone/fetch repository
        # 2. Load prompt and context
        # 3. Send to P40 coding endpoint (11436)
        # 4. Apply changes to repository
        # 5. Run tests
        # 6. Return result

        # For now, return WORKER_UNAVAILABLE since model is not loaded
        return HandlerResult.WORKER_UNAVAILABLE

    def validate_output(self, output: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate coding output.

        Args:
            output: Coding result with changes, test results, etc.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # TODO: Validate coding output
        # Check: files changed, tests pass, no unintended modifications
        return True, None


# Handler factory
HANDLERS = [VisionHandler(), CodingHandler()]


def initialize_handlers() -> Dict[str, TaskHandler]:
    """Initialize all handlers.
    
    Returns:
        Dict mapping task kind to handler instance
    """
    handlers = {}
    for handler in HANDLERS:
        handlers[handler.handler_type] = handler
    return handlers


def get_handler_for_task(task: Dict[str, Any]) -> Optional[TaskHandler]:
    """Find handler for a task.
    
    Args:
        task: Task dict from scheduler (has 'kind' key)
    
    Returns:
        Matching TaskHandler or None
    """
    task_kind = task.get("kind")
    if not task_kind:
        return None
    
    for handler in HANDLERS:
        if handler.handler_type == task_kind:
            return handler
    return None
