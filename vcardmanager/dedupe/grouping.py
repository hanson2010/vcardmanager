"""Find groups of cards that describe the same person."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from rapidfuzz.fuzz import token_sort_ratio

from vcardmanager.keys import email_key, name_key, phone_key
from vcardmanager.model import VCard

MATCH_KINDS = ("phone", "email", "name")


@dataclass
class DuplicateGroup:
    cards: list[VCard]
    reasons: set[str] = field(default_factory=set)  # which kinds of key linked the cards


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def find_duplicate_groups(
    cards: Sequence[VCard],
    match: Iterable[str] = MATCH_KINDS,
    *,
    name_threshold: int = 100,
    min_name_length: int = 5,
    ignore_phones: Iterable[str] = (),
    region: str | None = None,
) -> list[DuplicateGroup]:
    """Groups of two or more cards linked by a shared key, in the order of their first card.

    Links are transitive: if A matches B and B matches C, all three are one group. Group cards never match.
    With `name_threshold` below 100, names whose token-sorted similarity reaches it also match, if both
    are at least `min_name_length` characters and start with the same character.
    """
    kinds = set(match)
    if unknown := kinds - set(MATCH_KINDS):
        raise ValueError(f"unknown match kind(s): {', '.join(sorted(unknown))}; use {', '.join(MATCH_KINDS)}")
    ignored = {ignored_key for phone in ignore_phones if (ignored_key := phone_key(phone, region))}
    contacts = [i for i, card in enumerate(cards) if card.kind != "group"]

    owners: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i in contacts:
        keys: set[tuple[str, str]] = set()
        if "phone" in kinds:
            keys |= {("phone", k) for p in cards[i].get_all("TEL") if (k := phone_key(p.value, region))}
            keys -= {("phone", k) for k in ignored}
        if "email" in kinds:
            keys |= {("email", k) for p in cards[i].get_all("EMAIL") if (k := email_key(p.value))}
        if "name" in kinds and (k := name_key(cards[i])):
            keys.add(("name", k))
        for key in keys:
            owners[key].append(i)

    union = _UnionFind(len(cards))
    links: list[tuple[int, int, str]] = []
    for (kind, _), indexes in owners.items():
        for other in indexes[1:]:
            union.union(indexes[0], other)
            links.append((indexes[0], other, kind))
    if "name" in kinds and name_threshold < 100:
        links += _fuzzy_name_links(cards, contacts, union, name_threshold, min_name_length)

    members: dict[int, list[int]] = defaultdict(list)
    for i in contacts:
        members[union.find(i)].append(i)
    reasons: dict[int, set[str]] = defaultdict(set)
    for a, _, kind in links:
        reasons[union.find(a)].add(kind)
    return [
        DuplicateGroup([cards[i] for i in indexes], reasons[root])
        for root, indexes in sorted(members.items(), key=lambda item: item[1][0])
        if len(indexes) > 1
    ]


def _fuzzy_name_links(
    cards: Sequence[VCard], contacts: list[int], union: _UnionFind, threshold: int, min_length: int
) -> list[tuple[int, int, str]]:
    blocks: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i in contacts:
        key = name_key(cards[i])
        if key and len(key) >= min_length:
            blocks[key[0]].append((i, key))  # compare only names that start with the same character
    links = []
    for block in blocks.values():
        for n, (a, key_a) in enumerate(block):
            for b, key_b in block[n + 1 :]:
                if key_a != key_b and token_sort_ratio(key_a, key_b) >= threshold:
                    union.union(a, b)
                    links.append((a, b, "similar name"))
    return links


def find_duplicates(
    cards: Sequence[VCard],
    match: Iterable[str] = MATCH_KINDS,
    *,
    name_threshold: int = 100,
    min_name_length: int = 5,
    ignore_phones: Iterable[str] = (),
    region: str | None = None,
) -> list[list[VCard]]:
    """Like `find_duplicate_groups`, returning just the cards of each group."""
    groups = find_duplicate_groups(
        cards,
        match,
        name_threshold=name_threshold,
        min_name_length=min_name_length,
        ignore_phones=ignore_phones,
        region=region,
    )
    return [group.cards for group in groups]
