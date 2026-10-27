import base64
import io
import os

import pytest
from click.testing import CliRunner
from PIL import Image

from vcardmanager import parse_text, read_vcards
from vcardmanager.clean import DEFAULT_RULES, RULES, clean, clean_cards, resolve
from vcardmanager.clean.photos import compress_image, prepare_image
from vcardmanager.cli import main


def card(body, version="3.0"):
    return parse_text(f"BEGIN:VCARD\nVERSION:{version}\n{body}END:VCARD\n")[0]


def only(body, *rules, **options):
    return clean(card(body), rules, **options)


# ---------------------------------------------------------------- registry
def test_resolve_order_and_dependencies():
    assert [r.name for r in resolve(["sort-properties", "drop-extensions"])] == [
        "groups-to-categories",
        "drop-extensions",
        "sort-properties",
    ]
    with pytest.raises(ValueError, match="unknown rule"):
        resolve(["no-such-rule"])


def test_defaults_match_readme():
    off = {r.name for r in RULES if not r.default}
    assert off == {"groups-to-categories", "photo-embed"}  # groups-to-categories runs with drop-extensions
    assert DEFAULT_RULES == {r.name for r in RULES} - off


def test_clean_does_not_change_its_input():
    original = card("FN:  Ann  \nEMAIL:ANN@X.EXAMPLE\n")
    clean(original)
    assert original.get("FN").value == "  Ann  "


# ---------------------------------------------------------------- structure
def test_fix_encoding():
    result = only("FN:A\u200bn\u0007n\nNOTE:e\u0301\n", "fix-encoding")
    assert result.get("FN").value == "Ann"
    assert result.get("NOTE").value == "é"  # NFC


def test_upgrade_version_removes_invalid_type():
    result = only("FN:a\nEMAIL;TYPE=#1:a@b.example\nTEL;TYPE=CELL:1\n", "upgrade-version")
    assert result.get("EMAIL").params == {}
    assert result.get("TEL").params == {"TYPE": ["CELL"]}
    assert any("#1" in w for w in result.warnings)


def test_trim_whitespace_keeps_line_breaks_and_components():
    result = only("FN:  Ann   Lee \nNOTE:  a  b \\n  c\nN: Lee ; Ann ;;;\n", "trim-whitespace")
    assert result.get("FN").value == "Ann Lee"
    assert result.get("NOTE").value == "a b\nc"
    assert result.get("N").components == ["Lee", "Ann", "", "", ""]


def test_drop_empty():
    result = only("FN:a\nNOTE:\nN:;;;;\nitem1.X-ABLABEL:\nTEL:1\n", "drop-empty")
    assert [p.name for p in result.properties] == ["FN", "TEL"]


def test_type_normalize():
    result = only("FN:a\nTEL;TYPE=mobile,cell;TYPE=Work:1\n", "type-normalize")
    assert result.get("TEL").params["TYPE"] == ["CELL", "WORK"]


def test_dedupe_values_merges_types_and_drops_labels():
    result = only(
        "FN:a\nTEL;TYPE=CELL:+1 650 253 0000\nitem1.TEL;TYPE=WORK:+16502530000\nitem1.X-ABLABEL:Office\n"
        "EMAIL:A@b.example\nEMAIL:a@B.example\n",
        "dedupe-values",
    )
    assert [(p.name, p.group) for p in result.properties] == [("FN", None), ("TEL", None), ("EMAIL", None)]
    assert result.get("TEL").params["TYPE"] == ["CELL", "WORK"]


def test_dedupe_values_matches_like_dedupe():
    result = only("FN:a\nTEL;TYPE=CELL:+86 138 0013 8000\nTEL;TYPE=WORK:13800138000\nTEL:95338\nTEL:95338\n",
                  "dedupe-values")  # fmt: skip
    assert [(t.value, t.params.get("TYPE")) for t in result.get_all("TEL")] == [
        ("+86 138 0013 8000", ["CELL", "WORK"]),  # with and without the country code: one number
        ("95338", None),  # short numbers are compared by their digits
    ]


def test_sort_properties_keeps_groups_together():
    result = only("X-FOO:1\nitem1.X-ABLABEL:Home\nNOTE:n\nitem1.EMAIL:a@b.example\nFN:a\nUID:1\n", "sort-properties")
    assert [(p.group or "") + p.name for p in result.properties] == [
        "FN",
        "item1EMAIL",
        "item1X-ABLABEL",
        "NOTE",
        "UID",
        "X-FOO",
    ]


