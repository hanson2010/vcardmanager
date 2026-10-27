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
        ("[clean]\noutput = 'x.vcf'\n", "unknown key `output`"),  # paths stay on the command line
        ("clean = 1\n", "must be a [section]"),
        ("[clean\n", "rules.toml"),
    ],
)
def test_bad_rules_files(tmp_path, text, message):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message.replace("[", r"\[")):
        defaults_for(load_rules(path), clean)


def test_readme_example_is_valid(tmp_path):
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    example = readme.split("```toml\n", 1)[1].split("```", 1)[0]
    path = tmp_path / "rules.toml"
    path.write_text(example, encoding="utf-8")
    config = load_rules(path)
    for command in main.commands.values():
        defaults_for(config, command)  # raises ConfigError on an unknown key


def test_rules_file_applies_and_command_line_wins(rules_file, legacy_v21_gbk, tmp_path):
    out = tmp_path / "out.vcf"
    assert run("clean", legacy_v21_gbk, "-o", out, "--rules", rules_file).exit_code == 0
    data = out.read_bytes()
    assert b"\r\n" not in data and b"VERSION:4.0\n" in data
    cards = read_vcards(out)
    assert not [p for c in cards for p in c.properties if p.name.startswith("X-")]  # drop-extensions enabled
    overridden = tmp_path / "overridden.vcf"
    result = run("clean", legacy_v21_gbk, "-o", overridden, "--vcard-version", "3.0", "--rules", rules_file)
    assert result.exit_code == 0
    assert {c.source_version for c in read_vcards(overridden)} == {"3.0"}


def test_rules_file_errors_are_usage_errors(tmp_path, google_v30):
    bad = tmp_path / "bad.toml"
    bad.write_text("[strip]\nfeild = ['PHOTO']\n", encoding="utf-8")
    result = run("strip", google_v30, "--rules", bad)
    assert result.exit_code == 2
    assert "unknown key `feild`" in result.output
    assert run("info", google_v30, "--rules", tmp_path / "missing.toml").exit_code == 2


def test_quiet_and_verbose(legacy_v21_gbk, tmp_path):
    normal = run("merge", legacy_v21_gbk, "-o", tmp_path / "a.vcf")
    assert "8 warnings (use -v to list them)" in normal.stderr
    assert "Wrote 79 cards" in normal.stderr
    quiet = run("merge", legacy_v21_gbk, "-o", tmp_path / "b.vcf", "-q")
    assert quiet.stderr == ""
    verbose = run("merge", legacy_v21_gbk, "-o", tmp_path / "c.vcf", "-v")
    assert verbose.stderr.count(": missing FN") == 8


def test_line_ending_lf(google_v30, tmp_path):
    out = tmp_path / "out.vcf"
    assert run("merge", google_v30, "-o", out, "--line-ending", "lf").exit_code == 0
    assert b"\r" not in out.read_bytes()
    assert read_vcards(out) == read_vcards(google_v30)
