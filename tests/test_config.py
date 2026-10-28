import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from vcardmanager import read_vcards
from vcardmanager.cli import clean, dedupe, main
from vcardmanager.config import ConfigError, defaults_for, load_rules

RULES = """
[clean]
region = "CN"
phone-format = "international"
enable = ["drop-extensions"]

[dedupe]
match = ["email"]
ignore-phones = ["+86 10 5765 0000"]

[output]
version = "4.0"
line-ending = "lf"
"""


@pytest.fixture
def rules_file(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(RULES, encoding="utf-8")
    return path


def run(*args, input=None):
    return CliRunner().invoke(main, [str(a) for a in args], input=input)


def test_defaults_for_each_command(rules_file):
    config = load_rules(rules_file)
    assert defaults_for(config, clean) == {
        "vcard_version": "4.0",
        "line_ending": "lf",
        "region": "CN",
        "phone_format": "international",
        "enable": ["drop-extensions"],
    }
    assert defaults_for(config, dedupe)["match"] == "email"  # a TOML list becomes the comma-separated option
    assert defaults_for(config, dedupe)["ignore_phones"] == ["+86 10 5765 0000"]
    assert defaults_for(config, main.commands["validate"]) == {}  # [output] only applies to writing commands


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[clean]\nregoin = 'CN'\n", "unknown key `regoin`"),
        ("[output]\ncolour = 'red'\n", "unknown key `colour`"),
        ("[clean]\ninputs = ['x.vcf']\n", "unknown key `inputs`"),  # paths stay on the command line
        ("clean = 1\n", "must be a [section]"),
        ("[clean\n", "rules.toml"),
    ],
)
def test_bad_rules_files(tmp_path, text, message):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message.replace("[", r"\[")):
        defaults_for(load_rules(path), clean)


def test_rules_file_applies_and_command_line_wins(rules_file, legacy_v21_gbk, copy_of):
    out = copy_of(legacy_v21_gbk)
    assert run("clean", out, "--rules", rules_file).exit_code == 0
    data = out.read_bytes()
    assert b"\r\n" not in data and b"VERSION:4.0\n" in data
    cards = read_vcards(out)
    assert not [p for c in cards for p in c.properties if p.name.startswith("X-")]  # drop-extensions enabled
    overridden = copy_of(legacy_v21_gbk, "overridden.vcf")
    result = run("clean", overridden, "--vcard-version", "3.0", "--rules", rules_file)
    assert result.exit_code == 0
    assert {c.source_version for c in read_vcards(overridden)} == {"3.0"}


def test_rules_file_errors_are_usage_errors(tmp_path, google_v30):
    bad = tmp_path / "bad.toml"
    bad.write_text("[strip]\nfeild = ['PHOTO']\n", encoding="utf-8")
    result = run("strip", google_v30, "--rules", bad)
    assert result.exit_code == 2
    assert "unknown key `feild`" in result.output
    assert run("info", google_v30, "--rules", tmp_path / "missing.toml").exit_code == 2


def test_quiet_and_verbose(legacy_v21_gbk, copy_of):
    source = copy_of(legacy_v21_gbk)
    normal = run("merge", source)
    assert "8 warnings (use -v to list them)" in normal.stderr
    assert "Wrote 79 cards" in normal.stderr
    quiet = run("merge", source, "-q")
    assert quiet.stderr == ""
    verbose = run("merge", source, "-v")
    assert verbose.stderr.count(": missing FN") == 8


def test_line_ending_lf(google_v30, copy_of, tmp_path):
    assert run("merge", copy_of(google_v30), "--line-ending", "lf").exit_code == 0
    out = tmp_path / "google_v30_merged.vcf"
    assert b"\r" not in out.read_bytes()
    assert read_vcards(out) == read_vcards(google_v30)


def test_rules_example_is_valid(tmp_path):
    example = (Path(__file__).parents[1] / "rules.toml.example").read_text(encoding="utf-8")
    assert defaults_for(load_rules_text(tmp_path, example), clean) == {}  # everything is commented out
    uncommented = re.sub(r"^# (?=[a-z-]+ = )", "", example, flags=re.MULTILINE)
    config = load_rules_text(tmp_path, uncommented)
    for command in main.commands.values():
        defaults_for(config, command)  # raises ConfigError on an unknown key
    assert defaults_for(config, main.commands["strip"])["fields"] == ["PHOTO", "X-*", "TEL[TYPE=FAX]"]


def load_rules_text(tmp_path, text):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return load_rules(path)


def test_strip_options_from_rules_file(google_v30, copy_of, tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text(
        '[strip]\nfields = ["PHOTO", "NOTE"]\ndrop-types = ["EMAIL=INTERNET"]\nquiet = true\n', encoding="utf-8"
    )
    source = copy_of(google_v30)
    result = run("strip", source, "--rules", rules)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert not {p.name for c in read_vcards(source) for p in c.properties} & {"PHOTO", "NOTE"}
    assert b"TYPE=INTERNET" not in source.read_bytes()
