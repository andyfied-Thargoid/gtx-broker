import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gtx_broker.scheduler.handlers import HandlerResult, VisionHandler  # noqa: E402


VALID_RESULT = {
    "merchant": "ASDA",
    "date": "2026-09-26",
    "currency": "GBP",
    "totals": {"subtotal": 31.50, "vat": 3.02, "total": 29.24, "savings": 2.26},
    "line_items": [{"description": "CEREAL", "quantity": None, "price": 1.50}],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def _image(path: Path, format: str = "JPEG") -> Path:
    Image.new("RGB", (640, 480), "white").save(path, format=format)
    return path


def test_vision_handler_sends_image_and_parses_fenced_json(tmp_path):
    image = tmp_path / "receipt.jpg"
    _image(image)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "vision.gguf"}]})
        body = json.loads(request.data)
        image_url = body["messages"][0]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")
        assert body["temperature"] == 0
        return FakeResponse({"choices": [{"message": {"content": "```json\n" + json.dumps(VALID_RESULT) + "\n```"}}]})

    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=fake_urlopen):
        result = handler.execute({"id": "vision-1", "kind": "vision", "payload": {"image_path": str(image)}})

    assert result is HandlerResult.SUCCESS
    assert handler.last_result == VALID_RESULT
    assert len(requests) == 2


def test_vision_handler_accepts_absolute_advertised_model_path(tmp_path):
    image = tmp_path / "receipt.jpg"
    _image(image)

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "/mnt/scratch/models/vision.gguf"}]})
        return FakeResponse({"choices": [{"message": {"content": json.dumps(VALID_RESULT)}}]})

    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=fake_urlopen):
        result = handler.execute({"kind": "vision", "payload": {"image_path": str(image)}})

    assert result is HandlerResult.SUCCESS


def test_vision_handler_reports_unavailable_without_endpoint(tmp_path):
    image = tmp_path / "receipt.jpg"
    _image(image)
    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")

    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=URLError("offline")):
        result = handler.execute({"kind": "vision", "payload": {"image_path": str(image)}})

    assert result is HandlerResult.WORKER_UNAVAILABLE
    assert handler.last_result is None


@pytest.mark.parametrize(
    "bad_result",
    [
        {"merchant": "ASDA", "date": "2026-09-26", "currency": "GBP", "totals": {}, "line_items": [{"description": "x", "price": "bad"}]},
        {**VALID_RESULT, "line_items": [{"description": "discount", "quantity": float("nan"), "price": -2.0}]},
    ],
)
def test_vision_handler_rejects_invalid_structured_output(tmp_path, bad_result):
    image = tmp_path / "receipt.jpg"
    _image(image)
    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "vision.gguf"}]})
        return FakeResponse({"choices": [{"message": {"content": json.dumps(bad_result)}}]})

    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=fake_urlopen):
        result = handler.execute({"kind": "vision", "payload": {"image_path": str(image)}})

    assert result is HandlerResult.FAILED
    assert handler.last_result is None


def test_vision_handler_supports_explicit_general_image_schema(tmp_path):
    image = _image(tmp_path / "flowers.jpg")
    result_payload = {
        "description": "A vase of flowers on a table.",
        "objects": [{"label": "flowers", "attributes": {"color": "mixed"}}],
        "text": [],
        "confidence": 0.91,
    }

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "vision.gguf"}]})
        body = json.loads(request.data)
        assert "description, objects, text, confidence" in body["messages"][0]["content"][0]["text"]
        return FakeResponse({"choices": [{"message": {"content": json.dumps(result_payload)}}]})

    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=fake_urlopen):
        result = handler.execute({
            "kind": "vision",
            "payload": {"image_path": str(image), "schema": "image_description"},
        })

    assert result is HandlerResult.SUCCESS
    assert handler.last_result == result_payload


def test_vision_handler_holds_valid_receipt_for_review(tmp_path):
    image = _image(tmp_path / "receipt.jpg")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "vision.gguf"}]})
        return FakeResponse({"choices": [{"message": {"content": json.dumps(VALID_RESULT)}}]})

    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen", side_effect=fake_urlopen):
        result = handler.execute({
            "kind": "vision",
            "payload": {"image_path": str(image), "requires_review": True},
        })

    assert result is HandlerResult.AWAITING_REVIEW
    assert handler.last_result == VALID_RESULT


FENCE = chr(96) * 3


@pytest.mark.parametrize(
    "raw",
    ["", "not json", FENCE + "json\nnot json\n" + FENCE, FENCE + "json\n[]\n" + FENCE],
)
def test_vision_handler_rejects_malformed_or_non_object_responses(raw):
    with pytest.raises(ValueError):
        VisionHandler._parse_output(raw)


def test_vision_handler_rejects_unknown_schema_without_calling_endpoint(tmp_path):
    image = _image(tmp_path / "flowers.jpg")
    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen") as urlopen:
        result = handler.execute({
            "kind": "vision",
            "payload": {"image_path": str(image), "schema": "unknown"},
        })
    assert result is HandlerResult.FAILED
    urlopen.assert_not_called()


def test_vision_handler_rejects_unreadable_image(tmp_path):
    image = tmp_path / "fake.jpg"
    image.write_bytes(b"not an image")
    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen") as urlopen:
        result = handler.execute({"kind": "vision", "payload": {"image_path": str(image)}})
    assert result is HandlerResult.FAILED
    urlopen.assert_not_called()


def test_vision_handler_rejects_symlink_and_oversized_images(tmp_path):
    image = _image(tmp_path / "real.jpg")
    link = tmp_path / "link.jpg"
    link.symlink_to(image)
    handler = VisionHandler(endpoint="http://vision.test/v1", model="vision.gguf")
    with patch("gtx_broker.scheduler.handlers.urlopen") as urlopen:
        assert handler.execute({"kind": "vision", "payload": {"image_path": str(link)}}) is HandlerResult.FAILED
        assert VisionHandler(
            endpoint="http://vision.test/v1", model="vision.gguf", max_image_bytes=1
        ).execute({"kind": "vision", "payload": {"image_path": str(image)}}) is HandlerResult.FAILED
    urlopen.assert_not_called()
