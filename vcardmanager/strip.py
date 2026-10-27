"""Remove properties by name pattern, optionally restricted by parameter values."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from vcardmanager.groups import GROUP_MARKERS
from vcardmanager.model import Property, VCard

# a vCard without these is invalid; BEGIN, END and VERSION are written by the writer
REQUIRED = frozenset({"FN"})

PRESETS: dict[str, dict[str, list[str]]] = {
    "minimal": {"keep": ["FN", "N", "TEL", "EMAIL"]},
    "privacy": {"fields": ["PHOTO", "NOTE", "BDAY", "GEO", "ADR", "X-*"]},
}

_PATTERN = re.compile(r"^(?P<name>[^\[\]]+)(?P<conditions>(?:\[[^\[\]=]+=[^\[\]]*\])*)$")
_CONDITION = re.compile(r"\[(?P<key>[^\[\]=]+)=(?P<value>[^\[\]]*)\]")


@dataclass(frozen=True)
class FieldPattern:
    """`TEL`, `X-*`, or `TEL[TYPE=FAX]`: a name glob plus parameter conditions that must all match."""

    name: str
    conditions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, text: str) -> "FieldPattern":
        match = _PATTERN.match(text.strip())
        if not match:
            raise ValueError(f"not a field pattern: {text!r} (use NAME, X-*, or NAME[PARAM=VALUE])")
        conditions = tuple(
            (m["key"].strip().upper(), m["value"].strip().upper()) for m in _CONDITION.finditer(match["conditions"])
        )
        return cls(match["name"].strip().upper(), conditions)

    def matches(self, prop: Property) -> bool:
        if not fnmatchcase(prop.name, self.name):
            return False
        return all(
            any(fnmatchcase(value.upper(), pattern) for value in prop.params.get(key, []))
            for key, pattern in self.conditions
        )


def parse_patterns(texts: Iterable[str]) -> list[FieldPattern]:
    """Parse patterns; each text may hold several, separated by commas."""
    return [FieldPattern.parse(part) for text in texts for part in text.split(",") if part.strip()]


def _would_remove(name: str, fields: list[FieldPattern], keep: list[FieldPattern] | None) -> bool:
    prop = Property(name, "")
    if keep is not None and not any(p.matches(prop) for p in keep):
        return True
    return any(p.matches(prop) for p in fields)


def needs_group_conversion(fields: Iterable[FieldPattern], keep: Iterable[FieldPattern] | None) -> bool:
    """True if stripping would remove what defines groups while keeping CATEGORIES.

    Then membership should be copied into CATEGORIES first (`groups.groups_to_categories`) so it survives.
    When CATEGORIES is stripped too, there is nowhere to keep it.
    """
    fields, keep = list(fields), list(keep) if keep is not None else None
    if _would_remove("CATEGORIES", fields, keep):
        return False
    return any(_would_remove(name, fields, keep) for name in GROUP_MARKERS)


def strip_card(
    card: VCard, fields: Iterable[FieldPattern] = (), keep: Iterable[FieldPattern] | None = None
) -> tuple[VCard, int]:
    """Return a copy of `card` without the matching properties, and how many were removed.

    With `keep`, only properties matching one of its patterns stay; `fields` is then applied on top.
    `FN` is always kept.
    """
    fields, keep = list(fields), list(keep) if keep is not None else None
    kept = [
        prop
        for prop in card.properties
        if prop.name in REQUIRED
        or ((keep is None or any(p.matches(prop) for p in keep)) and not any(p.matches(prop) for p in fields))
    ]
    stripped = VCard(kept, card.source_version, card.source, list(card.warnings))
    drop_orphaned_groups(card, stripped)
    return stripped, len(card.properties) - len(kept)


def drop_orphaned_groups(before: VCard, after: VCard) -> None:
    """Clear a property-group prefix (`item1.`) that no longer links anything.

    A prefix exists to tie properties together, like `item1.EMAIL` with `item1.X-ABLABEL`. When removing
    properties leaves a prefix on a single property, the prefix is dropped.
    """
    was = Counter(p.group for p in before.properties if p.group)
    now = Counter(p.group for p in after.properties if p.group)
    for index, prop in enumerate(after.properties):
        if prop.group and now[prop.group] == 1 and was[prop.group] > 1:
            orphan = prop.copy()  # `after` may share properties with `before`: do not change them in place
            orphan.group = None
            after.properties[index] = orphan
