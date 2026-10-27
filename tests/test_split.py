import pytest

from vcardmanager import parse_text, read_vcards
from vcardmanager.io import OutputError
from vcardmanager.groups import group_members
from vcardmanager.split import parse_size, split_cards, write_split


def cards_named(*names):
    return parse_text("".join(f"BEGIN:VCARD\nVERSION:3.0\nFN:{n}\nUID:{n}\nEND:VCARD\n" for n in names))


@pytest.mark.parametrize(
    ("text", "expected"), [("1024", 1024), ("500KB", 512000), ("1MB", 1048576), ("1.5 mb", 1572864), ("2GiB", 2 << 30)]
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_parse_size_rejects_garbage():
    with pytest.raises(ValueError):
        parse_size("big")


def test_per_file_one_uses_fn_and_handles_collisions():
    chunks = split_cards(cards_named("Ann", "ann", "Bob/HR"), per_file=1)
    assert [stem for stem, _ in chunks] == ["Ann", "ann_2", "Bob_HR"]


def test_per_file_chunks():
    chunks = split_cards(cards_named(*"abcdefghijk"), per_file=5)
    assert [(stem, len(c)) for stem, c in chunks] == [("contacts-1", 5), ("contacts-2", 5), ("contacts-3", 1)]


def test_max_size_chunks():
    cards = cards_named(*"abcdef")  # each card is 50 bytes as 3.0
    chunks = split_cards(cards, max_bytes=110)
    assert [len(c) for _, c in chunks] == [2, 2, 2]
    assert [len(c) for _, c in split_cards(cards, max_bytes=10)] == [1] * 6  # oversized cards get their own file


def test_exactly_one_mode():
    with pytest.raises(ValueError):
        split_cards([], per_file=1, by="group")
    with pytest.raises(ValueError):
        split_cards([])


def test_by_property():
    cards = parse_text(
        "BEGIN:VCARD\nVERSION:3.0\nFN:a\nORG:Acme;Sales\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:b\nORG:Acme;IT\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:c\nEND:VCARD\n"
    )
    assert [(stem, len(c)) for stem, c in split_cards(cards, by="ORG")] == [("Acme", 2), ("no-org", 1)]


def test_group_formats_vcard40_and_icloud():
    cards = parse_text(
        "BEGIN:VCARD\nVERSION:4.0\nKIND:group\nFN:Team\nMEMBER:urn:uuid:A1\nMEMBER:urn:uuid:missing\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nX-ADDRESSBOOKSERVER-KIND:group\nN:Family\nFN:Family\n"
        "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:b2\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nFN:Ann\nUID:urn:uuid:a1\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Bob\nUID:B2\nCATEGORIES:Team\nEND:VCARD\n"
    )
    groups = {name: [c.get("FN").value for c in members] for name, members in group_members(cards).items()}
    assert groups == {"Team": ["Ann", "Bob"], "Family": ["Bob"]}


def test_by_group_writes_group_cards_first():
    cards = parse_text(
        "BEGIN:VCARD\nVERSION:4.0\nKIND:group\nFN:Empty\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nKIND:group\nFN:Team\nMEMBER:urn:uuid:a\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Ann\nUID:a\nCATEGORIES:Friends\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Bob\nEND:VCARD\n"
    )
    chunks = split_cards(cards, by="group")
    assert [(stem, [c.get("FN").value for c in members]) for stem, members in chunks] == [
        ("Empty", ["Empty"]),  # a group without members keeps its card
        ("Team", ["Team", "Ann"]),
        ("Friends", ["Ann"]),  # CATEGORIES groups have no group card
        ("ungrouped", ["Bob"]),
    ]


def test_write_split_refuses_existing_files(tmp_path):
    chunks = split_cards(cards_named("Ann"), per_file=1)
    paths = write_split(chunks, tmp_path / "out")
    assert [p.name for p in paths] == ["Ann.vcf"]
    with pytest.raises(OutputError):
        write_split(chunks, tmp_path / "out")
    write_split(chunks, tmp_path / "out", force=True)
    assert read_vcards(paths[0])[0].get("FN").value == "Ann"
