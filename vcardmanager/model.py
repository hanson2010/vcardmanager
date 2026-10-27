"""In-memory model: a vCard is an ordered list of properties."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from vcardmanager.encoding import escape, split_unescaped, unescape

# Properties whose value is split into components, with the separator used.
STRUCTURED = {
    "N": ";",
    "ADR": ";",
    "ORG": ";",
    "GENDER": ";",
    "CLIENTPIDMAP": ";",
    "CATEGORIES": ",",
    "NICKNAME": ",",
}

# Properties whose value is a URI or binary data: stored and written without text escaping.
VERBATIM = frozenset(
    {"URL", "PHOTO", "LOGO", "SOUND", "KEY", "SOURCE", "MEMBER", "IMPP", "FBURL", "CALURI", "CALADRURI", "GEO"}
)


@dataclass
class Property:
    name: str
    raw: str  # the value as a 3.0/4.0 file holds it: unfolded and decoded, but still escaped
    params: dict[str, list[str]] = field(default_factory=dict)
    group: str | None = None

    @classmethod
    def from_value(
        cls, name: str, value: str, params: dict[str, list[str]] | None = None, group: str | None = None
    ) -> Property:
        prop = cls(name.upper(), "", params or {}, group)
        prop.value = value
        return prop

    def copy(self) -> Property:
        """An independent copy: changing its params does not change this property."""
        return Property(self.name, self.raw, {key: list(values) for key, values in self.params.items()}, self.group)

    @property
    def value(self) -> str:
        """The unescaped value. For structured properties the separators are kept as they are."""
        return self.raw if self.name in VERBATIM else unescape(self.raw)

    @value.setter
    def value(self, value: str) -> None:
        self.raw = value if self.name in VERBATIM else escape(value)

    @property
    def components(self) -> list[str]:
        """The unescaped components of a structured value, e.g. the five parts of `N`."""
        sep = STRUCTURED.get(self.name, ";")
        return [unescape(part) for part in split_unescaped(self.raw, sep)]

    @components.setter
    def components(self, components: list[str]) -> None:
        sep = STRUCTURED.get(self.name, ";")
        self.raw = sep.join(escape(part) for part in components)


@dataclass
class VCard:
    properties: list[Property] = field(default_factory=list)
    source_version: str = "3.0"
    source: str | None = field(default=None, compare=False)  # "path:line", for reports
    warnings: list[str] = field(default_factory=list, compare=False)

    def get(self, name: str) -> Property | None:
        name = name.upper()
        return next((p for p in self.properties if p.name == name), None)

    def get_all(self, name: str) -> list[Property]:
        name = name.upper()
        return [p for p in self.properties if p.name == name]

    def add(self, prop: Property) -> None:
        self.properties.append(prop)

    def remove(self, pattern: str) -> int:
        """Remove properties whose name matches a glob pattern (`PHOTO`, `X-*`). Returns how many were removed."""
        pattern = pattern.upper()
        kept = [p for p in self.properties if not fnmatchcase(p.name, pattern)]
        removed = len(self.properties) - len(kept)
        self.properties = kept
        return removed

    @property
    def kind(self) -> str:
        """`"group"` for a group card in any of the known formats, else `"individual"`."""
        for name in ("KIND", "X-ADDRESSBOOKSERVER-KIND", "X-TYPE"):
            prop = self.get(name)
            if prop is not None and prop.value.strip().lower() == "group":
                return "group"
        return "individual"
