"""CardDAV address books (RFC 6352): read every contact, then upload only what changed."""

import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithPriorAuth,
    Request,
    build_opener,
)

from vcardmanager.model import VCard
from vcardmanager.parser import parse_text
from vcardmanager.writer import dumps

DAV = "{DAV:}"
CARDDAV = "{urn:ietf:params:xml:ns:carddav}"
BATCH = 100  # contacts per addressbook-multiget request

_XML = {"Content-Type": "application/xml; charset=utf-8"}
_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/></d:prop></d:propfind>'
)
_MULTIGET = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<c:addressbook-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><d:getetag/><c:address-data/></d:prop>{hrefs}</c:addressbook-multiget>"
)


class CardDAVError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Resource:
    """One contact on the server: its address, version tag (etag) and vCard text."""

    href: str
    etag: str | None
    text: str
    cards: list[VCard] = field(default_factory=list)


@dataclass
class Changes:
    updates: list[tuple[Resource, str]] = field(default_factory=list)  # resource and its new text
    deletes: list[Resource] = field(default_factory=list)
    creates: list[str] = field(default_factory=list)


def resource_href(card: VCard) -> str:
    """The resource a card was read from: its source is `<href>:<line>`."""
    return card.source.rsplit(":", 1)[0] if card.source else ""


def plan_changes(resources: Iterable[Resource], cards: Iterable[VCard], version: str = "3.0") -> Changes:
    """Compare the processed cards with what the server has.

    A resource is updated if its cards changed, and deleted if none of its cards is left (e.g. merged into
    another contact). Cards from no known resource are created. Unchanged contacts are not uploaded again.
    """
    resources = list(resources)
    known = {r.href for r in resources}
    by_href: dict[str, list[VCard]] = {}
    changes = Changes()
    for card in cards:
        href = resource_href(card)
        if href in known:
            by_href.setdefault(href, []).append(card)
        else:
            changes.creates.append(dumps([card], version))
    for resource in resources:
        if not resource.cards:  # nothing readable in it: leave it alone
            continue
        new = by_href.get(resource.href)
        if not new:
            changes.deletes.append(resource)
        elif (text := dumps(new, version)) != dumps(resource.cards, version):
            changes.updates.append((resource, text))
    return changes


class AddressBook:
    def __init__(self, url: str, username: str, password: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/") + "/"
        self.timeout = timeout
        passwords = HTTPPasswordMgrWithPriorAuth()
        passwords.add_password(None, self.url, username, password, is_authenticated=True)  # Basic, sent upfront
        self._opener = build_opener(HTTPBasicAuthHandler(passwords), HTTPDigestAuthHandler(passwords))

    def _send(self, method: str, url: str, body: str | None = None, headers: dict[str, str] | None = None) -> bytes:
        data = body.encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers=headers or {})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return bytes(response.read())
        except HTTPError as exc:
            reason = "wrong username or password" if exc.code == 401 else f"HTTP {exc.code} {exc.reason}"
            raise CardDAVError(f"{method} {url}: {reason}", exc.code) from exc
        except (URLError, OSError) as exc:
            raise CardDAVError(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from exc

    def _path(self, href: str) -> str:
        return unquote(urlparse(urljoin(self.url, href)).path).rstrip("/")

    def fetch(self) -> list[Resource]:
        """Every contact in the address book, in server order."""
        listing = self._send("PROPFIND", self.url, _PROPFIND, {**_XML, "Depth": "1"})
        is_book, hrefs = False, []
        for href, props in _responses(listing):
            kinds = props.get(f"{DAV}resourcetype")
            if kinds is not None and kinds.find(f"{DAV}collection") is not None:
                if self._path(href) == self._path(self.url):
                    is_book = kinds.find(f"{CARDDAV}addressbook") is not None
                continue
            hrefs.append(href)
        if not is_book:
            raise CardDAVError(f"{self.url} is not a CardDAV address book; give the address book's URL")
        by_path = {self._path(href): href for href in hrefs}
        found: dict[str, Resource] = {}
        for start in range(0, len(hrefs), BATCH):
            batch = "".join(f"<d:href>{_escape(h)}</d:href>" for h in hrefs[start : start + BATCH])
            reply = self._send("REPORT", self.url, _MULTIGET.format(hrefs=batch), {**_XML, "Depth": "1"})
            for href, props in _responses(reply):
                data = props.get(f"{CARDDAV}address-data")
                original = by_path.get(self._path(href))
                if original is None or data is None or not data.text:
                    continue
                etag = props.get(f"{DAV}getetag")
                text = data.text
                found[original] = Resource(original, etag.text if etag is not None else None, text,
                                           parse_text(text, source=original))  # fmt: skip
        return [found[h] for h in hrefs if h in found]

    def apply(self, changes: Changes, done: Callable[[str, bool], None] | None = None) -> list[str]:
        """Upload new and changed contacts, then delete merged-away ones. Returns one message per failure.

        `done` is called after each request with what it did (`updated Ann Lee`) and whether it worked.
        """
        failures = []
        vcard = {"Content-Type": "text/vcard; charset=utf-8"}
        jobs: list[tuple[str, str, str | None, dict[str, str], str]] = [
            ("PUT", urljoin(self.url, f"{uuid.uuid4()}.vcf"), text, {**vcard, "If-None-Match": "*"},
             f"added {_name(parse_text(text))}")
            for text in changes.creates
        ]  # fmt: skip
        jobs += [
            ("PUT", urljoin(self.url, r.href), text, {**vcard, **_if_match(r)}, f"updated {_name(parse_text(text))}")
            for r, text in changes.updates
        ]
        jobs += [
            ("DELETE", urljoin(self.url, r.href), None, _if_match(r), f"deleted {_name(r.cards)}")
            for r in changes.deletes
        ]
        for method, url, body, headers, label in jobs:
            try:
                self._send(method, url, body, headers)
                ok = True
            except CardDAVError as exc:
                ok = False
                if exc.status == 412:
                    failures.append(f"{method} {url}: changed on the server meanwhile; skipped")
                else:
                    failures.append(str(exc))
            if done is not None:
                done(label, ok)
        return failures


def _name(cards: list[VCard]) -> str:
    fn = cards[0].get("FN") if cards else None
    return fn.value if fn is not None and fn.value else "(no name)"


def _if_match(resource: Resource) -> dict[str, str]:
    return {"If-Match": resource.etag} if resource.etag else {}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _responses(body: bytes) -> Iterator[tuple[str, dict[str, ET.Element]]]:
    """(href, {property tag: element}) for each response of a multistatus, with only the found properties."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CardDAVError(f"the server sent invalid XML: {exc}") from exc
    for response in root.iter(f"{DAV}response"):
        href = response.findtext(f"{DAV}href")
        if not href:
            continue
        props: dict[str, ET.Element] = {}
        for propstat in response.findall(f"{DAV}propstat"):
            if " 200 " not in (propstat.findtext(f"{DAV}status") or ""):
                continue
            for prop in propstat.findall(f"{DAV}prop"):
                props.update((child.tag, child) for child in prop)
        yield href.strip(), props