# ---------------------------------------------------------------- phones
@pytest.mark.parametrize(
    ("raw", "style", "expected"),
    [
        ("138 0013 8000", "e164", "+8613800138000"),
        ("0086 138 0013 8000", "e164", "+8613800138000"),
        ("138 0013 8000", "international", "+86 138 0013 8000"),
        ("138 0013 8000", "national", "138 0013 8000"),
        ("+1 650-253-0000", "national", "+1 650-253-0000"),  # foreign: keeps its country code
        ("tel:+16502530000", "e164", "tel:+16502530000"),
    ],
)
def test_phone_formats(raw, style, expected):
    result = only(f"FN:a\nTEL:{raw}\n", "phone-format", region="cn", phone_format=style)
    assert result.get("TEL").value == expected


def test_phone_format_rejects_unknown_style():
    with pytest.raises(ValueError):
        only("FN:a\n", "phone-format", phone_format="fancy")


def test_phone_type():
    result = only(
        "FN:a\nTEL;TYPE=CELL,WORK:+86 10 5700 7272\nTEL;TYPE=WORK:+86 138 0013 8000\n"
        "TEL;TYPE=CELL,PREF:010 5700 7272\n",
        "phone-type",
        region="CN",
    )
    assert [t.params["TYPE"] for t in result.get_all("TEL")] == [["WORK"], ["WORK", "CELL"], ["PREF", "VOICE"]]


# ---------------------------------------------------------------- emails
def test_email_cleanup():
    result = only(
        'FN:a\nEMAIL:"Ann Lee" <Ann@Example.COM>\nEMAIL:mailto:B@x.example\nEMAIL:nobody1@nowhere.invalid\n',
        "email-lowercase",
    )
    assert [e.value for e in result.get_all("EMAIL")] == ["ann@example.com", "b@x.example"]


# ---------------------------------------------------------------- names
@pytest.mark.parametrize(
    ("body", "fn"),
    [
        ("N:王;小明;;;\n", "王小明"),
        ("N:Lee;Ann;M.;Dr.;PhD\n", "Dr. Ann M. Lee PhD"),
        ("N:沈;云;;;/APA\n", "沈云/APA"),
        ("ORG:Acme;Sales\n", "Acme"),
        ("EMAIL:support@acme.example\n", "support (acme.example)"),
        ("TEL:+1 650 253 0000\n", "+1 650 253 0000"),
    ],
)
def test_name_fill_sources(body, fn):
    assert only(body, "name-fill").get("FN").value == fn


def test_name_fill_replaces_the_parser_warning():
    filled = only("EMAIL:ann@x.example\n", "name-fill")
    assert filled.warnings == ["missing FN, filled with 'ann'"]


def test_name_fill_adds_n_but_not_to_group_cards():
    assert only("FN:Ann Lee\n", "name-fill").get("N").components == ["", "Ann Lee", "", "", ""]
    group = clean(card("KIND:group\nFN:Team\n", "4.0"), ["name-fill"])
    assert group.get("N") is None
    assert only("FN:a\nN:;handle\n", "name-fill").get("N").components == ["", "handle", "", "", ""]


