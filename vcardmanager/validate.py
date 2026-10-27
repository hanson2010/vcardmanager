"""Check cards against RFC 6350 (vCard 4.0) and RFC 2426 (vCard 3.0)."""

from collections import Counter
from dataclasses import dataclass

from vcardmanager.model import VCard

ERROR = "error"
WARNING = "warning"

KNOWN_PROPERTIES = frozenset(
    {
        # RFC 6350
        "SOURCE", "KIND", "XML", "FN", "N", "NICKNAME", "PHOTO", "BDAY", "ANNIVERSARY", "GENDER", "ADR", "TEL",
        "EMAIL", "IMPP", "LANG", "TZ", "GEO", "TITLE", "ROLE", "LOGO", "ORG", "MEMBER", "RELATED", "CATEGORIES",
        "NOTE", "PRODID", "REV", "SOUND", "UID", "CLIENTPIDMAP", "URL", "KEY", "FBURL", "CALADRURI", "CALURI",
        # RFC 2426 only
        "LABEL", "MAILER", "NAME", "PROFILE", "SORT-STRING", "CLASS", "AGENT",
    }
)  # fmt: skip
KNOWN_PARAMETERS = frozenset(
    {"TYPE", "PREF", "VALUE", "LANGUAGE", "ALTID", "PID", "MEDIATYPE", "CALSCALE", "SORT-AS", "GEO", "TZ", "LABEL"}
    | {"ENCODING", "CONTEXT"}
)
AT_MOST_ONCE = ("N", "BDAY", "ANNIVERSARY", "GENDER", "KIND", "PRODID", "REV", "UID")

_COMMON_TYPES = {"HOME", "WORK", "PREF"}
KNOWN_TYPES = {
    "TEL": _COMMON_TYPES
    | {"TEXT", "VOICE", "FAX", "CELL", "VIDEO", "PAGER", "TEXTPHONE", "MSG", "BBS", "MODEM"}
    | {"CAR", "ISDN", "PCS", "MAIN"},
    "EMAIL": _COMMON_TYPES | {"INTERNET", "X400"},
    "ADR": _COMMON_TYPES | {"DOM", "INTL", "POSTAL", "PARCEL"},
    "LABEL": _COMMON_TYPES | {"DOM", "INTL", "POSTAL", "PARCEL"},
}
# TYPE on these names a media type (JPEG, PNG…), which is not checked
_MEDIA_PROPERTIES = {"PHOTO", "LOGO", "SOUND", "KEY"}
KINDS = {"individual", "group", "org", "location"}


@dataclass(frozen=True)
class Issue:
    level: str  # ERROR or WARNING
    message: str


def validate(card: VCard) -> list[Issue]:
    issues = [Issue(WARNING, w) for w in card.warnings if w != "missing FN"]
    if card.get("FN") is None:
        issues.append(Issue(ERROR, "missing FN"))
    counts = Counter(p.name for p in card.properties)
    issues += [
        Issue(ERROR, f"{name} appears {counts[name]} times; at most once is allowed")
        for name in AT_MOST_ONCE
        if counts[name] > 1
    ]
    for prop in card.properties:
        if prop.name not in KNOWN_PROPERTIES and not prop.name.startswith("X-"):
            issues.append(Issue(WARNING, f"non-standard property {prop.name}"))
        for key in prop.params:
            if key not in KNOWN_PARAMETERS and not key.startswith("X-"):
                issues.append(Issue(WARNING, f"{prop.name}: non-standard parameter {key}"))
        if prop.name not in _MEDIA_PROPERTIES:
            allowed = KNOWN_TYPES.get(prop.name, _COMMON_TYPES)
            for value in prop.params.get("TYPE", []):
                if value.upper() not in allowed and not value.upper().startswith("X-"):
                    issues.append(Issue(WARNING, f"{prop.name}: unknown TYPE {value}"))
        if prop.name == "N" and len(prop.components) != 5:
            issues.append(Issue(WARNING, f"N has {len(prop.components)} components, expected 5"))
        if prop.name == "KIND" and prop.value.lower() not in KINDS:
            issues.append(Issue(WARNING, f"unknown KIND {prop.value}"))
    return issues
