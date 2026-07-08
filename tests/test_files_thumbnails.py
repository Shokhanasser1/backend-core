"""PillowThumbnailer — resize-to-fit, format mapping, metadata strip, bad input.

Pure unit test (no DB, no Docker): the adapter is deterministic given the bytes.
FileService.create_thumbnail (which wires this into storage + the files table) is
covered end-to-end by the commerce.product_images feature tests.
"""

from io import BytesIO

import pytest
from PIL import Image

from core.files.adapters.pillow import PillowThumbnailer
from shared.errors import InvariantViolationError


def _image(fmt: str, *, size: tuple[int, int] = (400, 300), mode: str = "RGB") -> bytes:
    buffer = BytesIO()
    fill: tuple[int, int, int] | int = (10, 20, 30) if mode == "RGB" else 128
    Image.new(mode, size, fill).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("src_fmt", "expected_ct", "expected_fmt"),
    [
        ("JPEG", "image/jpeg", "JPEG"),
        ("PNG", "image/png", "PNG"),
        ("WEBP", "image/webp", "WEBP"),
        ("GIF", "image/png", "PNG"),  # a GIF frame is flattened to a static PNG
    ],
)
async def test_generate_resizes_and_maps_format(
    src_fmt: str, expected_ct: str, expected_fmt: str
) -> None:
    data = _image(src_fmt, size=(400, 300))
    out, content_type = await PillowThumbnailer().generate(data, max_edge=128)
    assert content_type == expected_ct
    with Image.open(BytesIO(out)) as img:
        assert img.format == expected_fmt
        assert max(img.size) <= 128
        assert img.size == (128, 96)  # aspect ratio (4:3) preserved


async def test_smaller_image_is_not_enlarged() -> None:
    data = _image("PNG", size=(64, 48))
    out, _ = await PillowThumbnailer().generate(data, max_edge=256)
    with Image.open(BytesIO(out)) as img:
        assert img.size == (64, 48)


async def test_metadata_is_stripped() -> None:
    exif = Image.Exif()
    exif[0x010E] = "confidential description"  # ImageDescription tag
    buffer = BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buffer, format="JPEG", exif=exif)
    source = buffer.getvalue()
    # Sanity: the source really carries the EXIF we want gone.
    with Image.open(BytesIO(source)) as src:
        assert src.getexif().get(0x010E) == "confidential description"

    out, _ = await PillowThumbnailer().generate(source, max_edge=128)
    with Image.open(BytesIO(out)) as img:
        assert dict(img.getexif()) == {}


async def test_transparency_preserved_for_png() -> None:
    data = _image("PNG", size=(200, 200), mode="RGBA")
    out, content_type = await PillowThumbnailer().generate(data, max_edge=64)
    assert content_type == "image/png"
    with Image.open(BytesIO(out)) as img:
        assert img.mode == "RGBA"


async def test_corrupt_image_is_rejected() -> None:
    # Valid magic bytes, garbage payload: passes the sniff but cannot be decoded.
    with pytest.raises(InvariantViolationError):
        await PillowThumbnailer().generate(b"\xff\xd8\xff not really a jpeg", max_edge=128)


async def test_non_positive_edge_is_rejected() -> None:
    with pytest.raises(InvariantViolationError):
        await PillowThumbnailer().generate(_image("PNG"), max_edge=0)
