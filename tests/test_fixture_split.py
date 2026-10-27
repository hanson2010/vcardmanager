"""Group membership and `split --by group` on the real-world fixtures."""

from conftest import uid

import vcardmanager
from vcardmanager.groups import group_members


def member_uids(groups):
    return {name: {uid(c) for c in cards} for name, cards in groups.items()}


def test_legacy_groups_union_of_members_and_grouping(legacy_v21_gbk):
    groups = member_uids(group_members(vcardmanager.read_vcards(legacy_v21_gbk)))
    assert {name: len(m) for name, m in groups.items()} == {
        "work-team-a": 17,
        "work-branches": 7,
        "work-rd": 5,
        "work-ops": 2,
        "work-intl": 16,
        "work-hq": 6,
        "work-vendor-c": 3,
    }
    # listed only through the contact's `Grouping:` property, not in X-MEMBERS
    assert groups["work-ops"] == {"2", "153"}
    assert groups["work-vendor-c"] == {"203", "204", "205"}
    assert {"125", "128", "143", "166", "206"} <= groups["work-intl"]
    assert "202" in groups["work-team-a"]
    assert "198" in groups["work-rd"]
    assert "200" in groups["work-hq"]


def test_legacy_ungrouped_contacts(legacy_v21_gbk):
    cards = vcardmanager.read_vcards(legacy_v21_gbk)
    grouped = set().union(*member_uids(group_members(cards)).values())
    contacts = {uid(c) for c in cards if c.kind != "group"}
    assert len(contacts - grouped) == 16


def test_google_categories_as_groups(google_v30):
    groups = group_members(vcardmanager.read_vcards(google_v30))
    assert sorted(c.get("FN").value for c in groups["work-ops"]) == sorted(
        ["孙美婷", "董国/Ops", "单位-公司-值班室", "薛芳晨", "萧清强"]
    )
    assert len(groups["myContacts"]) == 55


def test_same_group_name_across_formats_is_one_group(google_v30, legacy_v21_gbk):
    groups = group_members(vcardmanager.read_vcards([google_v30, legacy_v21_gbk]))
    assert len(groups["work-ops"]) == 5 + 2
