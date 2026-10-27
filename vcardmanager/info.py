"""Summary statistics for `info`."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from vcardmanager.model import VCard

_LINE_PREFIX = re.compile(r"^line \d+: ")


@dataclass
class Summary:
    cards: int = 0
    groups: int = 0
    versions: Counter[str] = field(default_factory=Counter)
    properties: Counter[str] = field(default_factory=Counter)  # number of cards that have the property
    warnings: Counter[str] = field(default_factory=Counter)

    @property
    def individuals(self) -> int:
        return self.cards - self.groups


def summarize(cards: Iterable[VCard]) -> Summary:
    summary = Summary()
    for card in cards:
        summary.cards += 1
        summary.groups += card.kind == "group"
        summary.versions[card.source_version] += 1
        summary.properties.update({p.name for p in card.properties})
        summary.warnings.update(_LINE_PREFIX.sub("", w) for w in card.warnings)
    return summary


def format_summary(summary: Summary) -> str:
    lines = [
        f"Cards:      {summary.cards} ({summary.individuals} contacts, {summary.groups} groups)",
        "Versions:   " + ", ".join(f"{v} × {n}" for v, n in sorted(summary.versions.items())),
        "",
        "Properties (cards that have them):",
    ]
    width = max((len(name) for name in summary.properties), default=0)
    lines += [f"  {name:<{width}}  {count}" for name, count in summary.properties.most_common()]
    if summary.warnings:
        lines += ["", "Warnings:"]
        lines += [f"  {count:>4} × {message}" for message, count in summary.warnings.most_common()]
    return "\n".join(lines)