@pytest.mark.parametrize(
    ("fn", "n", "expected"),
    [
        ("欧阳娜娜", ";欧阳娜娜;;;", ["欧阳", "娜娜", "", "", ""]),  # compound surname
        # the four agreed layouts
        ("张三/suffix", ";张三/suffix;;;", ["张", "三", "", "", "/suffix"]),
        ("John Smith/suffix", ";John Smith/suffix;;;", ["Smith", "John", "", "", "/suffix"]),
        ("Bob 张三/suffix", ";Bob 张三/suffix;;;", ["Bob 张", "三", "", "", "/suffix"]),
        ("史强 John Smith/suffix", ";史强 John Smith/suffix;;;", ["Smith", "史强 John", "", "", "/suffix"]),
        # the English part's position in FN does not change the layout
        ("张三 Bob", ";张三 Bob;;;", ["Bob 张", "三", "", "", ""]),
        ("王海力Amy", "王海力;Amy;;;", ["Amy 王", "海力", "", "", ""]),
        ("Bob Lee 张三", ";Bob Lee 张三;;;", ["Lee", "张三 Bob", "", "", ""]),
        ("John Smith 史强", ";John Smith 史强;;;", ["Smith", "史强 John", "", "", ""]),
        ("Mary Jane Watson 王芳", ";Mary Jane Watson 王芳;;;", ["Watson", "王芳 Mary Jane", "", "", ""]),
        ("Gavin KONG 王小明", ";Gavin KONG 王小明;;;", ["KONG", "王小明 Gavin", "", "", ""]),
        ("欧阳娜娜 Nana", ";欧阳娜娜 Nana;;;", ["Nana 欧阳", "娜娜", "", "", ""]),
        # already in the agreed layout: unchanged
        ("Bob 张三", "Bob 张;三;;;", ["Bob 张", "三", "", "", ""]),
        ("史强 John Smith", "Smith;史强 John;;;", ["Smith", "史强 John", "", "", ""]),
        ("Mary Jane Watson", ";Mary Jane Watson;;;", ["Watson", "Mary Jane", "", "", ""]),
        ("Lee", ";Lee;;;", ["", "Lee", "", "", ""]),  # one word: nothing to split
        ("Ann Lee", "Lee;Ann;;;", ["Lee", "Ann", "", "", ""]),  # already split
        ("单位-快递", ";单位-快递;;;", ["", "单位-快递", "", "", ""]),  # organisation
        ("王小明/北京/朝阳", ";王小明/北京/朝阳;;;", ["王", "小明", "", "", "/北京/朝阳"]),
    ],
)
def test_name_label_and_split(fn, n, expected):
    result = only(f"FN:{fn}\nN:{n}\n", "name-label", "name-split")
    assert result.get("N").components == expected
    assert result.get("FN").value == fn


def test_name_label_not_applied_to_organisations():
    result = only("FN:单位-燃气/客服\nN:;单位-燃气/客服;;;\n", "name-label")
    assert result.get("N").components == ["", "单位-燃气/客服", "", "", ""]


@pytest.mark.parametrize(
    ("body", "fn", "n"),
    [
        ("FN:JOHN SMITH\nN:SMITH;JOHN;;;\n", "John SMITH", ["SMITH", "John", "", "", ""]),
        ("FN:John Smith\nN:Smith;John;;;\n", "John SMITH", ["SMITH", "John", "", "", ""]),
        ("FN:Lei Chen/HR\nN:Chen;Lei;;;/HR\n", "Lei CHEN/HR", ["CHEN", "Lei", "", "", "/HR"]),  # label untouched
        ("FN:史强 John Smith\nN:Smith;史强 John;;;\n", "史强 John SMITH", ["SMITH", "史强 John", "", "", ""]),
        ("FN:Bob 张三\nN:Bob 张;三;;;\n", "Bob 张三", ["Bob 张", "三", "", "", ""]),  # Chinese surname: unchanged
        ("FN:单位-快递\nN:;单位-快递;;;\n", "单位-快递", ["", "单位-快递", "", "", ""]),  # no surname: unchanged
    ],
)
def test_name_case_surname_in_capitals(body, fn, n):
    result = only(body, "name-case")
    assert result.get("FN").value == fn
    assert result.get("N").components == n


@pytest.mark.parametrize(
    ("body", "fn"),
    [
        ("FN:john smith\n", "John SMITH"),  # without N, the surname is found like name-split does
        ("FN:Mary Jane Watson\n", "Mary Jane WATSON"),
        ("FN:MARY-JANE O'NEIL/HR\n", "Mary-Jane O'NEIL/HR"),  # hyphens and apostrophes; the label keeps its case
        ("FN:bob\n", "Bob"),  # one word: no surname to find
        ("FN:McDonald\n", "McDonald"),
        ("FN:IBM\n", "IBM"),  # short all-caps word: an abbreviation
        ("FN:Gavin KONG\n", "Gavin KONG"),
        ("FN:lin_shan\n", "lin_shan"),  # handles
        ("FN:user2\n", "user2"),
        ("FN:tzhao\nEMAIL:tzhao@x.example\n", "tzhao"),
        ("FN:ann@other.example\n", "ann@other.example"),  # an email address used as the name
        ("FN:王小明 JOHN\n", "王小明 JOHN"),  # Chinese surname: unchanged
    ],
)
def test_name_case_guards(body, fn):
    assert only(body, "name-case").get("FN").value == fn


