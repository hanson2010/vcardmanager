"""Email address rules."""

import re

from vcardmanager.clean.base import Context
from vcardmanager.model import VCard

_NAME_AND_ADDRESS = re.compile(r'^\s*"?[^"<]*"?\s*<(?P<address>[^>]+)>\s*$')
_PLACEHOLDER = re.compile(r"^nobody[a-z0-9]*@nowhere\.invalid$")


def email_lowercase(card: VCard, ctx: Context) -> None:
    """Lowercase addresses, unwrap `"Name" <addr>` and `mailto:`, and drop placeholder addresses."""
    kept = []
    for prop in card.properties:
        if prop.name == "EMAIL":
            address = prop.value.strip()
            if address.lower().startswith("mailto:"):
                address = address[7:]
            if match := _NAME_AND_ADDRESS.match(address):
                address = match["address"].strip()
            address = address.lower()
            if _PLACEHOLDER.match(address):
                card.warnings.append(f"EMAIL {address}: placeholder address removed")
                continue
            if "@" not in address:
                card.warnings.append(f"EMAIL {address}: no @ in address")
            if address != prop.value:
                prop.value = address
        kept.append(prop)
    card.properties = kept
