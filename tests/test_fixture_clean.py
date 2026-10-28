"""`clean` rules on the real-world fixtures."""

import base64
import io

import pytest
from conftest import by_fn, by_uid
from PIL import Image

import vcardmanager
import vcardmanager.clean as clean_mod


def cleaned(path, region="CN"):
    return [clean_mod.clean(c, clean_mod.DEFAULT_RULES, region=region) for c in vcardmanager.read_vcards(path)]


@pytest.mark.parametrize(
    ("fn", "raw", "expected"),
    [
        ("韩乐嘉/项目办", "137 1831 2288", "+8613718312288"),  # national mobile with spaces
        ("孙美婷", "+86 13947454933", "+8613947454933"),
        ("孙美婷", "01085310330", "+861085310330"),  # trunk 0 + Beijing area code
        ("单位-公司-值班室", "+8610 89777615", "+861089777615"),
        ("何云/XB", "+86 1055389007", "+861055389007"),
        ("彭嘉晨", "022 2631 5694", "+862226315694"),  # Tianjin landline
        ("Holly 王月思/ASN", "+852 2133 7994", "+85221337994"),  # foreign number keeps its country
    ],
)
def test_phone_e164(google_v30, fn, raw, expected):
    raw_card = by_fn(vcardmanager.read_vcards(google_v30))[fn]
    assert raw in [t.value for t in raw_card.get_all("TEL")]
    card = by_fn(cleaned(google_v30))[{"Holly 王月思/ASN": "王月思 Holly/ASN"}.get(fn, fn)]  # name-split reorders FN
    assert expected in [t.value for t in card.get_all("TEL")]


def test_phone_short_service_number_unchanged(google_v30):
    card = by_fn(cleaned(google_v30))["单位-快递-速递"]
    assert card.get("TEL").value == "95338"
    assert card.warnings == []  # a known service number, not an invalid one


def test_phone_without_region_left_alone(google_v30):
    card = by_fn(cleaned(google_v30, region=None))["韩乐嘉/项目办"]
    assert card.get("TEL").value == "137 1831 2288"


def test_duplicate_email_in_card_removed(google_v30):
    card = by_fn(cleaned(google_v30))["沈云/APA"]
    assert [e.value for e in card.get_all("EMAIL")] == ["shenyun@airport-a.example"]


def test_email_lowercased(google_v30):
    card = by_fn(cleaned(google_v30))["杨伟云 Joy"]
    assert card.get("EMAIL").value == "joy.yang@vendor-d.example"


def test_type_normalized(google_v30):
    card = by_fn(cleaned(google_v30))["曹山成/XA"]
    assert card.get("TEL").params["TYPE"] == ["CELL"]


def test_name_filled_from_email(legacy_v21_gbk):
    card = by_uid(cleaned(legacy_v21_gbk))["195"]
    assert card.get("FN").value == "tzhao"


# ---------------------------------------------------------------- name-label / name-split
@pytest.mark.parametrize(
    ("fn", "expected_n"),
    [
        ("沈云/APA", ["沈", "云", "", "", "/APA"]),  # was N:;沈云/APA;;;
        ("刘南/Ops 安质", ["刘", "南", "", "", "/Ops 安质"]),  # was N:刘南/Ops;安质;;;
        ("叶佳松/HR", ["叶", "佳松", "", "", "/HR"]),  # was N:叶佳松/HR;;;;
        ("王月思 Holly/ASN", ["王", "月思 Holly", "", "", "/ASN"]),  # was FN Holly 王月思/ASN: English word last
        ("傅清洋 Lena", ["傅", "清洋 Lena", "", "", ""]),  # was FN 傅清洋Lena, N:傅清洋;Lena;;;
        ("单位-快递-速递", ["速递", "", "", "单位-快递-", ""]),  # name-prefix: the last part is the family name
        ("张伟诚", ["张", "伟诚", "", "", ""]),  # was N:张;诚;伟;;: the middle name goes before the Han given name
    ],
)
def test_name_label_and_split(google_v30, fn, expected_n):
    card = by_fn(cleaned(google_v30))[fn]
    assert card.get("N").components == expected_n
    assert card.get("FN") is not None


@pytest.mark.parametrize(
    ("uid_", "expected_n"),
    [
        ("205", ["CHEN", "Lei", "", "", ""]),  # English names are given-first; name-case writes the surname in capitals
        ("60", ["KONG", "Gavin", "", "", ""]),  # all-caps token is the family name
    ],
)
def test_name_split_latin(legacy_v21_gbk, uid_, expected_n):
    card = by_uid(cleaned(legacy_v21_gbk))[uid_]
    assert card.get("N").components == expected_n