# ---------------------------------------------------------------- photos
def image_bytes(fmt, size=(40, 20), mode="RGB", color="red"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF"])
def test_prepare_image_keeps_common_formats(fmt):
    data = image_bytes(fmt)
    assert prepare_image(data) == (data, fmt)


def test_prepare_image_converts_other_formats_and_transparency():
    converted, kind = prepare_image(image_bytes("WEBP", mode="RGBA", color=(0, 0, 0, 0)))
    assert kind == "JPEG"
    with Image.open(io.BytesIO(converted)) as image:
        assert image.format == "JPEG"
        assert image.getpixel((0, 0)) == pytest.approx((255, 255, 255), abs=2)  # transparent became white


def noise(fmt, size, **save):
    """A photo-like image that does not compress well, so sizes compare like real photos."""
    buffer = io.BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, fmt, **save)
    return buffer.getvalue()


def test_compress_image():
    shrunk = compress_image(noise("PNG", (1200, 600)), max_px=512)
    with Image.open(io.BytesIO(shrunk)) as image:
        assert (image.format, image.size) == ("JPEG", (512, 256))
    assert compress_image(noise("PNG", (300, 200)), max_px=512) is None  # already small enough
    # a low-quality JPEG would grow when re-encoded at quality 85: keep the original
    assert compress_image(noise("JPEG", (600, 600), quality=5), max_px=512) is None
    with pytest.raises(ValueError, match="not a readable image"):
        compress_image(b"not an image", max_px=512)


def test_photo_compression_rule():
    big = base64.b64encode(noise("PNG", (1000, 1000))).decode()
    small = base64.b64encode(image_bytes("PNG")).decode()
    result = only(
        f"FN:a\nPHOTO;ENCODING=b;TYPE=PNG:{big}\nPHOTO;ENCODING=b;TYPE=PNG:{small}\nPHOTO:https://x.example/p.jpg\n",
        "photo-compression",
    )
    first, second, link = result.get_all("PHOTO")
    assert first.params == {"ENCODING": ["b"], "TYPE": ["JPEG"]}
    with Image.open(io.BytesIO(base64.b64decode(first.raw))) as image:
        assert image.size == (512, 512)
    assert second.raw == small  # small photos are left alone
    assert link.raw == "https://x.example/p.jpg"  # links are photo-embed's business


def test_photo_compression_reads_40_data_uris_and_warns_on_bad_data():
    big = base64.b64encode(noise("JPEG", (900, 300))).decode()
    result = clean(card(f"FN:a\nPHOTO:data:image/jpeg;base64,{big}\n", "4.0"), ["photo-compression"], photo_max_px=300)
    assert result.get("PHOTO").params == {"ENCODING": ["b"], "TYPE": ["JPEG"]}
    broken = only("FN:a\nPHOTO;ENCODING=b;TYPE=JPEG:bm90IGFuIGltYWdl\n", "photo-compression")
    assert broken.get("PHOTO").raw == "bm90IGFuIGltYWdl"
    assert any("not a readable image" in w for w in broken.warnings)


def test_photo_embed_then_compression():
    fetch = lambda url: noise("PNG", (1600, 1200))  # noqa: E731
    result = only("FN:a\nPHOTO:https://x.example/big.png\n", "photo-embed", "photo-compression", fetch=fetch)
    with Image.open(io.BytesIO(base64.b64decode(result.get("PHOTO").raw))) as image:
        assert (image.format, image.size) == ("JPEG", (512, 384))


def test_prepare_image_rejects_non_images():
    with pytest.raises(ValueError, match="not a readable image"):
        prepare_image(b"<html>Not found</html>")


def test_photo_embed_from_photo_url():
    def fetch(url):
        assert url == "https://x.example/a.webp"
        return image_bytes("WEBP")

    result = only("FN:a\nPHOTO;VALUE=uri:https://x.example/a.webp\n", "photo-embed", fetch=fetch)
    photo = result.get("PHOTO")
    assert photo.params == {"ENCODING": ["b"], "TYPE": ["JPEG"]}
    assert prepare_image(base64.b64decode(photo.raw))[1] == "JPEG"


