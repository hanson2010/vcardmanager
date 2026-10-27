"""Name rules: fill FN/N, move labels to the N suffix, split names into family and given parts."""

import re

from vcardmanager.clean.base import Context
from vcardmanager.model import Property, VCard

_HAN_CHARS = "㐀-䶿一-鿿豈-﫿"
_HAN = re.compile(f"[{_HAN_CHARS}]")
_TOKEN = re.compile(f"[{_HAN_CHARS}]+|[^\\s{_HAN_CHARS}]+")
_LATIN_WORD = re.compile(r"^[^\W\d_][\w'.-]*$")

COMPOUND_SURNAMES = frozenset(
    {
        "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "令狐", "司徒", "夏侯", "长孙",
        "宇文", "轩辕", "端木", "澹台", "申屠", "太史", "闻人", "呼延", "万俟", "独孤", "南宫", "西门", "百里",
        "东郭", "钟离", "赫连", "拓跋", "公冶", "濮阳", "淳于", "单于", "司空", "亓官", "司寇", "子车", "颛孙",
        "巫马", "公西", "漆雕", "乐正", "壤驷", "公良",
    }
)  # fmt: skip

# email local parts that name a role, not a person: the domain says whose they are
_ROLE_ADDRESSES = ("contact", "info", "admin", "hello", "support", "service", "sales", "noreply", "no-reply")


def _pad(components: list[str], size: int = 5) -> list[str]:
    return (components + [""] * size)[:size] if len(components) < size else components


def has_han(text: str) -> bool:
    return bool(_HAN.search(text))


def _name_from_n(card: VCard) -> str:
    n = card.get("N")
    if n is None:
        return ""
    family, given, additional, prefix, suffix = (c.strip() for c in _pad(n.components)[:5])
    if has_han(family + given + additional):
        name = family + additional + given
    else:
        name = " ".join(part for part in (prefix, given, additional, family) if part)
    if suffix and name:
        name += suffix if suffix.startswith("/") else f" {suffix}"
    return name


def _name_from_email(card: VCard) -> str:
    email = card.get("EMAIL")
    if email is None or "@" not in email.value:
        return ""
    local, _, domain = email.value.strip().partition("@")
    if local.lower().startswith(_ROLE_ADDRESSES):
        return f"{local} ({domain})"
    return local


def _first_component(card: VCard, name: str) -> str:
    prop = card.get(name)
    return prop.components[0].strip() if prop is not None and prop.components else ""


def name_fill(card: VCard, ctx: Context) -> None:
    """Build a missing FN from N, ORG, the email address or the phone number; add N from FN if missing.

    An N with fewer than five parts is padded.
    """
    fn = card.get("FN")
    if fn is None or not fn.value.strip():
        tel = card.get("TEL")
        name = (
            _name_from_n(card)
            or _first_component(card, "ORG")
            or _name_from_email(card)
            or (tel.value.strip() if tel is not None else "")
        )
        if not name:
            card.warnings.append("no FN, and nothing to build one from")
            return
        if fn is None:
            card.properties.insert(0, Property.from_value("FN", name))
        else:
            fn.value = name
        fn = card.get("FN")
        card.warnings = [w for w in card.warnings if w != "missing FN"] + [f"missing FN, filled with {name!r}"]
    n = card.get("N")
    if n is None and fn is not None and card.kind != "group":
        n = Property("N", "")
        n.components = ["", fn.value.strip(), "", "", ""]
        card.properties.insert(card.properties.index(fn) + 1, n)
    elif n is not None and len(components := n.components) < 5:
        n.components = _pad(components)  # N has five parts: family;given;additional;prefix;suffix


def is_personal_name(name: str) -> bool:
    """A person's name: one Han name of 2 to 4 characters with optional Latin words, or 1 to 4 Latin words."""
    tokens = _TOKEN.findall(name)
    han = [t for t in tokens if has_han(t)]
    if not all(has_han(t) or _LATIN_WORD.match(t) for t in tokens):
        return False
    if han:
        return len(han) == 1 and 2 <= len(han[0]) <= 4
    return 1 <= len(tokens) <= 4


def name_label(card: VCard, ctx: Context) -> None:
    """Move a label after a slash in FN (`沈云/APA`) to the N suffix, keeping the slash. FN is not changed."""
    fn, n = card.get("FN"), card.get("N")
    if fn is None or n is None or card.kind == "group":
        return
    slash = fn.value.find("/")
    if slash <= 0:
        return
    name, label = fn.value[:slash].strip(), fn.value[slash:].strip()
    if not is_personal_name(name):
        return
    components = _pad(n.components)
    if any("/" in c for c in components[:4]):
        n.components = ["", name, "", "", label]  # the label was stuffed into the name parts: rebuild them
    elif not components[4]:
        components[4] = label
        n.components = components


def _split_english(words: list[str]) -> tuple[str, list[str]]:
    """(family, given words) of an English name: the last word, or the only all-caps word (`Gavin KONG`)."""
    capitals = [w for w in words if len(w) > 1 and w.isupper()]
    family = capitals[0] if len(capitals) == 1 else words[-1]
    return family, [w for w in words if w is not family]


