from pathlib import Path

import pytest

from vcardmanager.io import OutputError, check_output, collect_inputs, safe_filename, unique_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Clyde Mui/HK", "Clyde Mui_HK"),
        ('a<b>c:d"e\\f|g?h*i', "a_b_c_d_e_f_g_h_i"),
        ("trailing dots... ", "trailing dots"),
        ("CON", "_CON"),
        ("com1.backup", "_com1.backup"),
        ("", "unnamed"),
        ("///", "_"),
        ("王明/IT 香港", "王明_IT 香港"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_safe_filename_length_limit():
    assert len(safe_filename("x" * 300)) == 100


def test_unique_name_ignores_case():
    used: set[str] = set()
    assert [unique_name(n, used) for n in ("Ann", "ann", "ANN", "Bob")] == ["Ann", "ann_2", "ANN_3", "Bob"]


def test_collect_inputs_expands_directories(tmp_path):
    (tmp_path / "b.vcf").write_text("")
    (tmp_path / "a.VCF").write_text("")
    (tmp_path / "notes.txt").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.vcard").write_text("")
    assert [Path(p).name for p in collect_inputs([str(tmp_path)])] == ["a.VCF", "b.vcf"]
    assert [Path(p).name for p in collect_inputs([str(tmp_path)], recursive=True)] == ["a.VCF", "b.vcf", "c.vcard"]
    assert collect_inputs(["-"]) == ["-"]
    with pytest.raises(FileNotFoundError):
        collect_inputs([str(tmp_path / "missing.vcf")])


def test_check_output_refuses_overwrite(tmp_path):
    existing = tmp_path / "out.vcf"
    existing.write_text("")
    with pytest.raises(OutputError, match="already exists"):
        check_output(str(existing))
    with pytest.raises(OutputError, match="also an input"):
        check_output(str(existing), [str(existing)])
    check_output(str(existing), [str(existing)], force=True)
    check_output(str(tmp_path / "new.vcf"))
    check_output("-")
