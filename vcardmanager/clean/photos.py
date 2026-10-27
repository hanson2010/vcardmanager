"""Download photo links and embed the images."""

import base64
import binascii
import io
import re
import time
import urllib.request

from PIL import Image

from vcardmanager.clean.base import Context
from vcardmanager.model import Property, VCard

MAX_PHOTO_BYTES = 10 * 1024 * 1024
# formats every vCard reader shows; anything else (WebP, BMP, TIFF…) is converted to JPEG
KEPT_FORMATS = frozenset({"JPEG", "PNG", "GIF"})

_PHOTO_NOTE = re.compile(r"^Photo:\s*(?P<url>\S+)$")
_DATA_URI = re.compile(r"^data:[\w.+-]+/[\w.+-]+;base64,(?P<data>.*)$", re.IGNORECASE | re.DOTALL)


def default_fetch(url: str, timeout: float = 10.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "vcardmanager"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise OSError(f"larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
    return data


def _photo_url(prop: Property) -> str | None:
    if prop.name == "PHOTO" and "ENCODING" not in prop.params:
        url = prop.raw.strip()
    elif prop.name == "NOTE" and (match := _PHOTO_NOTE.match(prop.value.strip())):
        url = match["url"].replace("\\:", ":")  # Google writes `https\\://`, which unescapes to `https\://`
    else:
        return None
    return url if url.lower().startswith(("http://", "https://")) else None


def _short(url: str, width: int = 80) -> str:
    return url if len(url) <= width else f"{url[: width // 2 - 2]}...{url[-(width // 2 - 1) :]}"


def _download(url: str, ctx: Context) -> bytes | Exception:
    """Download once per URL, logging the start and the outcome of each real download."""
    if url not in ctx.photo_cache:
        number = len(ctx.photo_cache) + 1
        if ctx.log:
            ctx.log(f"Downloading photo {number}: {_short(url)}")
        start = time.perf_counter()
        try:
            ctx.photo_cache[url] = ctx.fetch(url) if ctx.fetch else default_fetch(url, ctx.photo_timeout)
        except Exception as exc:  # any failure keeps the link; the reason goes into a warning
            ctx.photo_cache[url] = exc
        if ctx.log:
            result, seconds = ctx.photo_cache[url], time.perf_counter() - start
            if isinstance(result, Exception):
                ctx.log(f"Photo {number}: failed after {seconds:.1f} s ({result})")
            else:
                ctx.log(f"Photo {number}: {len(result) / 1024:.0f} KB in {seconds:.1f} s")
    return ctx.photo_cache[url]


_UNREADABLE = (OSError, ValueError, Image.DecompressionBombError)


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Return the image to embed and its TYPE.

    JPEG, PNG and GIF are kept byte for byte; other formats are re-encoded as JPEG (transparency becomes
    white). Raises ValueError if the data is not an image Pillow can read.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if (kind := image.format or "") in KEPT_FORMATS:
                return data, kind
            return _to_jpeg(image), "JPEG"
    except _UNREADABLE as exc:
        raise ValueError(f"not a readable image ({exc})") from exc


def compress_image(data: bytes, max_px: int, quality: int = 85) -> bytes | None:
    """A JPEG of at most `max_px` on its longest side, or None if the image is already that small or
    the JPEG would not be smaller. Raises ValueError if the data is not an image Pillow can read."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) <= max_px:
                return None
            image.load()
            image.thumbnail((max_px, max_px))
            compressed = _to_jpeg(image, quality)
    except _UNREADABLE as exc:
        raise ValueError(f"not a readable image ({exc})") from exc
    return compressed if len(compressed) < len(data) else None


def _to_jpeg(image: Image.Image, quality: int = 85) -> bytes:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
    else:
        flat = image.convert("RGB")
    buffer = io.BytesIO()
    flat.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def photo_embed(card: VCard, ctx: Context) -> None:
    """Replace photo links (PHOTO URLs, Google `NOTE:Photo: <url>` lines) with the embedded image."""
    references: dict[str, list[Property]] = {}
    for prop in card.properties:
        if (url := _photo_url(prop)) is not None:
            references.setdefault(url, []).append(prop)
    for url, props in references.items():
        data = _download(url, ctx)
        if isinstance(data, Exception):
            card.warnings.append(f"photo {url}: download failed ({data}), link kept")
            continue
        try:
            image, kind = prepare_image(data)  # shrinking is photo-compression's job, which runs next
        except ValueError as exc:
            card.warnings.append(f"photo {url}: {exc}, link kept")
            continue
        photo = Property("PHOTO", base64.b64encode(image).decode("ascii"), {"ENCODING": ["b"], "TYPE": [kind]})
        first = next(i for i, p in enumerate(card.properties) if p is props[0])
        card.properties[first] = photo
        card.properties = [p for p in card.properties if not any(p is q for q in props[1:])]


def _embedded_image(prop: Property) -> bytes | None:
    """The bytes of an embedded photo: 3.0 `ENCODING=b` or a 4.0 `data:` URI. None for a link."""
    if [e.lower() for e in prop.params.get("ENCODING", [])] == ["b"]:
        encoded = prop.raw
    elif (match := _DATA_URI.match(prop.raw.strip())) is not None:
        encoded = match["data"]
    else:
        return None
    return base64.b64decode(encoded, validate=False)


def photo_compression(card: VCard, ctx: Context) -> None:
    """Shrink embedded photos larger than `photo_max_px` to a JPEG of that size, when that makes them smaller."""
    for index, prop in enumerate(card.properties):
        if prop.name != "PHOTO":
            continue
        try:
            data = _embedded_image(prop)
            if data is None:
                continue
            compressed = compress_image(data, ctx.photo_max_px, ctx.photo_quality)
        except (ValueError, binascii.Error) as exc:
            card.warnings.append(f"PHOTO: {exc}, left unchanged")
            continue
        if compressed is not None:
            params = {k: v for k, v in prop.params.items() if k not in ("ENCODING", "TYPE", "VALUE", "MEDIATYPE")}
            params.update({"ENCODING": ["b"], "TYPE": ["JPEG"]})
            card.properties[index] = Property("PHOTO", base64.b64encode(compressed).decode("ascii"), params, prop.group)
