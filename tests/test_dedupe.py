import csv

import pytest
from click.testing import CliRunner

from vcardmanager import parse_text, read_vcards
from vcardmanager.cli import main
from vcardmanager.dedupe import apply_merges, find_duplicate_groups, find_duplicates, merge_group
from vcardmanager.keys import email_key, name_key, phone_key


def cards(*bodies):
    return parse_text("".join(f"BEGIN:VCARD\nVERSION:3.0\n{body}END:VCARD\n" for body in bodies))


def fns(groups):
    return [[c.get("FN").value for c in group] for group in groups]


# ---------------------------------------------------------------- keys
@pytest.mark.parametrize(
    "number", ["+86 138 0013 8000", "0086 13800138000", "13800138000", "138-0013-8000", "tel:+8613800138000"]
)
def test_phone_key_equal_forms(number):
    assert phone_key(number) == "13800138000"


def test_phone_key_trunk_prefix_and_short_numbers():
    assert phone_key("010 8531 0330") == phone_key("+86 10 8531 0330") == "1085310330"
    assert phone_key("95338") is None
    assert phone_key("") is None


def test_email_and_name_keys():
    assert email_key('"Ann" <Ann@X.example>') == "ann@x.example"
    assert email_key("not an address") is None
    (ann,) = cards("FN:  Ann   LEE\n")
    assert name_key(ann) == "ann lee"
    (lee,) = cards("FN:Lee Ann\n")
    assert name_key(lee) == name_key(ann)  # word order does not matter
    for fn in ("ann@x.example", "+1 650 253 0000", ""):
        assert name_key(cards(f"FN:{fn}\n")[0]) is None


# ---------------------------------------------------------------- grouping
def test_transitive_groups_and_reasons():
    found = cards(
        "FN:Ann\nTEL:+1 650 253 0000\n",
        "FN:A. Lee\nTEL:6502530000\nEMAIL:ann@x.example\n",
        "FN:Someone\n",
        "FN:Annie\nEMAIL:ANN@x.example\n",
    )
    (group,) = find_duplicate_groups(found, region="US")
    assert [c.get("FN").value for c in group.cards] == ["Ann", "A. Lee", "Annie"]
    assert group.reasons == {"phone", "email"}


def test_match_kinds_and_ignored_phones():
    found = cards("FN:Ann\nTEL:+1 650 253 0000\n", "FN:Bob\nTEL:+1 650 253 0000\n")
    assert fns(find_duplicates(found)) == [["Ann", "Bob"]]
    assert find_duplicates(found, ["email", "name"]) == []
    assert find_duplicates(found, ignore_phones=["(650) 253-0000"], region="US") == []
    with pytest.raises(ValueError, match="unknown match"):
        find_duplicates(found, ["birthday"])


def test_shared_hotline_does_not_link():
    assert find_duplicates(cards("FN:Ann\nTEL:95338\n", "FN:Bob\nTEL:95338\n")) == []


@pytest.mark.parametrize(
    ("han", "latin"),
    [("陈磊", "Lei Chen"), ("陈磊", "CHEN Lei"), ("张伟诚", "Wei-Cheng Zhang"), ("欧阳娜娜", "Nana Ouyang"),
     ("单雪", "Xue Shan"), ("吕方", "Fang Lv"), ("陈磊/HR", "Lei Chen")],
)  # fmt: skip
def test_pinyin_links_chinese_and_latin_names(han, latin):
    found = find_duplicates(cards(f"FN:{han}\n", f"FN:{latin}\n"), match=("pinyin",))
    assert fns(found) == [[han, latin]]


@pytest.mark.parametrize(
    "bodies",
    [
        ("FN:陈磊\n", "FN:陈雷\n", "FN:Lei Chen\n"),  # which one is Lei Chen? link nobody
        ("FN:陈磊\n", "FN:陈蕾\n"),  # two Chinese names: pinyin only links Chinese to Latin
        ("FN:Lei Chen\n", "FN:Chen Lei\n"),
        ("FN:陈磊\n", "FN:Lei Chen Smith Jones Brown\n"),
        ("FN:单位-快递\n", "FN:Dan Wei\n"),
    ],
)
def test_pinyin_does_not_link(bodies):
    assert find_duplicates(cards(*bodies), match=("pinyin",)) == []


def test_pinyin_is_not_a_default_match():
    assert find_duplicates(cards("FN:陈磊\n", "FN:Lei Chen\n")) == []


