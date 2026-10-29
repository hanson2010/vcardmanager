"""Normalized match keys: two cards that share a key are the same person."""

import re
import unicodedata

import phonenumbers
from phonenumbers import NumberParseException

from vcardmanager.model import VCard

MIN_PHONE_DIGITS = 7  # shorter numbers are service hotlines (95338) that many contacts share
_ADDRESS_IN_ANGLES = re.compile(r"<([^>]+)>")
# `example.com.cn` is `example.com` plus a country code: the same organisation
_COUNTRY_DOMAIN = re.compile(r"^(?P<base>.+\.(?:com|net|org|edu|gov))\.[a-z]{2}$")


def phone_key(value: str, region: str | None = None) -> str | None:
    """The national significant number, so `+86 138 0013 8000`, `0086…` and `13800138000` are equal.

    The country code is ignored: a number without one cannot be told apart from the same digits abroad.
    """
    text = value.strip().removeprefix("tel:").strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    digits = re.sub(r"\D", "", text)
    national = digits.lstrip("0")  # a national number's trunk prefix
    if text.startswith("+") or region:
        try:
            national = phonenumbers.national_significant_number(phonenumbers.parse(text, region))
        except NumberParseException:
            pass
    return national if len(national) >= MIN_PHONE_DIGITS else None


def email_key(value: str) -> str | None:
    match = _ADDRESS_IN_ANGLES.search(value)
    address = (match[1] if match else value).strip().removeprefix("mailto:").lower()
    return address if "@" in address else None


def email_match_key(value: str) -> str | None:
    """Like `email_key`, but a domain with a country code added matches the one without:
    `user@example.com.cn` = `user@example.com`. For telling cards apart, not values: both addresses are kept."""
    address = email_key(value)
    if address is None:
        return None
    user, _, domain = address.rpartition("@")
    match = _COUNTRY_DOMAIN.match(domain)
    return f"{user}@{match['base']}" if match else address


def name_key(card: VCard) -> str | None:
    """FN without its label (see `name_label`), normalized: compatibility forms folded, case ignored, words
    sorted (`Lee Ann` = `ann lee`)."""
    fn = card.get("FN")
    if fn is None:
        return None
    text = unicodedata.normalize("NFKC", fn.value.split("/", 1)[0]).casefold()
    words = text.split()
    if not words or "@" in text or not any(ch.isalpha() for ch in text):
        return None  # empty, an email address, or a phone number standing in for a name
    return " ".join(sorted(words))


def name_label(card: VCard) -> str:
    """The label after a slash in FN (`王小明/Mkt` → `mkt`), normalized; empty without one."""
    fn = card.get("FN")
    if fn is None or "/" not in fn.value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", fn.value.split("/", 1)[1]).casefold().split())
