import subprocess
import sys

import pytest
from click.testing import CliRunner
from conftest import ROOT

from vcardmanager import __version__, read_vcards
from vcardmanager.cli import main


def run(*args, input=None):
    return CliRunner().invoke(main, [str(a) for a in args], input=input)


def test_version_option():
    result = run("--version")
    assert result.exit_code == 0
    assert result.output == f"vcardmanager, version {__version__}\n"


def test_help_lists_commands():
    result = run("-h")
    assert result.exit_code == 0
    for command in ("merge", "split", "info", "validate"):
        assert command in result.output


def test_runs_as_module():
    result = subprocess.run(
        [sys.executable, "-m", "vcardmanager", "--version"], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert __version__ in result.stdout


def by_uid(cards):
    return sorted(cards, key=lambda c: c.get("UID").value)


def test_merge_split_merge_is_lossless(google_v30, legacy_v21_gbk, tmp_path):
    merged = tmp_path / "all.vcf"
    assert run("merge", google_v30, legacy_v21_gbk, "-o", merged).exit_code == 0
    assert run("split", merged, "-d", tmp_path / "parts", "--per-file", 1).exit_code == 0
    assert len(list((tmp_path / "parts").glob("*.vcf"))) == 55 + 79
    again = tmp_path / "again.vcf"
    assert run("merge", tmp_path / "parts", "-o", again).exit_code == 0
    assert by_uid(read_vcards(again)) == by_uid(read_vcards(merged))


def test_merge_to_stdout_and_from_stdin(google_v30):
    result = run("merge", "-", "-o", "-", input=google_v30.read_bytes())
    assert result.exit_code == 0
    assert result.stdout_bytes.count(b"BEGIN:VCARD\r\n") == 55
    assert b"\r\r\n" not in result.stdout_bytes


def test_merge_refuses_to_overwrite(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    out.write_text("keep me")
    result = run("merge", google_v30, "-o", out)
    assert result.exit_code == 2
    assert "already exists" in result.output
    assert out.read_text() == "keep me"
    assert run("merge", google_v30, "-o", out, "--force").exit_code == 0


@pytest.mark.parametrize(
    ("command", "options", "suffix"),
    [
        ("merge", [], "merged"),
        ("clean", ["--disable", "photo-embed"], "cleaned"),
        ("strip", ["--field", "PHOTO"], "stripped"),
        ("dedupe", [], "deduped"),
    ],
)
def test_default_output_never_overwrites(google_v30, tmp_path, command, options, suffix):
    source = tmp_path / "contacts.vcf"
    source.write_bytes(google_v30.read_bytes())
    for _ in range(3):
        result = run(command, source, *options)
        assert result.exit_code == 0, result.output
    names = sorted(p.name for p in tmp_path.iterdir())
    expected = [f"contacts_{suffix}.vcf", f"contacts_{suffix}_2.vcf", f"contacts_{suffix}_3.vcf"]
    assert names == sorted(["contacts.vcf", *expected])
    assert f"to {tmp_path / expected[2]}" in result.stderr
    assert source.read_bytes() == google_v30.read_bytes()  # the input is untouched


def test_default_output_for_directory_and_stdin(google_v30, tmp_path, monkeypatch):
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "a.vcf").write_bytes(google_v30.read_bytes())
    assert run("merge", folder).exit_code == 0
    assert (tmp_path / "exports_merged.vcf").exists()
    monkeypatch.chdir(tmp_path)
    assert run("merge", "-", input=google_v30.read_bytes()).exit_code == 0
    assert len(read_vcards(tmp_path / "stdin_merged.vcf")) == 55


def test_merge_writes_40(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    assert run("merge", google_v30, "-o", out, "--vcard-version", "4.0").exit_code == 0
    assert {c.source_version for c in read_vcards(out)} == {"4.0"}


def test_split_by_group(google_v30, legacy_v21_gbk, tmp_path):
    result = run("split", google_v30, legacy_v21_gbk, "-d", tmp_path, "--by", "group")
    assert result.exit_code == 0
    work_ops = read_vcards(tmp_path / "work-ops.vcf")
    assert [c.kind for c in work_ops].count("group") == 1  # the legacy group card comes first
    assert work_ops[0].kind == "group"
    assert len(work_ops) == 1 + 7
    assert len(read_vcards(tmp_path / "ungrouped.vcf")) == 16  # every Google contact has a label


def test_split_needs_exactly_one_mode(google_v30, tmp_path):
    result = run("split", google_v30, "-d", tmp_path, "--per-file", 1, "--by", "group")
    assert result.exit_code == 2
    assert run("split", google_v30, "-d", tmp_path, "--max-size", "huge").exit_code == 2


def test_info(legacy_v21_gbk):
    result = run("info", legacy_v21_gbk)
    assert result.exit_code == 0
    assert "79 (72 contacts, 7 groups)" in result.output


def test_validate_exit_codes(google_v30, legacy_v21_gbk):
    assert run("validate", google_v30).exit_code == 0
    result = run("validate", legacy_v21_gbk, "--errors-only")
    assert result.exit_code == 1
    assert result.stdout.count("error: missing FN") == 8
    assert "warning" not in result.stdout


def test_missing_input_is_a_usage_error(tmp_path):
    result = run("info", tmp_path / "missing.vcf")
    assert result.exit_code == 2
    assert "no such file" in result.output
