"""Merge a group of duplicate cards into one."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from vcardmanager.clean.base import Context, copy_card
from vcardmanager.clean.names import has_han, is_personal_name, name_label, name_split
from vcardmanager.clean.structure import property_rank
from vcardmanager.keys import email_key, phone_key
from vcardmanager.model import Property, VCard

# at most one per card; the most complete card's value wins, the others are dropped
SINGLE_VALUED = ("FN", "N", "BDAY", "ANNIVERSARY", "GENDER", "KIND")
# bookkeeping: the merged card keeps the base card's UID and PRODID and gets a new REV
_META = {"UID", "REV", "PRODID"}

_VALUE_KEYS: dict[str, Callable[[str], str | None]] = {"TEL": phone_key, "EMAIL": email_key}


def _completeness(card: VCard) -> tuple[int, int]:
    """More distinct properties wins; a longer FN breaks ties (a real name beats an email handle)."""
    names = {p.name for p in card.properties if p.name not in _META and not p.name.startswith("X-")}
    fn = card.get("FN")
    return len(names), len(fn.value) if fn else 0


def _value_key(prop: Property) -> tuple[str, str]:
    key_of = _VALUE_KEYS.get(prop.name)
    key = key_of(prop.value) if key_of else None
    return prop.name, key if key is not None else prop.raw.strip().lower()


def merge_group(cards: Sequence[VCard]) -> VCard:
    """Merge duplicates into a copy of the most complete card.

    FN is the best name in the group and N is rebuilt from it (see `_set_best_name`). Multi-valued properties
    (TEL, EMAIL, ADR, URL, NOTE, X-…) are combined without repeats. For BDAY and other single-valued properties
    the base card's value wins; another card's value is used only where the base has none. CATEGORIES are
    combined. The base card's UID is kept and REV is renewed.
    """
    if not cards:
        raise ValueError("nothing to merge")
    order = sorted(range(len(cards)), key=lambda i: _completeness(cards[i]), reverse=True)
    base = copy_card(cards[order[0]])
    _set_best_name(base, cards, cards[order[0]])
    seen = {_value_key(p): p for p in base.properties}
    used_groups = {p.group for p in base.properties if p.group}

    for index in order[1:]:
        other = cards[index]
        renamed: dict[str, str] = {}
        for prop in other.properties:
            if prop.name in _META:
                continue
            if prop.name == "CATEGORIES":
                _merge_categories(base, prop)
                continue
            if prop.name in SINGLE_VALUED:
                if base.get(prop.name) is None:
                    _insert(base, _copy(prop, None))
                continue
            key = _value_key(prop)
            if (existing := seen.get(key)) is not None:
                _add_types(existing, prop)  # e.g. the duplicate number was marked PREF
                continue
            group = None
            if prop.group:  # keep grouped properties together, without clashing with the base card's groups
                if prop.group not in renamed:
                    renamed[prop.group] = _free_group(used_groups)
                group = renamed[prop.group]
            seen[key] = _insert(base, _copy(prop, group))
        base.warnings.extend(w for w in other.warnings if w not in base.warnings)

    rev = Property("REV", datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    old_revs = [i for i, p in enumerate(base.properties) if p.name == "REV"]
    if old_revs:
        base.properties[old_revs[0]] = rev
    else:
        base.add(rev)
    return base


def _split_label(fn: str) -> tuple[str, str]:
    """`宋平国/CS` → (`宋平国`, `/CS`)."""
    slash = fn.find("/")
    return (fn[:slash].strip(), fn[slash:].strip()) if slash > 0 else (fn.strip(), "")


def _squash(name: str) -> str:
    return "".join(name.split()).casefold()


def _is_real_name(name: str, card: VCard) -> bool:
    """Not empty, not an email address or phone number, and not just the card's email handle."""
    if not name or "@" in name or not any(ch.isalpha() for ch in name):
        return False
    handles = {e.value.split("@")[0].strip().casefold() for e in card.get_all("EMAIL") if "@" in e.value}
    return name.casefold() not in handles


