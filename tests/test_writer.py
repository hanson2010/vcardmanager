import io

import pytest

from vcardmanager import Property, VCard, dumps, parse_text, read_vcards, write_vcards


def card(*props):
    return VCard(list(props))


def test_layout_and_line_endings():
    text = dumps([card(Property.from_value("FN", "A"))])
    assert text == "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A\r\nEND:VCARD\r\n"
    assert dumps([card(Property.from_value("FN", "A"))], line_ending="\n").count("\r") == 0


def test_long_lines_are_folded_and_read_back():
    note = Property.from_value("NOTE", "汉字" * 60)
    text = dumps([card(Property.from_value("FN", "A"), note)])
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    assert parse_text(text)[0].get("NOTE").value == "汉字" * 60


def test_group_params_and_quoting():
    prop = Property("ADR", ";;1 Main St;;;;", {"TYPE": ["work"], "LABEL": ["1 Main St, Room 2"]}, "item1")
    assert 'item1.ADR;TYPE=work;LABEL="1 Main St, Room 2":;;1 Main St;;;;' in dumps([card(prop)])


def test_unsupported_version():
    with pytest.raises(ValueError):
        dumps([], version="2.1")


def test_pref_between_30_and_40():
    tel = Property("TEL", "+1", {"TYPE": ["CELL", "PREF"]})
    assert "TEL;TYPE=CELL;PREF=1:+1" in dumps([card(tel)], version="4.0")
    tel40 = Property("TEL", "+1", {"TYPE": ["cell"], "PREF": ["1"]})
    assert "TEL;TYPE=cell,PREF:+1" in dumps([card(tel40)], version="3.0")


def test_embedded_photo_between_30_and_40():
    photo30 = Property("PHOTO", "/9j/4AAQ", {"TYPE": ["JPEG"], "ENCODING": ["b"]})
    assert "PHOTO:data:image/jpeg;base64,/9j/4AAQ" in dumps([card(photo30)], version="4.0")
    photo40 = Property("PHOTO", "data:image/png;base64,iVBOR")
    assert "PHOTO;ENCODING=b;TYPE=PNG:iVBOR" in dumps([card(photo40)], version="3.0")


def test_write_to_text_stream():
    buffer = io.StringIO()
    write_vcards([card(Property.from_value("FN", "A"))], buffer)
    assert buffer.getvalue().startswith("BEGIN:VCARD")


def test_legacy_21_converts_to_30_without_loss(legacy_v21_gbk, tmp_path):
    cards = read_vcards(legacy_v21_gbk)
    out = tmp_path / "out.vcf"
    write_vcards(cards, out, version="3.0")
    again = read_vcards(out)
    assert {c.source_version for c in again} == {"3.0"}
    assert [c.properties for c in again] == [c.properties for c in cards]
    assert "\\;" not in out.read_text(encoding="utf-8").split("X-MEMBERS:")[1].split("\r\n")[0]


@pytest.mark.parametrize("version", ["3.0", "4.0"])
def test_google_round_trip_through_each_version(google_v30, tmp_path, version):
    cards = read_vcards(google_v30)
    first = tmp_path / "first.vcf"
    write_vcards(cards, first, version=version)
    second = tmp_path / "second.vcf"
    write_vcards(read_vcards(first), second, version=version)
    assert first.read_bytes() == second.read_bytes()  # stable after one conversion