def test_fuzzy_names():
    found = cards("FN:Jonathan Smith\n", "FN:Jonathon Smith\n", "FN:Ann Lee\n", "FN:Anne Lee\n")
    assert find_duplicates(found) == []
    groups = find_duplicate_groups(found, name_threshold=90)
    assert fns(g.cards for g in groups) == [["Jonathan Smith", "Jonathon Smith"], ["Ann Lee", "Anne Lee"]]
    assert groups[0].reasons == {"similar name"}
    short_names_excluded = find_duplicates(found, name_threshold=90, min_name_length=8)  # "ann lee" has 7
    assert fns(short_names_excluded) == [["Jonathan Smith", "Jonathon Smith"]]


def test_group_cards_are_ignored():
    found = parse_text("BEGIN:VCARD\nVERSION:4.0\nKIND:group\nFN:Team\nEND:VCARD\n" * 2)
    assert find_duplicates(found) == []


# ---------------------------------------------------------------- merging
def test_merge_combines_values_and_records_conflicts():
    a, b = cards(
        "FN:Ann Lee\nN:Lee;Ann;;;\nTEL;TYPE=CELL:+1 650 253 0000\nBDAY:1990-01-01\nCATEGORIES:Friends\nUID:a\n"
        "item1.EMAIL:ann@x.example\nitem1.X-ABLABEL:Home\n",
        "FN:Annie\nTEL:6502530000\nEMAIL:ann@work.example\nBDAY:1991-02-02\nCATEGORIES:Work,Friends\nUID:b\n"
        "item1.URL:https://ann.example\nitem1.X-ABLABEL:Blog\nREV:2020\n",
    )
    merged = merge_group([a, b])
    assert merged.get("FN").value == "Ann Lee"
    assert [p.value for p in merged.get_all("TEL")] == ["+1 650 253 0000"]  # same number, other form
    assert [p.value for p in merged.get_all("EMAIL")] == ["ann@x.example", "ann@work.example"]
    assert merged.get("CATEGORIES").components == ["Friends", "Work"]
    assert [p.value for p in merged.get_all("UID")] == ["a"]
    assert merged.get("BDAY").value == "1990-01-01"  # the base card's value wins
    assert merged.get("NOTE") is None  # losing values are dropped, not recorded
    assert {(p.name, p.group) for p in merged.properties if p.group} == {
        ("EMAIL", "item1"),
        ("X-ABLABEL", "item1"),
        ("URL", "item2"),  # the second card's item1 is renamed, so the labels stay with their values
        ("X-ABLABEL", "item2"),
    }
    assert len(merged.get_all("REV")) == 1
    assert merged.get("REV").value != "2020"


def test_merge_keeps_types_of_repeated_values_and_order():
    a, b = cards(
        "FN:Ann\nTEL;TYPE=CELL:+86 153 7640 0148\nORG:Acme\nBDAY:2000-01-01\nNOTE:old\nUID:a\nREV:2019\n",
        "FN:Annie\nTEL;TYPE=CELL,PREF:15376400148\nEMAIL:a@x.example\nNOTE:new\n",
    )
    merged = merge_group([a, b])  # the first card is more complete, so it is the base
    assert merged.get("TEL").params["TYPE"] == ["CELL", "PREF"]
    names = [p.name for p in merged.properties]
    # EMAIL is new: it goes before NOTE, the first property that sort-properties puts after EMAIL
    assert names == ["FN", "TEL", "ORG", "BDAY", "EMAIL", "NOTE", "NOTE", "UID", "REV"]
    assert [p.value for p in merged.get_all("NOTE")] == ["old", "new"]  # NOTEs are combined, like other values


def merged_name(*bodies):
    merged = merge_group(cards(*bodies))
    n = merged.get("N")
    return merged.get("FN").value, n.components if n is not None else None


def test_best_name_contains_the_others():
    # the second card is more complete, but the first name also has the department label
    assert merged_name("FN:宋平国/CS\nN:;宋平国/CS;;;\n", "FN:宋平国\nN:宋;平国;;;\nTEL:1234567\nEMAIL:s@x.example\n") == (
        "宋平国/CS",
        ["宋", "平国", "", "", "/CS"],
    )
    assert merged_name("FN:杨伟云\nN:杨;伟云;;;\nTEL:1234567\n", "FN:Joy 杨伟云\n")[0] == "杨伟云 Joy"


def test_best_name_prefers_richer_names_over_handles():
    assert merged_name(
        "FN:xiaoqingqiang\nN:;xiaoqingqiang;;;\nEMAIL:xiaoqingqiang@x.example\nTEL:1234567\nNOTE:n\n",
        "FN:萧清强\nN:萧;清强;;;\n",
    ) == ("萧清强", ["萧", "清强", "", "", ""])
    assert merged_name("FN:Joy Yang\nN:Yang;Joy;;;\nTEL:1234567\n", "FN:Joy 杨伟云\n") == (
        "杨伟云 Joy",
        ["杨", "伟云 Joy", "", "", ""],
    )
    assert merged_name("FN:ann@x.example\nN:;ann;;;\nTEL:1234567\n", "FN:Ann Lee\n")[0] == "Ann Lee"


