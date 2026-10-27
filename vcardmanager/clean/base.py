"""Shared types for clean rules."""

from collections.abc import Callable
from dataclasses import dataclass, field

from vcardmanager.model import VERBATIM, Property, VCard

Fetch = Callable[[str], bytes]


@dataclass
class Context:
    region: str | None = None  # ISO country code assumed for phone numbers without one
    phone_format: str = "e164"  # e164 | international | national
    fetch: Fetch | None = None  # downloads a photo; None means urllib
    photo_timeout: float = 10.0
    photo_max_px: int = 512  # longest side of embedded photos (photo-compression)
    photo_quality: int = 85  # JPEG quality of compressed photos
    photo_cache: dict[str, bytes | Exception] = field(default_factory=dict)
    log: Callable[[str], None] | None = None  # progress messages, e.g. one line per photo download


CardRule = Callable[[VCard, Context], None]  # changes the card in place
CardsRule = Callable[[list[VCard], Context], list[VCard]]


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    default: bool
    card: CardRule | None = None
    cards: CardsRule | None = None
    requires: tuple[str, ...] = ()


def is_text(prop: Property) -> bool:
    """True for properties holding text, not a URI or binary data."""
    return prop.name not in VERBATIM and "ENCODING" not in prop.params


def copy_card(card: VCard) -> VCard:
    return VCard([p.copy() for p in card.properties], card.source_version, card.source, list(card.warnings))
