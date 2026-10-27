import pytest
from click.testing import CliRunner

from vcardmanager import parse_text, read_vcards
from vcardmanager.cli import main
from vcardmanager.strip import FieldPattern, needs_group_conversion, parse_patterns, strip_card

CARD = parse_text(
    "BEGIN:VCARD\nVERSION:3.0\nFN:Ann\nN:Lee;Ann;;;\nTEL;TYPE=CELL:1\nTEL;TYPE=WORK,FAX:2\n"
    "item1.EMAIL:a@b.example\nitem1.X-ABLABEL:Work\nitem2.URL:https://x.example\nitem2.X-ABLABEL:Site\n"
    "item2.X-ABDATE:2020\nPHOTO:https://p.example\nNOTE:hi\nEND:VCARD\n"
)[0]


def names(card):
    return [p.name for p in card.properties]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("photo", FieldPattern("PHOTO")),
        ("X-*", FieldPattern("X-*")),
        ("TEL[TYPE=fax]", FieldPattern("TEL", (("TYPE", "FAX"),))),
        ("TEL[TYPE=WORK][PREF=1]", FieldPattern("TEL", (("TYPE", "WORK"), ("PREF", "1")))),
    ],
)
def test_parse_pattern(text, expected):
    assert FieldPattern.parse(text) == expected


@pytest.mark.parametrize("bad", ["", "TEL[TYPE]", "TEL[TYPE=FAX", "[TYPE=FAX]"])
def test_parse_pattern_rejects(bad):
    with pytest.raises(ValueError):
        FieldPattern.parse(bad)


def test_parse_patterns_splits_commas():
    assert parse_patterns(["FN,N", " TEL "]) == [FieldPattern("FN"), FieldPattern("N"), FieldPattern("TEL")]


def test_strip_fields_with_glob_and_param_condition():
    stripped, removed = strip_card(CARD, parse_patterns(["PHOTO", "TEL[TYPE=FAX]"]))
    assert removed == 2
    assert [t.value for t in stripped.get_all("TEL")] == ["1"]
    assert stripped.get("PHOTO") is None
    assert CARD.get("PHOTO") is not None  # the input card is not changed


def test_strip_x_removes_orphaned_property_groups():
    stripped, _ = strip_card(CARD, parse_patterns(["X-*"]))
    assert names(stripped) == ["FN", "N", "TEL", "TEL", "EMAIL", "URL", "PHOTO", "NOTE"]
    assert stripped.get("EMAIL").group is None
    assert stripped.get("URL").group is None


def test_shared_group_prefix_is_kept():
    stripped, _ = strip_card(CARD, parse_patterns(["X-ABDATE"]))
    assert {(p.name, p.group) for p in stripped.properties if p.group} == {
        ("EMAIL", "item1"),
        ("X-ABLABEL", "item1"),
        ("URL", "item2"),
        ("X-ABLABEL", "item2"),
    }


def test_keep_list_always_keeps_fn():
    stripped, removed = strip_card(CARD, keep=parse_patterns(["TEL,EMAIL"]))
    assert names(stripped) == ["FN", "TEL", "TEL", "EMAIL"]
    assert removed == 7  # 11 properties, 4 kept


def test_keep_and_fields_combine():
    stripped, _ = strip_card(CARD, parse_patterns(["TEL[TYPE=FAX]"]), keep=parse_patterns(["TEL"]))
    assert names(stripped) == ["FN", "TEL"]


def run(*args):
    return CliRunner().invoke(main, ["strip", *map(str, args)])


def test_cli_preset_privacy(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    result = run(google_v30, "-o", out, "--preset", "privacy")
    assert result.exit_code == 0
    cards = read_vcards(out)
    assert len(cards) == 55
    left = {p.name for c in cards for p in c.properties}
    assert not left & {"PHOTO", "NOTE"}
    assert not [n for n in left if n.startswith("X-")]
    assert "Removed" in result.stderr


@pytest.mark.parametrize(
    ("fields", "keep", "expected"),
    [
        (["X-*"], None, True),  # removes X-TYPE / X-MEMBERS / X-ADDRESSBOOKSERVER-*
        (["GROUPING"], None, True),
        (["MEMBER"], None, True),
        (["PHOTO", "NOTE"], None, False),
        (["X-*", "CATEGORIES"], None, False),  # nowhere to keep the membership
        ([], ["FN,N,TEL,EMAIL"], False),  # minimal: CATEGORIES is not kept either
        ([], ["FN,N,TEL,EMAIL,CATEGORIES"], True),
    ],
)
def test_needs_group_conversion(fields, keep, expected):
    keep_patterns = parse_patterns(keep) if keep is not None else None
    assert needs_group_conversion(parse_patterns(fields), keep_patterns) is expected


def test_cli_strip_x_keeps_group_membership(legacy_v21_gbk, tmp_path):
    out = tmp_path / "out.vcf"
    result = run(legacy_v21_gbk, "-o", out, "--field", "X-*")
    assert result.exit_code == 0, result.output
    assert "no longer group cards" not in result.stderr
    assert "Moved the membership of 7 group cards into CATEGORIES" in result.stderr
    assert "wrote 72 of 79 cards" in result.stderr
    cards = {c.get("UID").value: c for c in read_vcards(out)}
    assert len(cards) == 72  # the 7 group cards are gone; their membership is in the contacts
    assert "work-ops" in cards["153"].get("CATEGORIES").components  # listed only through GROUPING
    assert "work-vendor-c" in cards["203"].get("CATEGORIES").components  # its group had no X-MEMBERS
    assert not {p.name for c in cards.values() for p in c.properties} & {"GROUPING", "X-TYPE", "X-MEMBERS"}


def test_cli_minimal_warns_about_group_cards(legacy_v21_gbk, tmp_path):
    out = tmp_path / "out.vcf"
    result = run(legacy_v21_gbk, "-o", out, "--preset", "minimal")
    assert result.exit_code == 0
    assert "7 group cards are no longer group cards" in result.stderr
    assert {p.name for c in read_vcards(out) for p in c.properties} <= {"FN", "N", "TEL", "EMAIL"}


def test_cli_needs_something_to_strip(google_v30, tmp_path):
    assert run(google_v30, "-o", tmp_path / "out.vcf").exit_code == 2
    assert run(google_v30, "-o", tmp_path / "out.vcf", "--field", "TEL[TYPE").exit_code == 2