def test_best_name_keeps_the_base_label():
    base = "FN:宋平国/IT\nN:宋;平国;;;/IT\nTEL:1234567\nEMAIL:s@x.example\n"
    assert merged_name(base, "FN:宋平国/CS\n")[0] == "宋平国/IT"


def test_best_name_most_common_then_first():
    # the base (most complete) card has a typo; the name on more cards wins
    assert merged_name("FN:林搏天\nTEL:1234567\nEMAIL:l@x.example\n", "FN:林博天\n", "FN:林博天\n")[0] == "林博天"
    assert merged_name("FN:林搏天\n", "FN:林博天\nTEL:1234567\n")[0] == "林搏天"  # a tie: the first card


def test_best_name_keeps_organisation_n():
    assert merged_name("FN:单位-快递-速递\nN:;单位-快递-速递;;;\n", "FN:单位-快递-速递/客服\nTEL:1234567\n") == (
        "单位-快递-速递/客服",
        ["", "单位-快递-速递", "", "", ""],
    )


def test_best_name_does_not_add_n_or_replace_non_names():
    assert merged_name("FN:Ann\nTEL:1234567\n", "FN:Ann\n") == ("Ann", None)  # no card had N
    assert merged_name("FN:+1 650 253 0000\nTEL:1234567\n", "FN:a@x.example\n")[0] == "+1 650 253 0000"


def test_merge_does_not_change_inputs():
    a, b = cards("FN:Ann\nTEL:1234567\n", "FN:Ann\nTEL:7654321\n")
    merge_group([a, b])
    assert len(a.get_all("TEL")) == 1


def test_apply_merges_keeps_order():
    found = cards("FN:A\n", "FN:B\nEMAIL:shared@x.example\n", "FN:C\n", "FN:D\nEMAIL:shared@x.example\n")
    result = apply_merges(found, find_duplicates(found))
    assert [c.get("FN").value for c in result] == ["A", "B", "C"]
    assert len(result[1].get_all("EMAIL")) == 1


# ---------------------------------------------------------------- CLI
def run(*args, input=None):
    return CliRunner().invoke(main, ["dedupe", *map(str, args)], input=input)


def merged_fixtures(google_v30, legacy_v21_gbk, copy_of):
    """Both fixtures merged into one file: some people are in both."""
    first = copy_of(google_v30)
    assert CliRunner().invoke(main, ["merge", str(first), str(copy_of(legacy_v21_gbk)), "-q"]).exit_code == 0
    return first.with_name("google_v30_merged.vcf")


def test_cli_dry_run_writes_nothing(google_v30, legacy_v21_gbk, copy_of):
    both = merged_fixtures(google_v30, legacy_v21_gbk, copy_of)
    before = both.read_bytes()
    result = run(both, "--dry-run")
    assert result.exit_code == 0
    assert result.stdout.count("Group ") == 7
    assert "7 groups, 15 cards would be merged" in result.stderr
    assert both.read_bytes() == before


def test_cli_merge_and_report(google_v30, legacy_v21_gbk, copy_of, tmp_path):
    both, report = merged_fixtures(google_v30, legacy_v21_gbk, copy_of), tmp_path / "report.csv"
    result = run(both, "--report", report)
    assert result.exit_code == 0, result.output
    assert len(read_vcards(both)) == 55 + 79 - 15 + 7
    rows = list(csv.DictReader(report.open(encoding="utf-8-sig")))
    assert len(rows) == 15
    assert {row["group"] for row in rows} == {str(n) for n in range(1, 8)}


def test_cli_interactive(google_v30, legacy_v21_gbk, copy_of):
    both = merged_fixtures(google_v30, legacy_v21_gbk, copy_of)
    result = run(both, "-i", input="y\n" + "n\n" * 6)
    assert result.exit_code == 0
    assert "Merged 2 cards into 1" in result.stderr  # only the first group: 蔡思 from each file


def test_cli_files_are_deduplicated_separately(google_v30, legacy_v21_gbk):
    result = run(google_v30, legacy_v21_gbk, "--dry-run")
    assert result.exit_code == 0
    assert "蔡思" not in result.stdout  # in both files, but once in each


def test_cli_match_option(google_v30, legacy_v21_gbk, copy_of):
    result = run(merged_fixtures(google_v30, legacy_v21_gbk, copy_of), "--dry-run", "--match", "email")
    assert result.stdout.count("Group ") == 5
    assert run(google_v30, "--dry-run", "--match", "shoe size").exit_code == 2
