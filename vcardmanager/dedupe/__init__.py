"""`dedupe`: find cards that describe the same person and merge them."""

from collections.abc import Iterable, Sequence

from vcardmanager.dedupe.grouping import (
    DEFAULT_MATCH,
    MATCH_KINDS,
    DuplicateGroup,
    find_duplicate_groups,
    find_duplicates,
)
from vcardmanager.dedupe.merging import merge_group
from vcardmanager.model import VCard


def apply_merges(cards: Sequence[VCard], groups: Iterable[Sequence[VCard]]) -> list[VCard]:
    """Replace each group with its merged card, placed where the group's first card was."""
    merged_at: dict[int, VCard] = {}
    dropped: set[int] = set()
    for group in groups:
        ids = [id(card) for card in group]
        merged_at[ids[0]] = merge_group(group)
        dropped.update(ids[1:])
    return [merged_at.get(id(card), card) for card in cards if id(card) not in dropped]


__all__ = [
    "DEFAULT_MATCH",
    "MATCH_KINDS",
    "DuplicateGroup",
    "apply_merges",
    "find_duplicate_groups",
    "find_duplicates",
    "merge_group",
]
