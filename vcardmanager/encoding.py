"""Low-level text handling: escaping, folding, charsets and quoted-printable."""

import codecs
import os
import quopri

# Tried in order when a file does not say how it is encoded. GB18030 is a superset of GBK and GB2312.
FALLBACK_ENCODINGS = ("utf-8", "gb18030", "big5", "shift_jis")

_UNESCAPES = {"\\": "\\", "n": "\n", "N": "\n", ",": ",", ";": ";"}


def unescape(text: str) -> str:
    r"""Undo vCard 3.0/4.0 escaping (`\\`, `\n`, `\,`, `\;`). Unknown escapes are kept as written."""
    if "\\" not in text:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append(_UNESCAPES.get(nxt, ch + nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def escape(text: str) -> str:
    """Escape a text value for vCard 3.0/4.0. Line breaks become `\\n`."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def split_unescaped(text: str, sep: str) -> list[str]:
    """Split on `sep` where it is not escaped with a backslash. Escapes are kept in the parts."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current.append(text[i : i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def fold(line: str, limit: int = 75) -> list[str]:
    """Fold a logical line into physical lines of at most `limit` UTF-8 octets.

    Continuation lines start with one space, which counts towards the limit. A character is never split.
    """
    if line.isascii():  # fast path for base64 photos and most values: one octet per character
        if len(line) <= limit:
            return [line]
        return [line[:limit]] + [" " + line[i : i + limit - 1] for i in range(limit, len(line), limit - 1)]
    lines: list[str] = []
    current: list[str] = []
    size = 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > limit and current:
            lines.append("".join(current))
            current = [" "]
            size = 1
        current.append(ch)
        size += width
    lines.append("".join(current))
    return lines


def decode_bytes(data: bytes, encoding: str | None = None) -> str:
    """Decode a whole file. Uses `encoding` if given, a BOM if present, else the first fallback that fits."""
    if encoding:
        return data.decode(encoding)
    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(name)
    for name in FALLBACK_ENCODINGS:
        try:
            return data.decode(name)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def detect_file_encoding(path: str | os.PathLike[str], chunk_size: int = 1 << 20) -> str:
    """Pick the encoding for a file like `decode_bytes` does, without holding the whole file in memory."""
    with open(path, "rb") as handle:
        head = handle.read(4)
    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if head.startswith(bom):
            return name
    for name in FALLBACK_ENCODINGS:
        decoder = codecs.getincrementaldecoder(name)()
        try:
            with open(path, "rb") as handle:
                while chunk := handle.read(chunk_size):
                    decoder.decode(chunk)
                decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            continue
        return name
    return "utf-8"  # practically unreachable: GB18030 accepts almost any byte sequence


def decode_quoted_printable(text: str, charset: str | None) -> str:
    """Decode a quoted-printable value (soft line breaks already joined) using its CHARSET."""
    data = quopri.decodestring(text.encode("utf-8"))
    if charset:
        try:
            return data.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    return decode_bytes(data)