def test_photo_downloads_are_logged_once_per_url():
    messages = []

    def fetch(url):
        if url.endswith("broken"):
            raise OSError("timed out")
        return image_bytes("PNG")

    clean_cards(
        parse_text(
            "BEGIN:VCARD\nVERSION:3.0\nFN:a\nPHOTO:https://x.example/ok\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nFN:b\nPHOTO:https://x.example/ok\nEND:VCARD\n"  # cached: not logged again
            "BEGIN:VCARD\nVERSION:3.0\nFN:c\nPHOTO:https://x.example/broken\nEND:VCARD\n"
        ),
        ["photo-embed"],
        fetch=fetch,
        log=messages.append,
    )
    assert [m.split(" in ")[0].split(" after ")[0] for m in messages] == [
        "Downloading photo 1: https://x.example/ok",
        "Photo 1: 0 KB",
        "Downloading photo 2: https://x.example/broken",
        "Photo 2: failed",
    ]
    assert messages[-1].endswith("(timed out)")


def test_cli_prints_photo_progress(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    result = CliRunner().invoke(main, ["clean", str(google_v30), "-o", str(out), "--enable", "photo-embed"])
    assert result.exit_code == 0, result.output
    # 8 photo NOTEs and 1 PHOTO, but 宋平国's PHOTO repeats his NOTE's URL: 8 downloads (refused in tests)
    assert result.stderr.count("Downloading photo ") == 8
    assert result.stderr.count(": failed after ") == 8
    quiet = CliRunner().invoke(main, ["clean", str(google_v30), "-o", str(tmp_path / "q.vcf"), "-q",
                                      "--enable", "photo-embed"])  # fmt: skip
    assert quiet.stderr == ""


def test_photo_embed_keeps_link_for_non_image():
    result = only("FN:a\nPHOTO:https://x.example/missing\n", "photo-embed", fetch=lambda url: b"<html>404</html>")
    assert result.get("PHOTO").raw == "https://x.example/missing"
    assert any("not a readable image" in w for w in result.warnings)


# ---------------------------------------------------------------- whole file
def test_groups_to_categories_icloud():
    cards = parse_text(
        "BEGIN:VCARD\nVERSION:3.0\nX-ADDRESSBOOKSERVER-KIND:group\nFN:Family\n"
        "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:a\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Ann\nUID:a\nCATEGORIES:Friends\nEND:VCARD\n"
    )
    result = clean_cards(cards, ["groups-to-categories"])
    assert len(result) == 1
    assert result[0].get("CATEGORIES").components == ["Friends", "Family"]


def test_clean_skips_whole_file_rules():
    group = card("X-TYPE:GROUP\nN:g\nX-MEMBERS:1;2\n", "2.1")
    assert clean(group, ["groups-to-categories"]).kind == "group"
    # drop-extensions depends on groups-to-categories, so one card on its own keeps its group fields
    kept = clean(group)
    assert kept.kind == "group"
    assert kept.get("X-MEMBERS") is not None


def test_cleaned_legacy_fixture_validates(legacy_v21_gbk, tmp_path):
    out = tmp_path / "out.vcf"
    result = CliRunner().invoke(main, ["clean", str(legacy_v21_gbk), "-o", str(out), "--region", "CN"])
    assert result.exit_code == 0, result.output
    assert "Cleaned 72 cards" in result.stderr  # drop-extensions is on: the 7 groups moved into CATEGORIES
    validated = CliRunner().invoke(main, ["validate", str(out)])
    assert validated.exit_code == 0
    assert "0 errors, 0 warnings" in validated.stderr
    assert all(c.get("FN") for c in read_vcards(out))


def test_cli_list_rules_and_errors(google_v30, tmp_path):
    runner = CliRunner()
    listing = runner.invoke(main, ["clean", "--list-rules"])
    assert listing.exit_code == 0
    assert "photo-embed" in listing.output and "off" in listing.output
    assert runner.invoke(main, ["clean"]).exit_code == 2
    bad = runner.invoke(main, ["clean", str(google_v30), "-o", str(tmp_path / "o.vcf"), "--enable", "nope"])
    assert bad.exit_code == 2
    assert "unknown rule" in bad.output