def _richness(name: str) -> int:
    """3: Chinese and English, 2: Chinese only, 1: English with two or more words, 0: a single word."""
    han, latin = has_han(name), any(ch.isascii() and ch.isalpha() for ch in name)
    if han:
        return 3 if latin else 2
    return 1 if len(name.split()) >= 2 else 0


def _set_best_name(merged: VCard, cards: Sequence[VCard], base: VCard) -> None:
    """Give the merged card the best FN in the group, and an N built from it.

    The name: one that contains another (`Joy 杨伟云` over `杨伟云`), then the richest (Chinese and English over
    Chinese over a full English name over one word), then the one on most cards, then the first. The label
    after a slash is the base card's, or else the first one in the group. Email handles, addresses and phone
    numbers never win. If no card has a real name, the base card's FN and N are kept.
    """
    candidates = []  # (position, name, label, card)
    for position, card in enumerate(cards):
        fn = card.get("FN")
        if fn is not None:
            name, label = _split_label(fn.value)
            if _is_real_name(name, card):
                candidates.append((position, name, label, card))
    if not candidates:
        return
    squashed = [_squash(name) for _, name, _, _ in candidates]
    count = {s: squashed.count(s) for s in squashed}

    def rank(index: int) -> tuple[bool, int, int, int]:
        position, name, _, _ = candidates[index]
        contained = any(s != squashed[index] and squashed[index] in s for s in squashed)
        return not contained, _richness(name), count[squashed[index]], -position

    best = max(range(len(candidates)), key=rank)
    _, name, _, source = candidates[best]
    base_fn = base.get("FN")
    base_label = _split_label(base_fn.value)[1] if base_fn is not None else ""
    label = base_label or next((label for _, _, label, _ in candidates if label), "")

    fn_value = name + label
    fn = merged.get("FN")
    if fn is None:
        fn = Property.from_value("FN", fn_value)
        merged.properties.insert(0, fn)
    else:
        fn.value = fn_value
    n = merged.get("N")
    if n is None and not any(card.get("N") for card in cards):
        return  # no card had an N: adding one is `clean`'s job (name-fill)
    if is_personal_name(name):
        rebuilt = VCard([Property.from_value("FN", fn_value), Property("N", "")])
        rebuilt.properties[1].components = ["", fn_value, "", "", ""]
        name_label(rebuilt, Context())
        name_split(rebuilt, Context())
        fn.value = rebuilt.properties[0].value  # name-split may reorder FN, e.g. `Bob 张三` -> `张三 Bob`
        new_n = rebuilt.properties[1]
    else:  # not a person's name (e.g. an organisation): keep the N that came with this FN
        new_n = source.get("N") or (n if n is not None else Property("N", ""))
    if n is None:
        _insert(merged, _copy(new_n, None))
    else:
        n.raw = new_n.raw


def _copy(prop: Property, group: str | None) -> Property:
    copied = prop.copy()
    copied.group = group
    return copied


def _insert(card: VCard, prop: Property) -> Property:
    """Insert after the card's last property of the same name; otherwise before the first one that
    `sort-properties` would put after it. Keeps the merged card readable without re-sorting it."""
    same = [i for i, p in enumerate(card.properties) if p.name == prop.name]
    if same:
        index = same[-1] + 1
    else:
        rank = property_rank(prop.name)
        index = next((i for i, p in enumerate(card.properties) if property_rank(p.name) > rank), len(card.properties))
    card.properties.insert(index, prop)
    return prop


def _add_types(target: Property, other: Property) -> None:
    types = target.params.get("TYPE", [])
    upper = {t.upper() for t in types}
    extra = [t for t in other.params.get("TYPE", []) if t.upper() not in upper]
    if extra:
        target.params["TYPE"] = types + extra


def _merge_categories(base: VCard, prop: Property) -> None:
    categories = base.get("CATEGORIES")
    if categories is None:
        base.add(_copy(prop, None))
        return
    current = [c for c in categories.components if c]
    added = [c for c in prop.components if c and c not in current]
    if added:
        categories.components = current + added


def _free_group(used: set[str]) -> str:
    n = 1
    while f"item{n}" in used:
        n += 1
    used.add(f"item{n}")
    return f"item{n}"
