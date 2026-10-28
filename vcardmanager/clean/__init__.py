"""`clean`: a registry of rules, applied in a fixed order."""

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from vcardmanager.clean.base import Context, Fetch, Rule, copy_card
from vcardmanager.clean.emails import email_lowercase
from vcardmanager.clean.extensions import drop_extensions, groups_to_categories
from vcardmanager.clean.names import name_case, name_fill, name_label, name_prefix, name_split
from vcardmanager.clean.phones import FORMATS, phone_format, phone_type
from vcardmanager.clean.photos import photo_compression, photo_embed
from vcardmanager.clean.structure import (
    dedupe_values,
    drop_empty,
    fix_encoding,
    fix_escapes,
    sort_properties,
    trim_whitespace,
    type_normalize,
    upgrade_version,
)
from vcardmanager.model import VCard
from vcardmanager.strip import drop_orphaned_groups

# grouped by topic; they run in this order
RULES = [
    # text
    Rule("fix-encoding", "Remove invisible and control characters, normalize Unicode", True, fix_encoding),
    Rule("fix-escapes", "Remove needless backslashes, such as \\: or Google's https\\\\://", True, fix_escapes),
    Rule("upgrade-version", "Remove 2.1 leftovers that 3.0/4.0 cannot express, such as TYPE=#1", True, upgrade_version),
    Rule("trim-whitespace", "Strip and collapse whitespace in values", True, trim_whitespace),
    Rule("drop-empty", "Remove properties with empty values", True, drop_empty),
    # extensions and groups
    Rule(
        "groups-to-categories",
        "Copy group-card membership into CATEGORIES and remove the group cards",
        False,
        cards=groups_to_categories,
    ),
    Rule(
        "drop-extensions",
        "Remove all X- properties (group membership is kept in CATEGORIES)",
        True,
        drop_extensions,
        requires=("groups-to-categories",),
    ),
    # emails and phones: normalized before repeats are compared
    Rule("email-lowercase", 'Lowercase emails, unwrap "Name" <addr>, drop placeholders', True, email_lowercase),
    Rule("phone-format", "Write valid numbers as E.164, international or national", True, phone_format),
    Rule("phone-type", "Correct CELL vs landline", True, phone_type),
    Rule("type-normalize", "Uppercase and deduplicate TYPE values, map vendor labels", True, type_normalize),
    Rule("dedupe-values", "Remove repeated phones, emails and URLs in a card", True, dedupe_values),
    # names: fill, then label, split and case
    Rule("name-fill", "Build a missing FN (from N, ORG, email or phone) and a missing N", True, name_fill),
    Rule("name-label", "Move a label after a slash into the N suffix", True, name_label),
    Rule("name-prefix", "AA-BB-…-ZZ: AA-BB-…- goes to the N prefix, ZZ to the family name", True, name_prefix),
    Rule("name-split", "Put family and given names in the right N fields", True, name_split),
    Rule("name-case", "Latin surnames in capitals (John SMITH); fix all-caps or all-lowercase names", True, name_case),
    # photos: download, then shrink
    Rule("photo-embed", "Download photo links and embed the images (uses the network, can be slow)", False, photo_embed),
    Rule("photo-compression", "Shrink embedded photos larger than --photo-max-px to JPEG", True, photo_compression),
    # layout
    Rule("sort-properties", "Write properties in a fixed, readable order", True, sort_properties),
]
RULES_BY_NAME = {rule.name: rule for rule in RULES}
DEFAULT_RULES = frozenset(rule.name for rule in RULES if rule.default)


def resolve(rules: Iterable[str]) -> list[Rule]:
    """The rules to run, with their dependencies, in execution order. Raises ValueError for unknown names."""
    names = set(rules)
    unknown = names - RULES_BY_NAME.keys()
    if unknown:
        raise ValueError(f"unknown rule(s): {', '.join(sorted(unknown))}")
    pending = list(names)
    while pending:
        for required in RULES_BY_NAME[pending.pop()].requires:
            if required not in names:
                names.add(required)
                pending.append(required)
    return [rule for rule in RULES if rule.name in names]


