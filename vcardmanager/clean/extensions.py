"""Rules about vendor extensions and group cards."""

from vcardmanager import groups
from vcardmanager.clean.base import Context
from vcardmanager.model import VCard


def groups_to_categories(cards: list[VCard], ctx: Context) -> list[VCard]:
    """Copy group membership from group cards into each member's CATEGORIES, then remove the group cards."""
    return groups.groups_to_categories(cards)


def drop_extensions(card: VCard, ctx: Context) -> None:
    """Remove all `X-` properties."""
    card.properties = [p for p in card.properties if not p.name.startswith("X-")]
