import base64
import itertools
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import pytest
from click.testing import CliRunner

from vcardmanager import parse_text
from vcardmanager.carddav import AddressBook, CardDAVError, Resource, plan_changes
from vcardmanager.cli import main

BOOK = "/dav/ann/contacts/"
AUTH = "Basic " + base64.b64encode(b"ann:secret").decode()


def vcard(uid, body):
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\n{body}END:VCARD\r\n"


class Server:
    """A minimal CardDAV server: one address book, Basic auth, etags checked on PUT and DELETE."""

    def __init__(self):
        self.store: dict[str, tuple[str, str]] = {}  # href → (etag, text)
        self.tags = itertools.count(1)
        self.requests: list[str] = []

    def put(self, name, text):
        self.store[BOOK + name] = (f'"{next(self.tags)}"', text)


def handler_for(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code, body=b"", headers=()):
            self.send_response(code)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _start(self):
            server.requests.append(f"{self.command} {self.path}")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Authorization") != AUTH:
                self._reply(401, headers=[("WWW-Authenticate", 'Basic realm="test"')])
                return None
            return body

        def _multistatus(self, responses):
            parts = "".join(
                f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{props}</d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                for href, props in responses
            )
            body = f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">{parts}</d:multistatus>'
            self._reply(207, body.encode(), [("Content-Type", "application/xml")])

        def do_PROPFIND(self):
            if self._start() is None:
                return
            book = (BOOK, "<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>")
            members = [(href, f"<d:resourcetype/><d:getetag>{etag}</d:getetag>") for href, (etag, _) in server.store.items()]
            self._multistatus([book, *members])

        def do_REPORT(self):
            body = self._start()
            if body is None:
                return
            hrefs = [e.text for e in ET.fromstring(body).iter("{DAV:}href")]
            self._multistatus(
                (href, f"<d:getetag>{server.store[href][0]}</d:getetag>"
                       f"<c:address-data>{escape(server.store[href][1])}</c:address-data>")  # fmt: skip
                for href in hrefs
                if href in server.store
            )

        def _precondition_failed(self):
            current = server.store.get(self.path)
            if self.headers.get("If-None-Match") == "*" and current:
                return True
            match = self.headers.get("If-Match")
            return match is not None and (current is None or current[0] != match)

        def do_PUT(self):
            body = self._start()
            if body is None:
                return
            if self._precondition_failed():
                return self._reply(412)
            server.store[self.path] = (f'"{next(server.tags)}"', body.decode())
            self._reply(201)

        def do_DELETE(self):
            if self._start() is None:
                return
            if self._precondition_failed():
                return self._reply(412)
            del server.store[self.path]
            self._reply(204)

    return Handler


@pytest.fixture
def server(monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "127.0.0.1")  # never send the test server's traffic through a proxy
    state = Server()
    state.put("ann.vcf", vcard("ann", "FN:Ann Lee\r\nEMAIL:ann@x.example\r\n"))
    state.put("ann2.vcf", vcard("ann2", "FN:Ann Lee\r\nTEL:+8613800138000\r\nEMAIL:ann@x.example\r\n"))
    state.put("bob.vcf", vcard("bob", "FN:Bob\r\nN:;Bob;;;\r\nEMAIL:bob@x.example\r\n"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{httpd.server_port}{BOOK}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def run(*args, password="secret"):
    return CliRunner().invoke(main, [str(a) for a in args], input=f"{password}\n")


def test_dedupe_on_server(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bob = server.store[BOOK + "bob.vcf"]
    result = run("dedupe", server.url, "--username", "ann", "--backup")
    assert result.exit_code == 0, result.output
    assert "Read 3 contacts" in result.stderr
    assert "Server: 1 contacts updated, 0 added, 1 deleted" in result.stderr
    assert sorted(server.store) == [BOOK + "ann2.vcf", BOOK + "bob.vcf"]  # the fuller card is kept
    assert server.store[BOOK + "bob.vcf"] == bob  # unchanged, so not uploaded again
    assert not [r for r in server.requests if r.startswith("PUT") and "bob" in r]
    assert len(parse_text((tmp_path / "contacts_backup.vcf").read_text(encoding="utf-8"))) == 3


def test_dry_run_changes_nothing(server):
    before = dict(server.store)
    result = run("dedupe", server.url, "--username", "ann", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "1 groups, 2 cards would be merged" in result.stderr
    assert server.store == before


def test_strip_on_server(server):
    result = run("strip", server.url, "--username", "ann", "--field", "TEL")
    assert result.exit_code == 0, result.output
    assert "Sending 1 changes to the server" in result.stderr
    assert "[1/1] updated " in result.stderr
    assert "Server: 1 contacts updated, 0 added, 0 deleted" in result.stderr
    assert "TEL" not in server.store[BOOK + "ann2.vcf"][1]


def test_info_on_server(server):
    before = dict(server.store)
    result = run("info", server.url, "--username", "ann")
    assert result.exit_code == 0, result.output
    assert "Read 3 contacts" in result.stderr
    assert "Cards:      3 " in result.stdout
    assert server.store == before
    assert not [r for r in server.requests if r.startswith(("PUT", "DELETE"))]


def test_validate_on_server(server):
    server.put("bad.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:bad\r\nEMAIL:x@x.example\r\nEND:VCARD\r\n")
    result = run("validate", server.url, "--username", "ann")
    assert result.exit_code == 1, result.output
    assert f"{BOOK}bad.vcf:" in result.stdout


def test_wrong_password(server):
    result = run("clean", server.url, "--username", "ann", password="wrong")
    assert result.exit_code == 1
    assert "wrong username or password" in result.output


def test_url_needs_username_and_no_other_input(server, google_v30):
    assert run("clean", server.url).exit_code == 2
    assert run("clean", google_v30, "--username", "ann").exit_code == 2
    assert run("clean", google_v30, server.url, "--username", "ann").exit_code == 2
    assert run("info").exit_code == 2
    assert run("validate", google_v30, server.url, "--username", "ann").exit_code == 2


def test_not_an_address_book(server):
    with pytest.raises(CardDAVError, match="not a CardDAV address book"):
        AddressBook(server.url + "ann.vcf", "ann", "secret").fetch()


def test_changed_meanwhile_is_skipped(server):
    book = AddressBook(server.url, "ann", "secret")
    resources = book.fetch()
    server.put("bob.vcf", vcard("bob", "FN:Robert\r\n"))  # someone else edits Bob
    cards = [c for r in resources[:2] for c in r.cards]
    cards += parse_text(vcard("bob", "FN:Bobby\r\n"), source=resources[2].href)
    reported = []
    failures = book.apply(plan_changes(resources, cards), lambda label, ok: reported.append((label, ok)))
    assert len(failures) == 1 and "changed on the server meanwhile" in failures[0]
    assert ("updated Bobby", False) in reported
    assert "Robert" in server.store[BOOK + "bob.vcf"][1]


def test_plan_changes():
    text = vcard("a", "FN:A\r\n")
    resources = [Resource(f"/b/{n}.vcf", '"1"', text, parse_text(text, source=f"/b/{n}.vcf")) for n in "xyz"]
    cards = [r.cards[0] for r in resources]
    changed = parse_text(vcard("a", "FN:B\r\n"), source="/b/y.vcf")[0]
    new = parse_text(vcard("n", "FN:N\r\n"))[0]
    changes = plan_changes(resources, [cards[0], changed, new])
    assert [r.href for r, _ in changes.updates] == ["/b/y.vcf"]
    assert [r.href for r in changes.deletes] == ["/b/z.vcf"]
    assert len(changes.creates) == 1 and "FN:N" in changes.creates[0]
