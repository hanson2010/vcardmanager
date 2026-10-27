import pytest

from vcardmanager import parse_text
from vcardmanager.parser import parse_property


def one(text):
    cards = parse_text(text.replace("\n", "\r\n"))
    assert len(cards) == 1
    return cards[0]


# ---------------------------------------------------------------- vCard 2.1
def test_v21_quoted_printable_with_soft_breaks_and_charset():
    card = one(
        "BEGIN:VCARD\n"
        "VERSION:2.1\n"
        "N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E7=8E=8B;=E5=B0=8F=E6=98=8E\n"
        "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E7=8E=8B=E5=B0=8F=\n"
        "=E6=98=8E\n"
        "NOTE;QUOTED-PRINTABLE:first=0D=0Asecond, third\n"
        "END:VCARD\n"
    )
    assert card.source_version == "2.1"
    assert card.get("N").components == ["王", "小明"]
    assert card.get("FN").value == "王小明"
    note = card.get("NOTE")
    assert note.value == "first\nsecond, third"
    assert note.raw == "first\\nsecond\\, third"  # stored in 3.0 form; the 2.1 comma was literal
    assert note.params == {}  # ENCODING and CHARSET are consumed by decoding


def test_v21_bare_parameters_become_type():
    card = one("BEGIN:VCARD\nVERSION:2.1\nFN:x\nTEL;CELL;HOME;PREF:123\nEMAIL;INTERNET:a@b.example\nEND:VCARD\n")
    assert card.get("TEL").params == {"TYPE": ["CELL", "HOME", "PREF"]}
    assert card.get("EMAIL").params == {"TYPE": ["INTERNET"]}


def test_v21_base64_photo_with_folded_lines():
    card = one("BEGIN:VCARD\nVERSION:2.1\nFN:x\nPHOTO;JPEG;ENCODING=BASE64:/9j/4AAQ\n  SkZJRgAB\n\nEND:VCARD\n")
    photo = card.get("PHOTO")
    assert photo.raw == "/9j/4AAQSkZJRgAB"
    assert photo.params == {"TYPE": ["JPEG"], "ENCODING": ["b"]}


def test_v21_escaped_semicolon_in_structured_value():
    card = one("BEGIN:VCARD\nVERSION:2.1\nFN:x\nORG:A\\;B;Sales\nEND:VCARD\n")
    assert card.get("ORG").components == ["A;B", "Sales"]


# ---------------------------------------------------------------- vCard 3.0 / 4.0
def test_v30_folding_tab_and_space():
    card = one("BEGIN:VCARD\nVERSION:3.0\nFN:Jo\n hn\nNOTE:a\n\tb\nEND:VCARD\n")
    assert card.get("FN").value == "John"
    assert card.get("NOTE").value == "ab"


def test_quoted_parameter_with_colon_and_semicolon():
    prop = parse_property('ADR;LABEL="1 Main St; Room 2: Hall";TYPE=work,home:;;1 Main St;;;;')
    assert prop.params == {"LABEL": ["1 Main St; Room 2: Hall"], "TYPE": ["work", "home"]}
    assert prop.components == ["", "", "1 Main St", "", "", "", ""]


def test_property_group_and_name_case():
    prop = parse_property("item1.x-ablabel:_$!<Mobile>!$_")
    assert (prop.group, prop.name, prop.value) == ("item1", "X-ABLABEL", "_$!<Mobile>!$_")


def test_property_without_colon_is_rejected():
    with pytest.raises(ValueError):
        parse_property("NOTE just text")


def test_crlf_lf_and_cr_line_endings():
    for sep in ("\r\n", "\n", "\r"):
        cards = parse_text(sep.join(["BEGIN:VCARD", "VERSION:3.0", "FN:A", "END:VCARD", ""]))
        assert [c.get("FN").value for c in cards] == ["A"]


def test_version_line_is_not_a_property():
    card = one("BEGIN:VCARD\nVERSION:4.0\nFN:x\nEND:VCARD\n")
    assert card.source_version == "4.0"
    assert card.get("VERSION") is None


# ---------------------------------------------------------------- broken files
def test_missing_end_closes_the_card():
    cards = parse_text("BEGIN:VCARD\nVERSION:3.0\nFN:A\nBEGIN:VCARD\nVERSION:3.0\nFN:B\n")
    assert [c.get("FN").value for c in cards] == ["A", "B"]
    assert all("missing END:VCARD" in c.warnings for c in cards)


def test_outer_wrapper_gives_version_to_inner_cards():
    cards = parse_text("BEGIN:VCARD\nVERSION:2.1\nBEGIN:VCARD\nFN:A\nEND:VCARD\nEND:VCARD\n")
    assert len(cards) == 1
    assert cards[0].source_version == "2.1"
    assert cards[0].warnings == []


def test_missing_version_assumes_30():
    card = one("BEGIN:VCARD\nFN:A\nEND:VCARD\n")
    assert card.source_version == "3.0"
    assert "missing VERSION, assumed 3.0" in card.warnings


def test_line_broken_without_folding_is_joined():
    card = one("BEGIN:VCARD\nVERSION:3.0\nFN:A\nNOTE:first line\nsecond line\nEND:VCARD\n")
    assert card.get("NOTE").value == "first line\nsecond line"
    assert any("without folding" in w for w in card.warnings)


def test_text_outside_cards_is_ignored():
    cards = parse_text("garbage\nBEGIN:VCARD\nVERSION:3.0\nFN:A\nEND:VCARD\ntrailing\n")
    assert len(cards) == 1


def test_source_records_file_and_line():
    cards = parse_text("\nBEGIN:VCARD\nVERSION:3.0\nFN:A\nEND:VCARD\n", source="x.vcf")
    assert cards[0].source == "x.vcf:2"


def test_kind_detects_group_cards():
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nKIND:group\nFN:G1\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nX-ADDRESSBOOKSERVER-KIND:group\nFN:G2\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Person\nEND:VCARD\n"
    )
    assert [c.kind for c in parse_text(text)] == ["group", "group", "individual"]
