"""Pinyin keys, so a Chinese name matches the same name written in Latin letters (`陈磊` = `Lei Chen`)."""

import itertools
import re

from vcardmanager.clean.names import COMPOUND_SURNAMES, has_han, is_personal_name
from vcardmanager.model import VCard

MAX_READINGS = 16  # characters with several readings (单: dan, shan) multiply the keys; cap them
_LATIN_NAME_WORD = re.compile(r"^[a-z][a-z'-]*$")


def _name(card: VCard) -> str:
    fn = card.get("FN")
    return fn.value.split("/", 1)[0].strip() if fn is not None else ""  # without a label


def _plain(text: str) -> str:
    """Lowercase letters only, ü as u: `Wei-Cheng` → `weicheng`, `Lü` → `lu`."""
    return re.sub(r"[^a-z]", "", text.casefold().replace("ü", "u").replace("v", "u"))


def han_pinyin_keys(card: VCard) -> set[str]:
    """`surname given` in pinyin for a Chinese name of 2 to 4 characters only (`陈磊` → `chen lei`), one key
    per reading; empty for other names."""
    from pypinyin import Style, pinyin  # loads a large dictionary: only when pinyin matching is asked for

    name = _name(card).replace(" ", "")
    if not (2 <= len(name) <= 4 and all(has_han(ch) for ch in name)):
        return set()
    size = 2 if name[:2] in COMPOUND_SURNAMES and len(name) > 2 else 1
    readings = [[_plain(r) for r in rs] for rs in pinyin(name, style=Style.NORMAL, heteronym=True)]
    keys = set()
    for combo in itertools.islice(itertools.product(*readings), MAX_READINGS):
        keys.add(f"{''.join(combo[:size])} {''.join(combo[size:])}")
    return keys


def latin_pinyin_keys(card: VCard) -> set[str]:
    """`surname given` for a Latin name of 2 to 4 words, with the surname first or last (`Lei Chen`,
    `CHEN Lei` → `chen lei`; `Wei-Cheng Zhang` → `zhang weicheng`); empty for other names."""
    name = _name(card)
    words = name.casefold().split()
    if has_han(name) or not 2 <= len(words) <= 4 or not is_personal_name(name):
        return set()
    if not all(_LATIN_NAME_WORD.match(w) for w in words):
        return set()
    plain = [_plain(w) for w in words]
    return {f"{plain[-1]} {''.join(plain[:-1])}", f"{plain[0]} {''.join(plain[1:])}"}