# ---------------------------------------------------------------- phone-type
def test_landline_tagged_cell_becomes_voice(google_v30):
    card = by_fn(cleaned(google_v30))["蔡思"]
    types = {t.value: t.params["TYPE"] for t in card.get_all("TEL")}
    assert types["+861055791816"] == ["VOICE"]  # was TYPE=CELL:+86 10 55791816
    assert types["+8615376400148"] == ["CELL"]


# ---------------------------------------------------------------- photo-embed
def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, "JPEG")
    return buffer.getvalue()


JPEG = _jpeg()
PHOTO_URL_PREFIX = "https://lh3.googleusercontent.com/contacts/AG6tpz"


def photo_cleaned(path, fetch):
    rules = clean_mod.DEFAULT_RULES | {"photo-embed"}
    return clean_mod.clean_cards(vcardmanager.read_vcards(path), rules, region="CN", fetch=fetch)


def test_photo_embed_off_by_default(google_v30):
    assert "photo-embed" not in clean_mod.DEFAULT_RULES
    card = by_fn(cleaned(google_v30))["沈云/APA"]
    assert card.get("NOTE").value.startswith("Photo: ")
    assert card.get("PHOTO") is None
    assert not any("photo" in w for w in card.warnings)  # nothing was attempted


def test_photo_note_link_becomes_embedded_photo(google_v30):
    urls = []

    def fetch(url):
        urls.append(url)
        return JPEG

    card = by_fn(photo_cleaned(google_v30, fetch))["沈云/APA"]
    photo = card.get("PHOTO")
    assert photo.params["ENCODING"] == ["b"]
    assert photo.params["TYPE"] == ["JPEG"]
    assert base64.b64decode(photo.value) == JPEG
    assert card.get("NOTE") is None  # the photo note is removed once embedded
    assert all(u.startswith(PHOTO_URL_PREFIX) for u in urls)  # Google's `https\://` is unescaped


def test_photo_note_and_photo_url_fetched_once(google_v30):
    urls = []

    def fetch(url):
        urls.append(url)
        return JPEG

    url = by_fn(vcardmanager.read_vcards(google_v30))["宋平国"].get("PHOTO").value
    card = by_fn(photo_cleaned(google_v30, fetch))["宋平国"]
    assert len(card.get_all("PHOTO")) == 1
    assert card.get("NOTE") is None
    assert urls.count(url) == 1  # same URL in NOTE and PHOTO: downloaded once


def test_photo_download_failure_keeps_link(google_v30):
    def fetch(url):
        raise OSError("404")

    card = by_fn(photo_cleaned(google_v30, fetch))["沈云/APA"]
    assert card.get("PHOTO") is None
    assert card.get("NOTE").value.startswith("Photo: ")
    assert card.warnings


# ---------------------------------------------------------------- drop-extensions
def extensions_cleaned(path):
    rules = clean_mod.DEFAULT_RULES | {"drop-extensions"}
    return clean_mod.clean_cards(vcardmanager.read_vcards(path), rules, region="CN")


def test_drop_extensions_keeps_groups_as_categories(legacy_v21_gbk):
    cards = extensions_cleaned(legacy_v21_gbk)
    assert all(c.kind != "group" for c in cards)
    assert len(cards) == 72
    contacts = by_uid(cards)
    assert "work-ops" in contacts["153"].get("CATEGORIES").components  # only listed via Grouping:
    assert "work-vendor-c" in contacts["203"].get("CATEGORIES").components  # group had no X-MEMBERS
    for card in cards:
        assert not [p.name for p in card.properties if p.name.startswith("X-") or p.name == "GROUPING"]


def test_drop_extensions_removes_orphaned_property_groups(google_v30):
    cards = by_fn(extensions_cleaned(google_v30))
    emails = cards["宋平国"].get_all("EMAIL")
    # item1/item2 lost their X-ABLABEL, so the prefixes go; the duplicated addresses are merged
    assert [(e.group, e.value) for e in emails] == [
        (None, "songpingguo@airtech.example"),
        (None, "songpingguo2@mail.example"),
    ]
    assert not [p for p in cards["朱强/深圳"].properties if p.name.startswith("X-")]


@pytest.mark.parametrize("fixture", ["google_v30", "legacy_v21_gbk"])
def test_clean_is_idempotent(fixture, request):
    once = cleaned(request.getfixturevalue(fixture))
    twice = [clean_mod.clean(c, clean_mod.DEFAULT_RULES, region="CN") for c in once]
    assert twice == once
