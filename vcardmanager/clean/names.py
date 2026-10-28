"""Name rules: fill FN/N, move labels to the N suffix and prefix, split names into family and given parts."""

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


def _concat(first: str, second: str) -> str:
    """Join two name parts: no space between Han characters (`张` + `三`), else one space."""
    if first and second and has_han(first[-1]) and has_han(second[0]):
        return first + second
    return " ".join(part for part in (first, second) if part)


def _fold_middle(given: str, middle: str) -> str:
    """The given name with the middle name moved in: after it (`Ann M.`), or before it when both are Han, as
    Chinese names read (`张;诚;伟` is 张伟诚)."""
    if has_han(given[:1]) and has_han(middle[-1:]):
        return middle + given
    return _concat(given, middle)


def _full_name(family: str, given: str) -> str:
    """English mode (a Latin surname): given + surname. Otherwise surname + given."""
    if family and not has_han(family):
        return _concat(given, family)
    return _concat(family, given)


def _name_from_n(card: VCard) -> str:
    n = card.get("N")
    if n is None:
        return ""
    family, given, middle, prefix, suffix = (c.strip() for c in _pad(n.components)[:5])
    name = _concat(prefix, _full_name(family, _fold_middle(given, middle)))
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


MIN_HYPHEN_PARTS = 2  # `单位-快递`: category parts, then the name


def _hyphen_prefix(fn: str) -> tuple[str, str, str] | None:
    """(prefix, name, label) of an FN like `AA-BB-CC-DD/label`: (`AA-BB-CC-`, `DD`, `/label`), else None.

    Any number of parts from MIN_HYPHEN_PARTS on; none may be empty. A Latin name with a hyphen
    (`Mary-Jane Smith`, `Jean-Pierre`) is a person's name, not one of these.
    """
    name, slash, label = fn.partition("/")
    if not has_han(name) and is_personal_name(name.strip()):
        return None
    parts = [part.strip() for part in name.split("-")]
    if len(parts) < MIN_HYPHEN_PARTS or not all(parts):
        return None
    return "-".join(parts[:-1]) + "-", parts[-1], slash + label.strip()


def name_prefix(card: VCard, ctx: Context) -> None:
    """For an FN of two or more parts joined by hyphens (`单位-快递-顺丰-张三`), put all but the last
    (`单位-快递-顺丰-`) in the N prefix and the last (`张三`) in the family name; a label after a slash goes
    to the suffix. FN is not changed.

    An N that holds something else than FN's name is left alone.
    """
    fn, n = card.get("FN"), card.get("N")
    if fn is None or n is None or card.kind == "group" or (split := _hyphen_prefix(fn.value)) is None:
        return
    prefix, name, label = split
    components = _pad(n.components)
    held = "".join(components[:4])
    if held.strip() and not any(_same_name(held, prefix + name + extra) for extra in ("", label)):
        return
    components[:4] = [name, "", "", prefix]
    if label and not components[4]:
        components[4] = label
    n.components = components


def _split_english(words: list[str]) -> tuple[str, list[str]]:
    """(family, given words) of an English name: the last word, or the only all-caps word (`Gavin KONG`)."""
    capitals = [w for w in words if len(w) > 1 and w.isupper()]
    family = capitals[0] if len(capitals) == 1 else words[-1]
    return family, [w for w in words if w is not family]


def _split(family: str, given: str) -> tuple[str, str] | None:
    """Lay out N's (family, given) in one of two modes, or None if it is not a person's name.

    English mode, when there are an English surname and an English given name: the English surname is the
    family name, and anything else (a Chinese name) goes before the English given name.
    Non-English mode: the Chinese surname is the family name, and an English word goes after the given name.

        张三             ->  张;三
        John Smith       ->  Smith;John
        Bob 张三         ->  张;三 Bob
        史强 John Smith  ->  Smith;史强 John

    Where the English part stands does not matter. A surname the app already split off is kept.
    """
    text = _concat(family, given)
    if not is_personal_name(text):
        return None
    tokens = _TOKEN.findall(text)
    han = [t for t in tokens if has_han(t)]
    english = [t for t in tokens if not has_han(t)]
    if len(english) >= 2:
        if family and not has_han(family) and " " not in family:
            surname = family  # `Smith;史强 John`, `Lee;Ann`: as the app split it
            english.remove(family)
        else:
            surname, english = _split_english(english)
        return surname, " ".join([*han, *english])
    if not han:
        return None  # a single word: nothing to split
    run = han[0]
    split_off = "".join(t for t in _TOKEN.findall(family) if has_han(t))
    if split_off and run.startswith(split_off) and len(split_off) < len(run):
        surname = split_off  # `张;三`, `Bob 张;三`: as the app split it
    else:
        surname = run[:2] if run[:2] in COMPOUND_SURNAMES and len(run) > 2 else run[0]
    return surname, " ".join([run[len(surname) :], *english])


def _split_name(text: str) -> tuple[str, str] | None:
    """(family, given) of a full name, as `name-split` lays it out, or None if it is not a person's name."""
    return _split("", text)


def _same_name(a: str, b: str) -> bool:
    """The same characters, in any order and spacing."""
    return sorted(a.replace(" ", "").casefold()) == sorted(b.replace(" ", "").casefold())


def name_split(card: VCard, ctx: Context) -> None:
    """Put family and given names in the right N fields (see `_split`) and move a middle name into the given
    name. FN is rebuilt to match when it holds the same name in another order (`Bob 张三` -> `张三 Bob`)."""
    n = card.get("N")
    if n is None or card.kind == "group":
        return
    components = _pad(n.components)
    if components[3].strip().endswith("-"):
        return  # laid out by `name-prefix`: the family name is kept whole
    family, given, middle = (c.strip() for c in components[:3])
    if middle:
        given, components[2] = _fold_middle(given, middle), ""
    split = _split(family, given)
    if split is not None:
        family, given = split
    if middle or split is not None:
        components[0], components[1] = family, given
        if components != n.components:
            n.components = components
    fn = card.get("FN")
    if split is None or fn is None:
        return
    name, slash, label = fn.value.partition("/")
    full = _full_name(family, given)
    if name.strip() != full and _same_name(name, full):
        fn.value = full + slash + label


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
    they are, so `张;三 Bob` does not change. Left alone: single words (`IBM`, `McDonald`), handles (names
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
