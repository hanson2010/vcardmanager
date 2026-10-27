"""Rules about text, parameters and property order."""

import re
import unicodedata

from vcardmanager.clean.base import Context, is_text
from vcardmanager.keys import email_key, phone_key
from vcardmanager.model import STRUCTURED, Property, VCard

_INVISIBLE = re.compile("[\u200b-\u200d\u2060\ufeff]")  # zero-width spaces and joiners, word joiner, BOM
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TYPE_TOKEN = re.compile(r"^[A-Za-z0-9-]+$")
_TYPE_ALIASES = {"MOBILE": "CELL", "IPHONE": "CELL"}

# written in this order; anything else follows, X- properties last
PROPERTY_ORDER = [
    "KIND", "FN", "N", "NICKNAME", "PHOTO", "BDAY", "ANNIVERSARY", "GENDER", "ORG", "TITLE", "ROLE",
    "TEL", "EMAIL", "IMPP", "ADR", "LABEL", "URL", "NOTE", "CATEGORIES", "MEMBER", "RELATED",
    "UID", "REV", "PRODID",
]  # fmt: skip
_RANK = {name: i for i, name in enumerate(PROPERTY_ORDER)}


def fix_encoding(card: VCard, ctx: Context) -> None:
    """Remove invisible and control characters and normalize Unicode to NFC."""
    for prop in card.properties:
        if is_text(prop):
            prop.raw = unicodedata.normalize("NFC", _CONTROL.sub("", _INVISIBLE.sub("", prop.raw)))


def upgrade_version(card: VCard, ctx: Context) -> None:
    """Remove what 2.1 leaves behind that 3.0/4.0 cannot express, such as `TYPE=#1`.

    The 2.1 syntax itself (bare parameters, quoted-printable, CHARSET) is converted when the file is read.
    """
    for prop in card.properties:
        types = prop.params.get("TYPE")
        if types is None:
            continue
        bad = [t for t in types if not _TYPE_TOKEN.match(t)]
        if bad:
            card.warnings.append(f"{prop.name}: removed invalid TYPE {', '.join(bad)}")
            prop.params["TYPE"] = [t for t in types if t not in bad]
        if not prop.params["TYPE"]:
            del prop.params["TYPE"]


def _trim(text: str) -> str:
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")).strip()


def trim_whitespace(card: VCard, ctx: Context) -> None:
    """Strip leading/trailing whitespace and collapse runs of spaces in every line of a value."""
    for prop in card.properties:
        if not is_text(prop):
            continue
        if prop.name in STRUCTURED:
            components = prop.components
            trimmed = [_trim(c) for c in components]
            if trimmed != components:
                prop.components = trimmed
        elif (trimmed_value := _trim(prop.value)) != prop.value:
            prop.value = trimmed_value


def _is_empty(prop: Property) -> bool:
    if not is_text(prop):
        return not prop.raw.strip()
    if prop.name in STRUCTURED:
        return not any(c.strip() for c in prop.components)
    return not prop.value.strip()


def drop_empty(card: VCard, ctx: Context) -> None:
    """Remove properties with empty values. An empty FN is rebuilt by `name-fill`."""
    card.properties = [p for p in card.properties if not _is_empty(p)]


def type_normalize(card: VCard, ctx: Context) -> None:
    """Uppercase TYPE values, remove repeats, and map vendor labels (`mobile`, `iPhone`) to CELL."""
    for prop in card.properties:
        types = prop.params.get("TYPE")
        if types is None:
            continue
        normalized: list[str] = []
        for value in types:
            for part in value.split(","):
                upper = _TYPE_ALIASES.get(part.strip().upper(), part.strip().upper())
                if upper and upper not in normalized:
                    normalized.append(upper)
        if normalized:
            prop.params["TYPE"] = normalized
        else:
            del prop.params["TYPE"]


def _value_key(prop: Property, region: str | None) -> str | None:
    """What makes two values equal: the same keys `dedupe` matches on, so both agree on what is a repeat."""
    value = prop.value
    if prop.name == "TEL":  # short numbers have no phone key: compare their digits
        return phone_key(value, region) or re.sub(r"\D", "", value)
    if prop.name == "EMAIL":
        return email_key(value) or value.strip().lower()
    if prop.name == "URL":
        return value.strip()
    if prop.name == "IMPP":
        return value.strip().lower()
    return None


def dedupe_values(card: VCard, ctx: Context) -> None:
    """Remove repeated phones, emails, URLs and IMPP handles; the first one gets the others' TYPE values.

    A removed property's labels (such as `item1.X-ABLABEL`) go with it when nothing else uses them.
    """
    seen: dict[tuple[str, str], Property] = {}
    kept: list[Property] = []
    dropped_groups: set[str] = set()
    for prop in card.properties:
        value_key = _value_key(prop, ctx.region)
        if value_key is not None:
            key = (prop.name, value_key)
            first = seen.get(key)
            if first is not None:
                first_types = first.params.setdefault("TYPE", [])
                upper = {t.upper() for t in first_types}
                first_types.extend(t for t in prop.params.get("TYPE", []) if t.upper() not in upper)
                if not first_types:
                    del first.params["TYPE"]
                if prop.group:
                    dropped_groups.add(prop.group)
                continue
            seen[key] = prop
        kept.append(prop)
    alive = {p.group for p in kept if p.group and not p.name.startswith("X-")}
    card.properties = [p for p in kept if not (p.group in dropped_groups and p.group not in alive)]


def property_rank(name: str) -> int:
    """Position of a property name in the order `sort-properties` writes."""
    if name in _RANK:
        return _RANK[name]
    return len(PROPERTY_ORDER) + (1 if name.startswith("X-") else 0)


def sort_properties(card: VCard, ctx: Context) -> None:
    """Sort properties into a fixed order. Grouped properties stay together, after their main property."""
    anchors: dict[str, tuple[int, int]] = {}
    for index, prop in enumerate(card.properties):
        if prop.group and not prop.name.startswith("X-"):
            anchors.setdefault(prop.group, (property_rank(prop.name), index))

    def key(item: tuple[int, Property]) -> tuple[int, int, bool, int]:
        index, prop = item
        rank, anchor = anchors.get(prop.group or "", (property_rank(prop.name), index))
        return rank, anchor, prop.name.startswith("X-"), index  # within a group, labels follow the main property

    card.properties = [prop for _, prop in sorted(enumerate(card.properties), key=key)]
