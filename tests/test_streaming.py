"""Streaming read/write: one card in memory at a time, and safe replacement of the output file."""

import pytest
from click.testing import CliRunner

from vcardmanager import iter_vcards, read_vcards, write_vcards
from vcardmanager.cli import main
from vcardmanager.encoding import detect_file_encoding


def test_iter_vcards_is_lazy(google_v30):
    cards = iter_vcards(google_v30)
    first = next(cards)
    assert first.get("FN").value == "韩乐嘉/项目办"
    assert sum(1 for _ in cards) == 54


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("FN:王\r\n".encode(), "utf-8"),
        ("FN:王\r\n".encode("gbk"), "gb18030"),
        (b"\xef\xbb\xbfFN:x\r\n", "utf-8-sig"),
        ("FN:王\r\n".encode("utf-16"), "utf-16"),
    ],
)
def test_detect_file_encoding(tmp_path, data, expected):
    path = tmp_path / "x.vcf"
    path.write_bytes(data)
    assert detect_file_encoding(path) == expected


def test_detect_reads_past_the_first_chunk(tmp_path):
    path = tmp_path / "x.vcf"
    path.write_bytes(b"A" * 5000 + "王".encode("gbk"))  # the first non-UTF-8 byte is far into the file
    assert detect_file_encoding(path, chunk_size=1024) == "gb18030"


def test_long_folded_values_are_joined(tmp_path):
    photo = "A" * 100_000
    path = tmp_path / "x.vcf"
    write_vcards(read_vcards_from_text(f"FN:x\nPHOTO;ENCODING=b;TYPE=JPEG:{photo}\n", tmp_path), path)
    assert read_vcards(path)[0].get("PHOTO").raw == photo


def read_vcards_from_text(body, tmp_path):
    source = tmp_path / "source.vcf"
    source.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\n{body}END:VCARD\r\n", encoding="utf-8")
    return read_vcards(source)


def test_failed_write_keeps_the_old_file(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    out.write_text("old", encoding="utf-8")

    def broken():
        yield from read_vcards(google_v30)[:3]
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        write_vcards(broken(), out)
    assert out.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.vcf"]  # no temporary file left behind


def test_overwriting_an_input_while_streaming_it(google_v30, tmp_path):
    copy = tmp_path / "contacts.vcf"
    copy.write_bytes(google_v30.read_bytes())
    result = CliRunner().invoke(main, ["strip", str(copy), "--field", "PHOTO"])
    assert result.exit_code == 0, result.output
    cards = read_vcards(copy)
    assert len(cards) == 55
    assert not any(c.get("PHOTO") for c in cards)


def test_bad_explicit_encoding_is_a_usage_error(google_v30, copy_of, tmp_path):
    source = copy_of(google_v30)
    result = CliRunner().invoke(main, ["merge", str(source), "--encoding", "ascii"])
    assert result.exit_code == 2
    assert [p.name for p in tmp_path.iterdir()] == ["google_v30.vcf"]
    result = CliRunner().invoke(main, ["clean", str(source), "--encoding", "ascii"])
    assert result.exit_code == 2
    assert source.read_bytes() == google_v30.read_bytes()  # left intact
