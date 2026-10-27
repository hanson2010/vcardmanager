"""Serialize cards as vCard 3.0 or 4.0."""

import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

from vcardmanager.encoding import fold
from vcardmanager.model import Property, VCard

SUPPORTED_VERSIONS = ("3.0", "4.0")

_IMAGE_TYPES = {"JPEG": "image/jpeg", "JPG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}
_DATA_URI = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.*)$", re.IGNORECASE | re.DOTALL)


def write_vcards(
    cards: Iterable[VCard], target: str | os.PathLike[str] | TextIO, version: str = "3.0", line_ending: str = "\r\n"
) -> None:
    """Write cards one at a time. A file is written next to the target and replaces it only when complete,
    so a failure leaves the old file intact, and an input can safely be overwritten while it is being read."""
    chunks = iter_dumps(cards, version, line_ending)
    if isinstance(target, (str, os.PathLike)):
        atomic_write(target, chunks)
    else:
        for chunk in chunks:
            target.write(chunk)


def atomic_write(path: str | os.PathLike[str], chunks: Iterable[str]) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            for chunk in chunks:
                handle.write(chunk)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def iter_dumps(cards: Iterable[VCard], version: str = "3.0", line_ending: str = "\r\n") -> Iterator[str]:
    """Yield the text of each card."""
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"cannot write vCard {version}; supported: {', '.join(SUPPORTED_VERSIONS)}")
    for card in cards:
        lines = ["BEGIN:VCARD", f"VERSION:{version}"]
        for prop in card.properties:
            lines.extend(fold(_content_line(_convert(prop, version))))
        lines.append("END:VCARD")
        yield line_ending.join(lines) + line_ending


def dumps(cards: Iterable[VCard], version: str = "3.0", line_ending: str = "\r\n") -> str:
    return "".join(iter_dumps(cards, version, line_ending))


def _content_line(prop: Property) -> str:
    name = f"{prop.group}.{prop.name}" if prop.group else prop.name
    params = "".join(f";{key}={','.join(_quote(v) for v in values)}" for key, values in prop.params.items())
    return f"{name}{params}:{prop.raw}"


def _quote(value: str) -> str:
    if any(ch in value for ch in ":;,"):
        return '"' + value.replace('"', "'") + '"'
    return value


def _convert(prop: Property, version: str) -> Property:
    """Map the parameters and binary values that differ between 3.0 and 4.0."""
    params = {key: list(values) for key, values in prop.params.items()}
    raw = prop.raw
    types = params.get("TYPE", [])
    if version == "4.0":
        if any(t.upper() == "PREF" for t in types):
            params["TYPE"] = [t for t in types if t.upper() != "PREF"]
            params.setdefault("PREF", ["1"])
        if [e.lower() for e in params.get("ENCODING", [])] == ["b"]:
            image = next((t for t in params.get("TYPE", []) if t.upper() in _IMAGE_TYPES), None)
            mime = _IMAGE_TYPES[image.upper()] if image else "application/octet-stream"
            raw = f"data:{mime};base64,{raw}"
            del params["ENCODING"]
            params["TYPE"] = [t for t in params.get("TYPE", []) if t != image]
        if not params.get("TYPE"):
            params.pop("TYPE", None)
    else:
        if "PREF" in params:
            del params["PREF"]
            if not any(t.upper() == "PREF" for t in types):
                params["TYPE"] = [*types, "PREF"]
        match = _DATA_URI.match(raw)
        if match:
            subtype = match["mime"].split("/")[1].upper()
            raw = match["data"]
            params["ENCODING"] = ["b"]
            params["TYPE"] = [*params.get("TYPE", []), "JPEG" if subtype == "JPG" else subtype]
    return Property(prop.name, raw, params, prop.group)
