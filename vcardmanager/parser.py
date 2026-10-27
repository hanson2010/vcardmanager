"""Tolerant vCard 2.1 / 3.0 / 4.0 parser.

Problems are recorded as warnings on the card they belong to; a bad line never stops the file.
"""

import io
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from vcardmanager.encoding import decode_quoted_printable, detect_file_encoding, escape, split_unescaped
from vcardmanager.model import STRUCTURED, VERBATIM, Property, VCard

PathLike = str | os.PathLike[str]

_PROPERTY_LINE = re.compile(r"^(?:[A-Za-z0-9_-]+\.)?[A-Za-z0-9_-]+[;:]")
# 2.1 parameters written without a name; these set the encoding, all others are TYPE values
_BARE_ENCODINGS = {"QUOTED-PRINTABLE", "BASE64", "B", "8BIT", "7BIT"}


@dataclass
class _Line:
    number: int
    text: str
    broken: bool = False  # a value continued on the next line without folding


def iter_vcards(paths: PathLike | Iterable[PathLike], encoding: str | None = None) -> Iterator[VCard]:
    """Yield the cards of one file or several files, in order, holding only one card in memory at a time."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    for path in paths:
        name = encoding or detect_file_encoding(path)
        with open(path, encoding=name, newline="") as handle:  # newline="": split on \r\n, \n and \r as is
            yield from iter_parse(handle, source=str(path))


def read_vcards(paths: PathLike | Iterable[PathLike], encoding: str | None = None) -> list[VCard]:
    """Read every card from one file or several files, in order."""
    return list(iter_vcards(paths, encoding))


def parse_text(text: str, source: str | None = None) -> list[VCard]:
    return list(iter_parse(io.StringIO(text, newline=""), source))


def iter_parse(lines: Iterable[str], source: str | None = None) -> Iterator[VCard]:
    """Yield cards from physical lines (with or without line endings)."""
    current: list[_Line] | None = None
    start = 0
    inherited: str | None = None  # VERSION of an outer card that wraps the whole file

    for line in _unfold(lines):
        marker = line.text.strip().upper()
        if marker == "BEGIN:VCARD":
            if current is not None:
                if _is_wrapper(current):
                    inherited = _find_version(current) or inherited
                else:
                    yield _build(current, start, source, inherited, ["missing END:VCARD"])
            current, start = [], line.number
        elif marker == "END:VCARD":
            if current is not None:
                yield _build(current, start, source, inherited, [])
                current = None
            # an END without an open card closes an outer wrapper: nothing to do
        elif current is not None:
            current.append(line)
    if current is not None and not _is_wrapper(current):
        yield _build(current, start, source, inherited, ["missing END:VCARD"])


def parse_property(line: str, version: str = "3.0") -> Property:
    """Parse one unfolded content line. Raises ValueError if it has no `:`."""
    head, text = _split_outside_quotes(line, ":", maxsplit=1)
    parts = _split_outside_quotes(head, ";")
    group, _, name = parts[0].strip().rpartition(".")
    name = name.upper()
    if not name:
        raise ValueError(f"no property name in {line!r}")

    params: dict[str, list[str]] = {}
    encoding = charset = None
    for part in parts[1:]:
        if not part.strip():
            continue
        key, eq, raw_values = part.partition("=")
        key = key.strip()
        if not eq:  # 2.1 bare parameter: `;CELL`, `;QUOTED-PRINTABLE`
            if key.upper() in _BARE_ENCODINGS:
                encoding = key.upper()
            else:
                params.setdefault("TYPE", []).append(key)
            continue
        key = key.upper()
        values = _param_values(raw_values)
        if key == "ENCODING":
            encoding = values[0].upper() if values else None
        elif key == "CHARSET":
            charset = values[0] if values else None
        else:
            params.setdefault(key, []).extend(values)

    if encoding in ("B", "BASE64"):
        params["ENCODING"] = ["b"]
        raw = re.sub(r"\s", "", text)
    else:
        if encoding == "QUOTED-PRINTABLE":
            text = decode_quoted_printable(text, charset)
        raw = _canonical(name, text, version)
    return Property(name, raw, params, group or None)


def _unfold(physical: Iterable[str]) -> Iterator[_Line]:
    """Join folded, soft-broken and broken lines into logical lines.

    Pieces are collected in a list and joined once, so a long folded value (a photo) is not copied per line.
    """
    number, broken, quoted_printable = 0, False, False
    parts: list[str] = []
    for index, raw in enumerate(physical, 1):
        text = raw.rstrip("\r\n")
        if not text.strip():
            continue
        if parts:
            if quoted_printable and parts[-1].endswith("="):
                parts[-1] = parts[-1][:-1]  # soft line break
                parts.append(text.lstrip())
                continue
            if text[0] in " \t":
                parts.append(text[1:])  # folded line
                continue
            if not _PROPERTY_LINE.match(text):
                parts.extend(("\n", text))
                broken = True
                continue
            yield _Line(number, "".join(parts), broken)
        number, parts, broken = index, [text], False
        quoted_printable = "QUOTED-PRINTABLE" in text.split(":", 1)[0].upper()
    if parts:
        yield _Line(number, "".join(parts), broken)


def _is_wrapper(lines: list[_Line]) -> bool:
    """An open card that holds nothing but VERSION wraps the cards that follow it."""
    return all(line.text.split(":", 1)[0].strip().upper() == "VERSION" for line in lines)


def _find_version(lines: list[_Line]) -> str | None:
    for line in lines:
        key, _, value = line.text.partition(":")
        if key.strip().upper() == "VERSION":
            return value.strip()
    return None


def _build(lines: list[_Line], start: int, source: str | None, inherited: str | None, warnings: list[str]) -> VCard:
    version = _find_version(lines)
    if version is None:
        version = inherited or "3.0"
        if inherited is None:
            warnings.append("missing VERSION, assumed 3.0")
    card = VCard(source_version=version, source=f"{source}:{start}" if source else None, warnings=warnings)
    for line in lines:
        if line.broken:
            card.warnings.append(f"line {line.number}: value continued on a new line without folding")
        try:
            prop = parse_property(line.text, version)
        except ValueError as exc:
            card.warnings.append(f"line {line.number}: skipped, {exc}")
            continue
        if prop.name != "VERSION":
            card.add(prop)
    if card.get("FN") is None:
        card.warnings.append("missing FN")
    return card


def _canonical(name: str, text: str, version: str) -> str:
    """Turn a decoded value into the escaped 3.0/4.0 form that `Property.raw` holds."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if version != "2.1":
        return text.replace("\n", "\\n")
    # 2.1 escapes nothing but `\;`, so commas and backslashes are literal
    if name in VERBATIM or name.startswith("X-"):
        return text.replace("\n", "\\n")
    sep = STRUCTURED.get(name)
    if sep == ";":
        return ";".join(escape(part.replace("\\;", ";")) for part in split_unescaped(text, ";"))
    if sep == ",":
        return ",".join(escape(part) for part in text.split(","))
    return escape(text.replace("\\;", ";"))


def _split_outside_quotes(text: str, sep: str, maxsplit: int = -1) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted:
            parts.append("".join(current))
            current = []
            if len(parts) == maxsplit:
                parts.append(text[i + 1 :])
                return parts
        else:
            current.append(ch)
    if maxsplit > 0:
        raise ValueError(f"no {sep!r} in {text!r}")
    parts.append("".join(current))
    return parts


def _param_values(raw: str) -> list[str]:
    return [value.strip().strip('"') for value in _split_outside_quotes(raw, ",")]
