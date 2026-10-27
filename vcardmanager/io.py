"""Input collection, safe output and filenames."""

import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import BinaryIO

from vcardmanager.encoding import decode_bytes
from vcardmanager.model import VCard
from vcardmanager.parser import iter_vcards, parse_text
from vcardmanager.writer import iter_dumps, write_vcards

STDIO = "-"
VCARD_SUFFIXES = (".vcf", ".vcard")

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class OutputError(Exception):
    """Writing would overwrite something the user did not allow."""


def collect_inputs(inputs: Iterable[str], recursive: bool = False) -> list[str]:
    """Expand directories to the vCard files inside them, sorted by path. `-` means stdin."""
    files: list[str] = []
    for item in inputs:
        if item == STDIO:
            files.append(item)
            continue
        path = Path(item)
        if path.is_dir():
            found = path.rglob("*") if recursive else path.glob("*")
            files.extend(str(p) for p in sorted(found) if p.is_file() and p.suffix.lower() in VCARD_SUFFIXES)
        elif path.is_file():
            files.append(item)
        else:
            raise FileNotFoundError(f"no such file or directory: {item}")
    return files


def iter_inputs(inputs: Iterable[str], recursive: bool = False, encoding: str | None = None) -> Iterator[VCard]:
    """Yield the cards of every input in order. Files are streamed; stdin is read whole."""
    for item in collect_inputs(inputs, recursive):
        if item == STDIO:
            yield from parse_text(decode_bytes(sys.stdin.buffer.read(), encoding), source="<stdin>")
        else:
            yield from iter_vcards(item, encoding)


def read_inputs(inputs: Iterable[str], recursive: bool = False, encoding: str | None = None) -> list[VCard]:
    return list(iter_inputs(inputs, recursive, encoding))


def default_output(inputs: Sequence[str], suffix: str) -> str:
    """A new file named after the first input, e.g. `contacts.vcf` → `contacts_cleaned.vcf`.

    It is placed next to that input (stdin: the current directory) and numbered (`_2`, `_3`…) so that it never
    replaces an existing file.
    """
    first = inputs[0] if inputs else STDIO
    base = Path("stdin") if first == STDIO else Path(first)
    name = base.name if base.is_dir() else base.stem
    candidate, n = base.with_name(f"{name}_{suffix}.vcf"), 2
    while candidate.exists():
        candidate, n = base.with_name(f"{name}_{suffix}_{n}.vcf"), n + 1
    return str(candidate)


def check_output(path: str, inputs: Iterable[str] = (), force: bool = False) -> None:
    """Refuse to overwrite an input or an existing file unless `force` is set."""
    if path == STDIO or force:
        return
    target = Path(path).resolve()
    if any(item != STDIO and Path(item).resolve() == target for item in inputs):
        raise OutputError(f"{path} is also an input; pass --force to overwrite it")
    if target.exists():
        raise OutputError(f"{path} already exists; pass --force to overwrite it")


LINE_ENDINGS = {"crlf": "\r\n", "lf": "\n"}


def write_output(
    cards: Iterable[VCard], path: str, version: str, line_ending: str = "crlf", stdout: BinaryIO | None = None
) -> None:
    """Write cards one at a time to a file (replaced only when complete) or to stdout."""
    if path != STDIO:
        write_vcards(cards, path, version, LINE_ENDINGS[line_ending])
        return
    stream = stdout or sys.stdout.buffer
    for chunk in iter_dumps(cards, version, LINE_ENDINGS[line_ending]):
        stream.write(chunk.encode("utf-8"))


def safe_filename(name: str, replacement: str = "_", max_length: int = 100) -> str:
    """Make a filename stem that is valid on Windows, macOS and Linux."""
    name = _INVALID_FILENAME_CHARS.sub(replacement, name).strip()
    if replacement:
        name = re.sub(f"{re.escape(replacement)}{{2,}}", replacement, name)
    name = name[:max_length].rstrip(". ")
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = replacement + name
    return name or "unnamed"


def unique_name(stem: str, used: set[str]) -> str:
    """Add `_2`, `_3`… until the stem is unused. Comparison ignores case, as Windows and macOS do."""
    candidate, n = stem, 2
    while candidate.lower() in used:
        candidate, n = f"{stem}_{n}", n + 1
    used.add(candidate.lower())
    return candidate
