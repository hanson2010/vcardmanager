"""Split cards into several files: by count, by size, by group or by a property."""

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from vcardmanager.groups import group_members, group_name
from vcardmanager.io import LINE_ENDINGS, OutputError, safe_filename, unique_name
from vcardmanager.model import VCard
from vcardmanager.writer import dumps

UNGROUPED = "ungrouped"

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMG]?)I?B?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


def parse_size(text: str) -> int:
    """`"1MB"` → 1048576. Accepts B, KB, MB, GB (powers of 1024) and plain byte counts."""
    match = _SIZE.match(text)
    if not match:
        raise ValueError(f"not a size: {text!r} (use e.g. 500KB or 1MB)")
    return int(float(match[1]) * _SIZE_UNITS[match[2].upper()])


def split_cards(
    cards: Sequence[VCard],
    *,
    per_file: int | None = None,
    max_bytes: int | None = None,
    by: str | None = None,
    version: str = "3.0",
) -> list[tuple[str, list[VCard]]]:
    """Return `(filename stem, cards)` chunks. Exactly one of `per_file`, `max_bytes`, `by` must be given."""
    if sum(option is not None for option in (per_file, max_bytes, by)) != 1:
        raise ValueError("give exactly one of per_file, max_bytes, by")
    used: set[str] = set()
    if by is not None:
        chunks = _split_by(cards, by)
    elif per_file == 1:
        chunks = [(_card_name(card), [card]) for card in cards]
    elif per_file is not None:
        if per_file < 1:
            raise ValueError("per_file must be at least 1")
        chunks = [("", list(cards[i : i + per_file])) for i in range(0, len(cards), per_file)]
    else:
        assert max_bytes is not None
        chunks = [("", chunk) for chunk in _split_by_size(cards, max_bytes, version)]
    width = len(str(len(chunks)))
    return [
        (unique_name(safe_filename(name) if name else f"contacts-{i:0{width}d}", used), chunk)
        for i, (name, chunk) in enumerate(chunks, 1)
    ]


def write_split(
    chunks: Iterable[tuple[str, list[VCard]]],
    directory: str | Path,
    version: str = "3.0",
    force: bool = False,
    line_ending: str = "crlf",
) -> list[Path]:
    """Write each chunk to `<directory>/<stem>.vcf`. Existing files are only replaced with `force`."""
    chunks = list(chunks)
    directory = Path(directory)
    paths = [directory / f"{stem}.vcf" for stem, _ in chunks]
    if not force:
        existing = [p for p in paths if p.exists()]
        if existing:
            raise OutputError(f"{existing[0]} already exists; pass --force to overwrite")
    directory.mkdir(parents=True, exist_ok=True)
    for path, (_, chunk) in zip(paths, chunks, strict=True):
        path.write_bytes(dumps(chunk, version, LINE_ENDINGS[line_ending]).encode("utf-8"))
    return paths


def _split_by(cards: Sequence[VCard], by: str) -> list[tuple[str, list[VCard]]]:
    if by.lower() == "group":
        groups = group_members(cards)
        definitions: dict[str, list[VCard]] = {}
        for card in cards:
            if card.kind == "group":
                definitions.setdefault(group_name(card), []).append(card)
        grouped = {id(card) for members in groups.values() for card in members}
        ungrouped = [c for c in cards if c.kind != "group" and id(c) not in grouped]
        # each group's file starts with its group card(s), so membership survives a later merge
        chunks = [(name, definitions.get(name, []) + members) for name, members in groups.items()]
        return [chunk for chunk in chunks if chunk[1]] + ([(UNGROUPED, ungrouped)] if ungrouped else [])
    keyed: dict[str, list[VCard]] = {}
    for card in cards:
        prop = card.get(by)
        key = (prop.components[0] if prop is not None else "").strip() or f"no-{by.lower()}"
        keyed.setdefault(key, []).append(card)
    return list(keyed.items())


def _split_by_size(cards: Sequence[VCard], max_bytes: int, version: str) -> list[list[VCard]]:
    chunks: list[list[VCard]] = []
    current: list[VCard] = []
    size = 0
    for card in cards:
        card_size = len(dumps([card], version).encode("utf-8"))
        if current and size + card_size > max_bytes:
            chunks.append(current)
            current, size = [], 0
        current.append(card)  # a card larger than the limit gets a file of its own
        size += card_size
    if current:
        chunks.append(current)
    return chunks


def _card_name(card: VCard) -> str:
    for name in ("FN", "UID"):
        prop = card.get(name)
        if prop is not None and prop.value.strip():
            return prop.value.strip()
    return "unnamed"