def _split_name(text: str) -> tuple[str, str] | None:
    """Split a full name into (family, given), or None if it does not look like a person's name.

    N is laid out so that reading it in the name's own order gives the full name back:

        张三                                     ->  张;三
        John Smith                               ->  Smith;John
        Chinese name + one English word          ->  Bob 张;三
        Chinese name + two or more English words ->  Smith;史强 John

    A Chinese name is read family-first, so a single English word goes before the surname. Two or more English
    words are a full English name, read given-first, so the Chinese name goes before the given name. Where the
    English part stands in FN does not matter.
    """
    if not is_personal_name(text):
        return None
    tokens = _TOKEN.findall(text)
    han = [t for t in tokens if has_han(t)]
    english = [t for t in tokens if not has_han(t)]
    if len(english) >= 2:  # `John Smith`, `史强 John Smith`, `Bob Lee 张三`
        family, given = _split_english(english)
        return family, " ".join([*han, *given])
    if not han:
        return None  # a single word: nothing to split
    run = han[0]  # `张三`, `Bob 张三`, `傅清洋Lena`
    surname = run[:2] if run[:2] in COMPOUND_SURNAMES and len(run) > 2 else run[0]
    return " ".join([*english, surname]), run[len(surname) :]


def name_split(card: VCard, ctx: Context) -> None:
    """Put family and given names in the right N fields; see `_split_name` for mixed Chinese/English names."""
    n = card.get("N")
    if n is None or card.kind == "group":
        return
    components = _pad(n.components)
    family, given = components[0].strip(), components[1].strip()
    if family and not has_han(family):
        return  # a Latin family name, as the app wrote it
    if family and (len(family) == 1 or family in COMPOUND_SURNAMES):
        return  # already split
    split = _split_name(family + given if family else given)
    if split is not None:
        components[0], components[1] = split
        n.components = components


_WORD = re.compile(r"[^\W\d_]+")


def _is_handle(text: str, handles: set[str]) -> bool:
    """`lin_shan`, `user2`, an email address or the contact's email name: not a name to re-case."""
    return "_" in text or "@" in text or any(ch.isdigit() for ch in text) or text.strip().casefold() in handles


def _needs_case_fix(name: str, handles: set[str]) -> bool:
    """A Latin name written all in capitals or all in lowercase, that is not an abbreviation or a handle."""
    name = name.split("/", 1)[0].strip()  # the label after a slash does not count
    if has_han(name) or len(name) < 2 or not (name.isupper() or name.islower()) or _is_handle(name, handles):
        return False
    return not (name.isupper() and " " not in name and len(name) <= 4)  # `IBM`, `ACI`: abbreviations


def _title(text: str) -> str:
    """`mary-jane o'neil` → `Mary-Jane O'Neil`; a label after a slash keeps its case."""
    name, slash, label = text.partition("/")
    return _WORD.sub(lambda m: m[0].capitalize(), name.lower()) + slash + label


def name_case(card: VCard, ctx: Context) -> None:
    """Write Latin surnames in capitals and fix the case of all-caps or all-lowercase names.

    `John Smith`, `JOHN SMITH` and `john smith` all become FN `John SMITH` and N `SMITH;John`. The surname
    is N's family field (or, without N, what `name-split` would take from FN); Chinese surnames are left as
    they are, so `Bob 张;三` does not change. Left alone: single words (`IBM`, `McDonald`), handles (names
    with `_`, `@` or digits, or equal to the contact's email name: `lin_shan`, `tzhao`) and labels after a slash.
    """
    handles = {e.value.split("@")[0].strip().casefold() for e in card.get_all("EMAIL") if "@" in e.value}
    fn, n = card.get("FN"), card.get("N")
    name = fn.value if fn is not None else " ".join(n.components[:2]) if n is not None else ""
    if _needs_case_fix(name, handles):  # given names: `JOHN SMITH` → `John Smith`
        if fn is not None:
            fn.value = _title(fn.value)
        if n is not None:
            n.components = [
                c if not c or c.startswith("/") or has_han(c) or _is_handle(c, handles) else _title(c)
                for c in n.components
            ]
    _upper_surname(fn, n, handles)


def _upper_surname(fn: Property | None, n: Property | None, handles: set[str]) -> None:
    """`John Smith` → `John SMITH` in FN, and `SMITH` in N's family field."""
    if n is not None:
        surname = _pad(n.components)[0].strip()
    elif fn is not None and (split := _split_name(fn.value.split("/", 1)[0].strip())) is not None:
        surname = split[0]
    else:
        return
    if not surname or surname.isupper() or has_han(surname) or _is_handle(surname, handles):
        return
    if n is not None:
        components = _pad(n.components)
        components[0] = surname.upper()
        n.components = components
    if fn is not None:
        name, slash, label = fn.value.partition("/")
        # the last occurrence: in `史强 John Smith` and `Smith John`-style names alike, it is the surname word
        matches = list(re.finditer(rf"(?<![^\W\d_]){re.escape(surname)}(?![^\W\d_])", name, re.IGNORECASE))
        if matches:
            last = matches[-1]
            fn.value = name[: last.start()] + surname.upper() + name[last.end() :] + slash + label
