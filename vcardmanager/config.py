"""The TOML rules file: default option values per command."""

import os
import tomllib
from typing import Any

import click

# [output] applies to every command that writes vCards
OUTPUT_KEYS = {"version": "vcard_version", "line-ending": "line_ending"}
WRITING_COMMANDS = frozenset({"merge", "split", "clean", "strip", "dedupe"})
# options that only make sense on the command line
_NOT_CONFIGURABLE = frozenset({"inputs", "output", "directory", "force", "rules", "help", "list_rules"})


class ConfigError(Exception):
    pass


def load_rules(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "rb") as handle:
        try:
            config = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    for section, values in config.items():
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: `{section}` must be a [section]")
    return config


def defaults_for(config: dict[str, Any], command: click.Command) -> dict[str, Any]:
    """Turn the file's `[output]` and `[<command>]` sections into a click `default_map` for `command`.

    Keys use the option names without dashes (`phone-format`); unknown keys raise ConfigError.
    """
    name = command.name or ""
    allowed = {p.name for p in command.params if p.name} - _NOT_CONFIGURABLE
    defaults: dict[str, Any] = {}
    if name in WRITING_COMMANDS:
        for key, value in config.get("output", {}).items():
            if key not in OUTPUT_KEYS:
                raise ConfigError(f"[output] has unknown key `{key}`; use {', '.join(OUTPUT_KEYS)}")
            defaults[OUTPUT_KEYS[key]] = value
    for key, value in config.get(name, {}).items():
        option = key.replace("-", "_")
        if option not in allowed:
            valid = ", ".join(sorted(o.replace("_", "-") for o in allowed))
            raise ConfigError(f"[{name}] has unknown key `{key}`; valid keys: {valid}")
        if option == "match" and isinstance(value, list):
            value = ",".join(value)
        defaults[option] = value
    return defaults
