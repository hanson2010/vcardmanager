from vcardmanager import parse_text, read_vcards
from vcardmanager.info import format_summary, summarize
from vcardmanager.validate import ERROR, WARNING, Issue, validate


def issues(body):
    return validate(parse_text(f"BEGIN:VCARD\nVERSION:3.0\n{body}END:VCARD\n")[0])


def test_valid_card_has_no_issues():
    assert issues("FN:Ann\nN:Lee;Ann;;;\nTEL;TYPE=CELL,pref:+1\nEMAIL;TYPE=INTERNET:a@b.example\n") == []


def test_missing_fn_is_an_error():
    assert Issue(ERROR, "missing FN") in issues("N:Lee;Ann;;;\n")


def test_cardinality():
    assert Issue(ERROR, "UID appears 2 times; at most once is allowed") in issues("FN:a\nUID:1\nUID:2\n")


def test_warnings():
    found = issues("FN:a\nN:Lee;Ann\nGROUPING:x\nTEL;TYPE=#1;FOO=bar:1\nKIND:robot\nX-ANY;X-P=1:ok\n")
    assert {i.message for i in found} == {
        "N has 2 components, expected 5",
        "non-standard property GROUPING",
        "TEL: unknown TYPE #1",
        "TEL: non-standard parameter FOO",
        "unknown KIND robot",
    }
    assert all(i.level == WARNING for i in found)


def test_google_fixture_is_valid(google_v30):
    assert [i for card in read_vcards(google_v30) for i in validate(card)] == []


def test_legacy_fixture_errors_are_the_cards_without_fn(legacy_v21_gbk):
    cards = read_vcards(legacy_v21_gbk)
    bad = [card.get("UID").value for card in cards if any(i.level == ERROR for i in validate(card))]
    assert len(bad) == 8  # 7 group cards + UID 195
    assert "195" in bad


def test_summary(legacy_v21_gbk):
    summary = summarize(read_vcards(legacy_v21_gbk))
    assert (summary.cards, summary.individuals, summary.groups) == (79, 72, 7)
    assert summary.versions == {"2.1": 79}
    assert summary.properties["X-MEMBERS"] == 6
    assert summary.warnings == {"missing FN": 8}
    assert "79 (72 contacts, 7 groups)" in format_summary(summary)
