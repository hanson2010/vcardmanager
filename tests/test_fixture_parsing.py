"""Parser behaviour on the real-world fixtures."""

from conftest import by_fn, by_uid

import vcardmanager


# ---------------------------------------------------------------- google_v30.vcf
def test_google_reads_all_contacts(google_v30):
    cards = vcardmanager.read_vcards(google_v30)
    assert len(cards) == 55
    assert {c.source_version for c in cards} == {"3.0"}


def test_google_folded_photo_note_is_unfolded(google_v30):
    card = by_fn(vcardmanager.read_vcards(google_v30))["宋平国"]
    note = card.get("NOTE").value
    photo = card.get("PHOTO").value
    # `https\\://` in the file unescapes to `https\://`; the fold must leave no whitespace behind
    assert note == "Photo: " + photo.replace("https://", "https\\://")


def test_google_property_groups_and_labels(google_v30):
    card = by_fn(vcardmanager.read_vcards(google_v30))["宋平国"]
    emails = card.get_all("EMAIL")
    assert [e.group for e in emails] == [None, "item1", None, "item2"]
    assert [p.group for p in card.get_all("X-ABLABEL")] == ["item1", "item2"]


def test_google_empty_value_is_kept(google_v30):
    card = by_fn(vcardmanager.read_vcards(google_v30))["Faye 徐飞杰/CoY"]
    label = card.get("X-ABLABEL")
    assert label.group == "item1"
    assert label.value == ""


def test_google_escaped_newline_in_note(google_v30):
    card = by_fn(vcardmanager.read_vcards(google_v30))["Faye 徐飞杰/CoY"]
    assert card.get("NOTE").value == "公司A\n某云计算OLAP首席架构师（19级）"


def test_google_structured_name_components(google_v30):
    cards = by_fn(vcardmanager.read_vcards(google_v30))
    assert cards["张伟诚"].get("N").components == ["张", "诚", "伟", "", ""]
    assert cards["刘南/Ops 安质"].get("N").components == ["刘南/Ops", "安质", "", "", ""]
    assert cards["Holly 王月思/ASN"].get("N").components == ["", "Holly 王月思", "", "", "/ASN"]


def test_google_parameters_kept_as_written(google_v30):
    cards = by_fn(vcardmanager.read_vcards(google_v30))
    assert cards["沈云/APA"].get("EMAIL").params["TYPE"] == ["INTERNET", "WORK"]
    # the parser does not normalize case; that is the clean rule `type-normalize`
    assert cards["曹山成/XA"].get("TEL").params["TYPE"] == ["cell"]


def test_google_categories(google_v30):
    card = by_fn(vcardmanager.read_vcards(google_v30))["王明/IT 香港"]
    assert card.get("CATEGORIES").components == ["hongkong", "myContacts", "work-intl"]


def test_google_round_trip(google_v30, tmp_path):
    cards = vcardmanager.read_vcards(google_v30)
    out = tmp_path / "out.vcf"
    vcardmanager.write_vcards(cards, out, version="3.0")
    assert vcardmanager.read_vcards(out) == cards


# ---------------------------------------------------------------- legacy_v21_gbk.vcf
def test_legacy_outer_wrapper_is_dropped(legacy_v21_gbk):
    cards = vcardmanager.read_vcards(legacy_v21_gbk)
    # 72 contacts + 7 group cards; the empty outer wrapper is not a contact
    assert len(cards) == 79
    assert sum(c.kind == "group" for c in cards) == 7


def test_legacy_inner_cards_inherit_wrapper_version(legacy_v21_gbk):
    cards = vcardmanager.read_vcards(legacy_v21_gbk)
    assert {c.source_version for c in cards} == {"2.1"}


def test_legacy_gbk_detected_without_charset(legacy_v21_gbk):
    cards = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))
    assert cards["2"].get("FN").value == "彭文子"
    assert cards["142"].get("FN").value == "卢帆雪 Miles"
    assert cards["111"].get("FN").value == "赵安雪/后勤"


def test_legacy_property_names_uppercased(legacy_v21_gbk):
    card = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))["1"]
    assert card.get("REV").value == "113"
    assert card.get("GROUPING").value == "db3b1dfc-ce1b-46f5-8ba9-886d1b9593b6"


def test_legacy_odd_parameter_is_tolerated(legacy_v21_gbk):
    card = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))["72"]
    assert [e.value for e in card.get_all("EMAIL")] == [
        "tangruidong@hk.airtech.example",
        "tangruidong@cn.airtech.example",
    ]


def test_legacy_bare_type_parameters(legacy_v21_gbk):
    card = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))["211"]
    tel = card.get("TEL")
    assert tel.value == "15376400148"
    assert tel.params["TYPE"] == ["CELL", "PREF"]


def test_legacy_card_without_fn_is_kept_with_warning(legacy_v21_gbk):
    card = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))["195"]
    assert card.get("FN") is None
    assert card.warnings


def test_legacy_group_card(legacy_v21_gbk):
    group = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))["c2e018ad-0bca-4e99-9dd0-a9b290e6d93a"]
    assert group.kind == "group"
    assert group.get("N").value == "work-ops"
    assert group.get("X-MEMBERS").value == "2"
