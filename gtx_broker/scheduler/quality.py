"""Deterministic image-quality triage before a vision worker is used."""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat


@dataclass(frozen=True)
class QualityAssessment:
    status: str
    metrics: dict
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "metrics": self.metrics,
            "reasons": list(self.reasons),
        }


class ImageQualityGate:
    """Measure cheap properties and classify an image for scheduling."""

    def __init__(self, *, min_width: int = 640, min_height: int = 480,
                 max_aspect_ratio: float = 4.0, min_focus_score: float = 5.0):
        self.min_width = min_width
        self.min_height = min_height
        self.max_aspect_ratio = max_aspect_ratio
        self.min_focus_score = min_focus_score

    def assess(
        self, image_path: Path, known_hashes: Iterable[str] = ()
    ) -> QualityAssessment:
        path = Path(image_path)
        try:
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                mode = image.mode
                orientation = image.getexif().get(274)
                sample = ImageOps.exif_transpose(image).convert("L")
                sample.thumbnail((256, 256))
                brightness = float(ImageStat.Stat(sample).mean[0])
                histogram = sample.histogram()
                pixels = max(1, sum(histogram))
                clipped_low = sum(histogram[:8]) / pixels
                clipped_high = sum(histogram[-8:]) / pixels
                edges = sample.filter(ImageFilter.FIND_EDGES)
                focus_score = float(ImageStat.Stat(edges).var[0])
        except (OSError, ValueError, SyntaxError, UnidentifiedImageError) as exc:
            return QualityAssessment(
                "reject", {"path": str(path)}, (f"image cannot be decoded: {exc}",)
            )

        aspect_ratio = max(width, height) / max(1, min(width, height))
        metrics = {
            "content_hash": content_hash,
            "width": width,
            "height": height,
            "aspect_ratio": round(aspect_ratio, 4),
            "mode": mode,
            "exif_orientation": orientation,
            "brightness_mean": round(brightness, 3),
            "clipped_low_fraction": round(clipped_low, 5),
            "clipped_high_fraction": round(clipped_high, 5),
            "focus_score": round(focus_score, 3),
        }
        reasons: list[str] = []
        if width < self.min_width or height < self.min_height:
            reasons.append("resolution below configured minimum")
        if aspect_ratio > self.max_aspect_ratio:
            reasons.append("extreme aspect ratio")
        if focus_score < self.min_focus_score:
            reasons.append("low focus score; possible blur")
        if brightness < 20:
            reasons.append("near-black exposure")
        if brightness > 235:
            reasons.append("near-white exposure")
        if clipped_low > 0.6 or clipped_high > 0.6:
            reasons.append("severe clipping")
        if content_hash in set(known_hashes):
            reasons.append("duplicate content hash")

        duplicate = "duplicate content hash" in reasons
        status = "needs_review" if duplicate else ("degraded" if reasons else "pass")
        return QualityAssessment(status, metrics, tuple(reasons))


try:
    from PIL import UnidentifiedImageError
except ImportError:  # pragma: no cover - dependency import is tested at install time
    class UnidentifiedImageError(OSError):
        pass
