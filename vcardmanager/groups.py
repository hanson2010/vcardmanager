"""Contact groups in every format vCard files use, and moving membership into CATEGORIES."""

import re
from collections.abc import Iterable

from vcardmanager.model import Property, VCard

# properties that define group membership, in every format `group_members` reads
GROUP_MARKERS = (
    "KIND", "MEMBER", "X-ADDRESSBOOKSERVER-KIND", "X-ADDRESSBOOKSERVER-MEMBER", "X-TYPE", "X-MEMBERS", "GROUPING",
)  # fmt: skip

_URN_UUID = re.compile(r"^urn:uuid:", re.IGNORECASE)


def group_members(cards: Iterable[VCard]) -> dict[str, list[VCard]]:
    """Map each group name to its member contacts, reading every known group format.

    - `CATEGORIES` on the contact (Google, Android)
    - group cards (`KIND:group`, iCloud `X-ADDRESSBOOKSERVER-KIND:group`, legacy `X-TYPE:GROUP`) that list
      members by UID in `MEMBER`, `X-ADDRESSBOOKSERVER-MEMBER` or `X-MEMBERS`
    - the legacy `GROUPING:<group uid>` back-reference on the contact

    A contact is a member if any source says so. Groups with the same name are one group.
    UIDs are matched within the file the group card came from.
    """
    cards = list(cards)
    groups: dict[str, list[VCard]] = {}

    def add(name: str, card: VCard) -> None:
        members = groups.setdefault(name, [])
        if not any(member is card for member in members):
            members.append(card)

    contacts = [c for c in cards if c.kind != "group"]
    by_uid = {(_file(c), uid): c for c in contacts if (uid := _uid(c))}
    group_by_uid: dict[tuple[str, str], str] = {}
    for group in (c for c in cards if c.kind == "group"):
        name = group_name(group)
        groups.setdefault(name, [])
        if uid := _uid(group):
            group_by_uid[(_file(group), uid)] = name
        for member_uid in _member_uids(group):
            if (member := by_uid.get((_file(group), member_uid))) is not None:
                add(name, member)
    for card in contacts:
        for prop in card.get_all("CATEGORIES"):
            for category in prop.components:
                if category.strip():
                    add(category.strip(), card)
        for prop in card.get_all("GROUPING"):
            if (linked := group_by_uid.get((_file(card), _normalize_uid(prop.value)))) is not None:
                add(linked, card)
    return groups


def group_name(group: VCard) -> str:
    """The name of a group card: its FN, else its N, else its UID."""
    fn = group.get("FN")
    if fn is not None and fn.value.strip():
        return fn.value.strip()
    n = group.get("N")
    if n is not None and (name := " ".join(part for part in n.components if part.strip())):
        return name
    return _uid(group) or "unnamed-group"


def groups_to_categories(cards: list[VCard]) -> list[VCard]:
    """Copy group membership into each member's CATEGORIES, then drop the group cards and GROUPING links.

    Changes the cards in place and returns the contacts that remain.
    """
    for name, members in group_members(cards).items():
        for card in members:
            categories = card.get("CATEGORIES")
            if categories is None:
                categories = Property("CATEGORIES", "")
                card.add(categories)
            current = [c for c in categories.components if c]
            if name not in current:
                categories.components = [*current, name]
    contacts = [card for card in cards if card.kind != "group"]
    for card in contacts:
        card.properties = [p for p in card.properties if p.name != "GROUPING"]
    return contacts


def _member_uids(group: VCard) -> list[str]:
    uids = [_normalize_uid(p.value) for name in ("MEMBER", "X-ADDRESSBOOKSERVER-MEMBER") for p in group.get_all(name)]
    for prop in group.get_all("X-MEMBERS"):
        uids.extend(_normalize_uid(uid) for uid in re.split(r"[;,]", prop.value) if uid.strip())
    return uids


def _uid(card: VCard) -> str:
    prop = card.get("UID")
    return _normalize_uid(prop.value) if prop is not None else ""


def _normalize_uid(value: str) -> str:
    return _URN_UUID.sub("", value.strip()).lower()


def _file(card: VCard) -> str:
    return card.source.rsplit(":", 1)[0] if card.source else ""
