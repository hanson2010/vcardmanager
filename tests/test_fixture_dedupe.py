"""`dedupe` on the real-world fixtures."""

from conftest import by_uid, uid

import vcardmanager
from vcardmanager import dedupe

# google_v30.vcf contacts that have a counterpart in legacy_v21_gbk.vcf
GOOGLE_LIN_BOTIAN = "41e9c99f-859a-4553-9eab-6c3f31927874"
GOOGLE_PENG_JIE = "62fdf83f-c3c0-4d0b-b5ef-67db3a770cea"
GOOGLE_CAI_SI = "9498460a-4a32-4d78-a3f8-923d5e4571c7"
GOOGLE_XIAO_QINGQIANG = "589fb1e7-c800-4c7d-9693-dabb74c61526"
GOOGLE_JOY_YANG = "eda7592b-4e6a-4d75-8f40-18b19b2de834"
GOOGLE_SONG_PINGGUO = "6c2e73fd-0e81-4514-be3f-b2078f262787"
GOOGLE_CHEN_LEI = "b88b6bf7-5171-44d5-b29a-203a01dc1a86"


def uid_groups(groups):
    return {frozenset(uid(c) for c in g) for g in groups}


def test_legacy_exact_duplicates(legacy_v21_gbk):
    groups = dedupe.find_duplicates(vcardmanager.read_vcards(legacy_v21_gbk))
    assert uid_groups(groups) == {frozenset({"80", "168"}), frozenset({"30", "180"})}


def test_cross_file_duplicates(google_v30, legacy_v21_gbk):
    cards = vcardmanager.read_vcards([google_v30, legacy_v21_gbk])
    groups = uid_groups(dedupe.find_duplicates(cards))
    assert groups == {
        frozenset({"80", "168"}),  # within legacy: same name + email
        frozenset({"30", "180", GOOGLE_LIN_BOTIAN}),  # name chain across files
        frozenset({"52", GOOGLE_PENG_JIE}),  # same name, different email domains
        frozenset({"211", GOOGLE_CAI_SI}),  # name + phone (13… vs +86 …)
        frozenset({"208", GOOGLE_XIAO_QINGQIANG}),  # email; legacy FN is only the handle
        frozenset({"209", GOOGLE_JOY_YANG}),  # email differs only in case
        frozenset({"210", GOOGLE_SONG_PINGGUO}),  # second email; legacy FN has a suffix
    }


def test_near_duplicates_not_merged_by_default(google_v30, legacy_v21_gbk):
    cards = vcardmanager.read_vcards([google_v30, legacy_v21_gbk])
    grouped = set().union(*uid_groups(dedupe.find_duplicates(cards)))
    # Clyde Mui vs Clyde Mui/HK, Nina Bao/HK vs Nina Bao, 陈磊 vs Lei Chen
    for not_expected in ("62", "87", "74", "101", "205", GOOGLE_CHEN_LEI):
        assert not_expected not in grouped


def test_match_by_email_only(google_v30, legacy_v21_gbk):
    cards = vcardmanager.read_vcards([google_v30, legacy_v21_gbk])
    groups = uid_groups(dedupe.find_duplicates(cards, match=("email",)))
    assert frozenset({"52", GOOGLE_PENG_JIE}) not in groups
    assert frozenset({"208", GOOGLE_XIAO_QINGQIANG}) in groups


def test_group_cards_never_deduplicated(legacy_v21_gbk):
    cards = vcardmanager.read_vcards(legacy_v21_gbk)
    groups = dedupe.find_duplicates(cards, match=("phone", "email", "name"))
    assert all(c.kind != "group" for g in groups for c in g)


def test_merge_keeps_all_values_once(google_v30, legacy_v21_gbk):
    google = by_uid(vcardmanager.read_vcards(google_v30))
    legacy = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))
    merged = dedupe.merge_group([google[GOOGLE_LIN_BOTIAN], legacy["30"], legacy["180"]])
    assert merged.get("FN").value == "林博天"
    assert [t.value for t in merged.get_all("TEL")] == ["+86 15829350427"]
    assert [e.value for e in merged.get_all("EMAIL")] == ["linbotian08@cn.airtech.example"]
    assert len(merged.get_all("UID")) == 1


def test_merge_prefers_real_name_over_handle(google_v30, legacy_v21_gbk):
    google = by_uid(vcardmanager.read_vcards(google_v30))
    legacy = by_uid(vcardmanager.read_vcards(legacy_v21_gbk))
    merged = dedupe.merge_group([legacy["208"], google[GOOGLE_XIAO_QINGQIANG]])
    assert merged.get("FN").value == "萧清强"
    assert {e.value for e in merged.get_all("EMAIL")} == {"xiaoqingqiang@airtech.example"}
