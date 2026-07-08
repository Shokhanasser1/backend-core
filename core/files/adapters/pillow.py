"""Pillow thumbnail adapter — resize to fit a max edge and strip metadata.

The CPU-bound decode/encode runs in a worker thread so the event loop is never
blocked. The output is re-encoded from scratch (no EXIF/ICC carried over) and
stays within the raster allowlist (JPEG/PNG/WebP; a GIF frame is flattened to
PNG), so serving a thumbnail inline is as XSS-safe as the original. A source that
passed the magic-byte sniff but cannot be decoded is a malformed upload
(``InvariantViolationError`` → HTTP 422), never a silent failure.
"""

import asyncio
from io import BytesIO
from typing import ClassVar

from PIL import Image

from shared.errors import InvariantViolationError

# Source PIL format -> (save format, content type). The upload allowlist already
# restricts sources to these four; anything unexpected is flattened to PNG.
_OUTPUT: dict[str, tuple[str, str]] = {
    "JPEG": ("JPEG", "image/jpeg"),
    "PNG": ("PNG", "image/png"),
    "WEBP": ("WEBP", "image/webp"),
    "GIF": ("PNG", "image/png"),  # first frame flattened to a static PNG
}
_DEFAULT_OUTPUT = ("PNG", "image/png")


class PillowThumbnailer:
    backend: ClassVar[str] = "pillow"

    async def generate(self, data: bytes, *, max_edge: int) -> tuple[bytes, str]:
        if max_edge <= 0:
            raise InvariantViolationError("thumbnail max edge must be positive")
        return await asyncio.to_thread(self._render, data, max_edge)

    def _render(self, data: bytes, max_edge: int) -> tuple[bytes, str]:
        try:
            with Image.open(BytesIO(data)) as image:
                save_format, content_type = _OUTPUT.get(
                    (image.format or "").upper(), _DEFAULT_OUTPUT
                )
                prepared = self._normalize_mode(image, save_format)
                # In place; preserves aspect ratio and only ever shrinks.
                prepared.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                # No exif=/icc_profile= passed: metadata is dropped on re-encode.
                prepared.save(buffer, format=save_format)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise InvariantViolationError("image could not be processed") from exc
        return buffer.getvalue(), content_type

    @staticmethod
    def _normalize_mode(image: Image.Image, save_format: str) -> Image.Image:
        """Coerce to a mode the target encoder accepts (JPEG has no alpha; palette
        / CMYK / GIF frames go to lossless RGBA)."""
        if save_format == "JPEG":
            return image.convert("RGB")
        if image.mode in ("RGB", "RGBA", "L"):
            return image
        return image.convert("RGBA")
