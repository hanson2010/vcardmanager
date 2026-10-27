import pytest
from hypothesis import given
from hypothesis import strategies as st

from vcardmanager.encoding import (
    decode_bytes,
    decode_quoted_printable,
    escape,
    fold,
    split_unescaped,
    unescape,
)

# line breaks are normalized to \n by escape(), so a lone \r does not round-trip
text_values = st.text(alphabet=st.characters(blacklist_characters="\r", blacklist_categories=("Cs",)))


@given(text_values)
def test_escape_round_trip(value):
    assert unescape(escape(value)) == value


@given(text_values)
def test_escaped_value_has_no_separators(value):
    escaped = escape(value)
    assert "\n" not in escaped
    assert split_unescaped(escaped, ";") == [escaped]
    assert split_unescaped(escaped, ",") == [escaped]


@given(st.lists(text_values, min_size=1))
def test_split_unescaped_round_trip(parts):
    joined = ";".join(escape(p) for p in parts)
    assert [unescape(p) for p in split_unescaped(joined, ";")] == parts


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1))
def test_fold_limits_and_round_trip(line):
    lines = fold(line)
    assert all(len(physical.encode("utf-8")) <= 75 for physical in lines)
    assert all(physical.startswith(" ") for physical in lines[1:])
    assert lines[0] + "".join(physical[1:] for physical in lines[1:]) == line


def test_fold_never_splits_multibyte_character():
    lines = fold("NOTE:" + "汉" * 40)  # 5 + 40 * 3 = 125 octets
    assert [len(physical.encode("utf-8")) for physical in lines] == [74, 52]  # 75 would split a character


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"a\,b\;c\\d\ne", "a,b;c\\d\ne"),
        (r"line\Nbreak", "line\nbreak"),
        (r"https\\://x", r"https\://x"),  # Google's double-escaped colon
        (r"unknown\:escape", r"unknown\:escape"),
        ("trailing\\", "trailing\\"),
    ],
)
def test_unescape(raw, expected):
    assert unescape(raw) == expected


def test_decode_bytes_fallbacks():
    assert decode_bytes("王".encode()) == "王"
    assert decode_bytes("王".encode("gbk")) == "王"
    assert decode_bytes(b"\xef\xbb\xbfFN:x") == "FN:x"
    assert decode_bytes("FN:王".encode("utf-16")) == "FN:王"
    assert decode_bytes("王".encode("big5"), encoding="big5") == "王"


def test_decode_quoted_printable_with_charset():
    assert decode_quoted_printable("=E7=8E=8B=E5=B0=8F=E6=98=8E", "UTF-8") == "王小明"
    assert decode_quoted_printable("=CD=F5", "GBK") == "王"
    assert decode_quoted_printable("=CD=F5", None) == "王"  # no CHARSET: detected like a file
    assert decode_quoted_printable("a=0D=0Ab", "UTF-8") == "a\r\nb"
