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


def test_merge_split_merge_is_lossless(google_v30, legacy_v21_gbk, copy_of, tmp_path):
    assert run("merge", copy_of(google_v30), copy_of(legacy_v21_gbk)).exit_code == 0
    merged = tmp_path / "google_v30_merged.vcf"
    assert run("split", merged, "-d", tmp_path / "parts", "--per-file", 1).exit_code == 0
    assert len(list((tmp_path / "parts").glob("*.vcf"))) == 55 + 79
    assert run("merge", tmp_path / "parts").exit_code == 0
    assert by_uid(read_vcards(tmp_path / "parts_merged.vcf")) == by_uid(read_vcards(merged))


def test_merge_never_overwrites(google_v30, copy_of, tmp_path):
    source = copy_of(google_v30, "contacts.vcf")
    for _ in range(3):
        result = run("merge", source)
        assert result.exit_code == 0, result.output
    expected = ["contacts_merged.vcf", "contacts_merged_2.vcf", "contacts_merged_3.vcf"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["contacts.vcf", *expected]
    assert f"to {tmp_path / expected[2]}" in result.stderr
    assert source.read_bytes() == google_v30.read_bytes()  # the input is untouched


def test_merge_from_directory_and_stdin(google_v30, tmp_path, monkeypatch):
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "a.vcf").write_bytes(google_v30.read_bytes())
    assert run("merge", folder).exit_code == 0
    assert (tmp_path / "exports_merged.vcf").exists()
    monkeypatch.chdir(tmp_path)
    assert run("merge", "-", input=google_v30.read_bytes()).exit_code == 0
    assert len(read_vcards(tmp_path / "stdin_merged.vcf")) == 55


@pytest.mark.parametrize(
    ("command", "options"),
    [("clean", ["--disable", "photo-embed"]), ("strip", ["--field", "PHOTO"]), ("dedupe", [])],
)
def test_in_place_with_backup(google_v30, legacy_v21_gbk, copy_of, tmp_path, command, options):
    first, second = copy_of(google_v30), copy_of(legacy_v21_gbk)
    result = run(command, first, second, *options)
    assert result.exit_code == 0, result.output
    assert "in 2 files" in result.stderr
    assert first.read_bytes() != google_v30.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["google_v30.vcf", "legacy_v21_gbk.vcf"]
    for _ in range(2):
        assert run(command, first, "--backup", *options).exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "google_v30.vcf", "google_v30_backup.vcf", "google_v30_backup_2.vcf", "legacy_v21_gbk.vcf"
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("command", "options"),
    [("clean", ["--disable", "photo-embed"]), ("strip", ["--field", "PHOTO"])],
)
def test_dry_run_writes_nothing(google_v30, copy_of, tmp_path, command, options):
    target = copy_of(google_v30)
    result = run(command, target, "--dry-run", "--backup", *options)
    assert result.exit_code == 0, result.output
    assert f"Would change {target}" in result.stderr
    assert "1 of 1 files would change; nothing was written" in result.stderr
    assert target.read_bytes() == google_v30.read_bytes()
    assert [p.name for p in tmp_path.iterdir()] == ["google_v30.vcf"]


def test_dry_run_reports_unchanged_files(google_v30, copy_of):
    target = copy_of(google_v30)
    assert run("strip", target, "--field", "PHOTO").exit_code == 0
    result = run("strip", target, "--field", "PHOTO", "--dry-run")
    assert "Would change" not in result.stderr
    assert "0 of 1 files would change" in result.stderr


def test_dry_run_from_stdin_writes_no_stdout(google_v30):
    result = run("strip", "-", "--field", "PHOTO", "--dry-run", input=google_v30.read_bytes())
    assert result.exit_code == 0
    assert result.stdout_bytes == b""
    assert "Would change stdin" in result.stderr


@pytest.mark.parametrize(("answers", "removed"), [("q\n", 0), ("y\nq\n", 1), ("n\ny\nq\n", 1), ("a\n", None)])
def test_strip_interactive(google_v30, copy_of, answers, removed):
    target = copy_of(google_v30)
    notes = google_v30.read_bytes().count(b"\nNOTE:")
    assert notes > 2
    result = run("strip", target, "-i", "--field", "NOTE", input=answers)
    assert result.exit_code == 0, result.output
    assert "-NOTE:" in result.stderr
    assert target.read_bytes().count(b"\nNOTE:") == (0 if removed is None else notes - removed)


def test_clean_interactive_all_matches_plain_clean(google_v30, copy_of):
    plain, asked = copy_of(google_v30), copy_of(google_v30, "asked.vcf")
    assert run("clean", plain, "--disable", "photo-embed").exit_code == 0
    result = run("clean", asked, "--disable", "photo-embed", "-i", input="a\n")
    assert result.exit_code == 0, result.output
    assert asked.read_bytes() == plain.read_bytes()


def test_interactive_refuses_stdin(google_v30):
    result = run("strip", "-", "-i", "--field", "PHOTO", input=google_v30.read_bytes())
    assert result.exit_code == 2
    assert "cannot be `-`" in result.output


def test_in_place_from_stdin_writes_stdout(google_v30):
    result = run("strip", "-", "--field", "PHOTO", input=google_v30.read_bytes())
    assert result.exit_code == 0
    assert result.stdout_bytes.count(b"BEGIN:VCARD\r\n") == 55
    assert b"\r\r\n" not in result.stdout_bytes
    assert b"PHOTO" not in result.stdout_bytes


def test_merge_writes_40(google_v30, copy_of, tmp_path):
    assert run("merge", copy_of(google_v30), "--vcard-version", "4.0").exit_code == 0
    assert {c.source_version for c in read_vcards(tmp_path / "google_v30_merged.vcf")} == {"4.0"}


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
