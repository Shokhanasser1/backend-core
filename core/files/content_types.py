"""Magic-bytes content sniffing (threat model: never trust the client-declared
Content-Type). Pure-python signature check over a small raster-image set — no
system dependency (libmagic) and no way for SVG/HTML to pass as an image, so
inline serving of a sniffed type is XSS-safe.
"""


def sniff_content_type(data: bytes) -> str | None:
    """The canonical content type inferred from the leading bytes, or None if the
    content does not match a supported raster-image signature."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # WebP: "RIFF" <4-byte size> "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
