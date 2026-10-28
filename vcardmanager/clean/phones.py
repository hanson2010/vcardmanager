"""Phone number rules, using Google's libphonenumber port."""

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumber, PhoneNumberFormat, PhoneNumberType, shortnumberinfo

from vcardmanager.clean.base import Context
from vcardmanager.model import Property, VCard

FORMATS = {
    "e164": PhoneNumberFormat.E164,
    "international": PhoneNumberFormat.INTERNATIONAL,
    "national": PhoneNumberFormat.NATIONAL,
}


def _number_text(prop: Property) -> tuple[str, bool]:
    """The number without a `tel:` prefix, and whether it had one."""
    value = prop.value.strip()
    if value.lower().startswith("tel:"):
        return value[4:], True
    return value, False


def _parse(text: str, region: str | None) -> PhoneNumber | None:
    if text.startswith("00"):
        text = "+" + text[2:]
    if not text.startswith("+") and region is None:
        return None  # no country code and no region to assume: leave it alone
    try:
        number = phonenumbers.parse(text, region)
    except NumberParseException:
        return None
    return number if phonenumbers.is_valid_number(number) else None


def _short_number(text: str, region: str | None) -> str | None:
    """A service number such as 95588 or 110 of `region`, as it is dialed there; None if `text` is not one.

    These are only dialed inside their country, so `+86 95588` becomes `95588`.
    """
    if region is None:
        return None
    if text.startswith("00"):
        text = "+" + text[2:]
    try:
        number = phonenumbers.parse(text, region)
    except NumberParseException:
        return None
    if phonenumbers.region_code_for_number(number) != region or not shortnumberinfo.is_valid_short_number_for_region(
        number, region
    ):
        return None
    return phonenumbers.national_significant_number(number)


def phone_format(card: VCard, ctx: Context) -> None:
    """Rewrite valid numbers in E.164, international or national format. Others are left as they are,
    except that service numbers (95588) lose a country code they cannot be dialed with."""
    for prop in card.get_all("TEL"):
        text, uri = _number_text(prop)
        number = _parse(text, ctx.region)
        if number is None:
            short = _short_number(text, ctx.region)
            if short is not None:
                prop.value = f"tel:{short}" if uri else short
                continue
            if text.startswith(("+", "00")) or ctx.region is not None:
                card.warnings.append(f"TEL {text}: not a valid phone number, left unchanged")
            continue
        style = FORMATS[ctx.phone_format]
        if style == PhoneNumberFormat.NATIONAL and phonenumbers.region_code_for_number(number) != ctx.region:
            style = PhoneNumberFormat.INTERNATIONAL  # a national format would lose a foreign country code
        formatted = phonenumbers.format_number(number, style)
        prop.value = f"tel:{formatted}" if uri else formatted


def phone_type(card: VCard, ctx: Context) -> None:
    """A landline tagged CELL loses CELL (VOICE if no other type is left); a mobile number gets CELL."""
    for prop in card.get_all("TEL"):
        number = _parse(_number_text(prop)[0], ctx.region)
        if number is None:
            continue
        types = list(prop.params.get("TYPE", []))
        kind = phonenumbers.number_type(number)
        if kind == PhoneNumberType.FIXED_LINE and any(t.upper() == "CELL" for t in types):
            types = [t for t in types if t.upper() != "CELL"]
            if not any(t.upper() != "PREF" for t in types):
                types.append("VOICE")
        elif kind == PhoneNumberType.MOBILE and not any(t.upper() == "CELL" for t in types):
            types.append("CELL")
        else:
            continue
        prop.params["TYPE"] = types