def make_context(
    *,
    region: str | None = None,
    phone_format: str = "e164",
    fetch: Fetch | None = None,
    photo_timeout: float = 10.0,
    photo_max_px: int = 512,
    photo_quality: int = 85,
    log: Callable[[str], None] | None = None,
) -> Context:
    """The settings the rules read. `clean`, `clean_cards` and `iter_clean` take the same keyword options.

    region: country for phone numbers without a country code (`CN`); phone_format: e164, international or
    national; fetch: downloads a photo URL (default: urllib); photo_max_px / photo_quality: size and JPEG
    quality for photo-compression; log: receives progress messages, such as each photo download.
    """
    if phone_format not in FORMATS:
        raise ValueError(f"unknown phone format {phone_format!r}; use one of {', '.join(FORMATS)}")
    return Context(
        region=region.upper() if region else None,
        phone_format=phone_format,
        fetch=fetch,
        photo_timeout=photo_timeout,
        photo_max_px=photo_max_px,
        photo_quality=photo_quality,
        log=log,
    )


def _apply(card: VCard, rules: list[Rule], ctx: Context) -> VCard:
    result = copy_card(card)
    for rule in rules:
        if rule.card is not None:
            rule.card(result, ctx)
    drop_orphaned_groups(card, result)
    return result


def clean(card: VCard, rules: Iterable[str] = DEFAULT_RULES, **options: Any) -> VCard:
    """Return a cleaned copy of one card. `options` are those of `make_context`.

    Rules that need the whole file (`groups-to-categories`) are skipped, and so are rules that depend on them
    (`drop-extensions`), so a group card never loses its membership here. Use `clean_cards` for a whole file.
    """
    ctx = make_context(**options)
    whole_file = {rule.name for rule in RULES if rule.cards is not None}
    active = [rule for rule in resolve(rules) if rule.name not in whole_file and not whole_file & set(rule.requires)]
    return _apply(card, active, ctx)


def clean_cards(
    cards: Iterable[VCard],
    rules: Iterable[str] = DEFAULT_RULES,
    *,
    progress: Callable[[list[VCard]], Iterable[VCard]] = iter,
    **options: Any,
) -> list[VCard]:
    """Return cleaned copies of all cards: whole-file rules first, then every card rule on each card.

    `progress` wraps the per-card loop, e.g. with a progress bar. `options` are those of `make_context`.
    """
    return list(iter_clean(cards, rules, progress=progress, **options))


def needs_all_cards(rules: Iterable[str]) -> bool:
    """True if a whole-file rule is active, so cards cannot be cleaned one at a time."""
    return any(rule.cards is not None for rule in resolve(rules))


def iter_clean(
    cards: Iterable[VCard],
    rules: Iterable[str] = DEFAULT_RULES,
    *,
    progress: Callable[[list[VCard]], Iterable[VCard]] | None = None,
    **options: Any,
) -> Iterator[VCard]:
    """Yield cleaned copies of the cards. `options` are those of `make_context`.

    Without whole-file rules and `progress`, cards are cleaned as they arrive, so memory use stays flat.
    Otherwise all cards are read first.
    """
    for _, cleaned in iter_clean_pairs(cards, rules, progress=progress, **options):
        yield cleaned


def iter_clean_pairs(
    cards: Iterable[VCard],
    rules: Iterable[str] = DEFAULT_RULES,
    *,
    progress: Callable[[list[VCard]], Iterable[VCard]] | None = None,
    **options: Any,
) -> Iterator[tuple[VCard, VCard]]:
    """Like `iter_clean`, but yield `(before, cleaned)`: `before` is the card after the whole-file rules,
    before the card rules, so it can be kept instead of `cleaned`."""
    ctx = make_context(**options)
    active = resolve(rules)
    stream: Iterable[VCard] = cards
    if progress is not None or any(rule.cards is not None for rule in active):
        work = [copy_card(card) for card in cards]
        for rule in active:
            if rule.cards is not None:
                work = rule.cards(work, ctx)
        stream = progress(work) if progress is not None else work
    for card in stream:
        yield card, _apply(card, active, ctx)


__all__ = [
    "DEFAULT_RULES",
    "RULES",
    "Context",
    "Rule",
    "clean",
    "clean_cards",
    "iter_clean",
    "iter_clean_pairs",
    "make_context",
    "needs_all_cards",
    "resolve",
]
