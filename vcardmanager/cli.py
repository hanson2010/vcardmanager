"""Command-line interface."""

import csv
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import click

from vcardmanager import __version__
from vcardmanager.clean import DEFAULT_RULES, RULES, iter_clean, needs_all_cards
from vcardmanager.clean.phones import FORMATS
from vcardmanager.config import ConfigError, defaults_for, load_rules
from vcardmanager.dedupe import MATCH_KINDS, DuplicateGroup, apply_merges, find_duplicate_groups
from vcardmanager.groups import groups_to_categories
from vcardmanager.info import format_summary, summarize
from vcardmanager.io import (
    LINE_ENDINGS,
    STDIO,
    OutputError,
    check_output,
    collect_inputs,
    default_output,
    iter_inputs,
    write_output,
)
from vcardmanager.model import VCard
from vcardmanager.split import parse_size, split_cards, write_split
from vcardmanager.strip import PRESETS, needs_group_conversion, parse_patterns, strip_card
from vcardmanager.validate import ERROR, validate
from vcardmanager.writer import SUPPORTED_VERSIONS

F = TypeVar("F", bound=Callable[..., Any])

PROGRESS_MIN_CARDS = 1000  # smaller jobs finish before a progress bar would help


# ---------------------------------------------------------------- shared options
def _apply_rules_file(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Eager callback: load the TOML file before the other options, so it can supply their defaults."""
    if value is None or ctx.command is None:
        return value
    try:
        defaults = defaults_for(load_rules(value), ctx.command)
    except (OSError, ConfigError) as exc:
        raise click.BadParameter(str(exc), ctx, param) from exc
    ctx.default_map = {**(ctx.default_map or {}), **defaults}
    return value


def _remember(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    ctx.meta[f"vcardmanager.{param.name}"] = value
    return value


def common_options(func: F) -> F:
    func = click.option("-q", "--quiet", is_flag=True, expose_value=False, callback=_remember,
                        help="Print only errors.")(func)  # fmt: skip
    func = click.option("-v", "--verbose", is_flag=True, expose_value=False, callback=_remember,
                        help="List every warning.")(func)  # fmt: skip
    return click.option(
        "--rules",
        type=click.Path(exists=True, dir_okay=False),
        is_eager=True,
        expose_value=False,
        callback=_apply_rules_file,
        help="TOML file with default options; command-line options override it.",
    )(func)


def _input_options(required: bool) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        func = click.option("-r", "--recursive", is_flag=True, help="Also read vCard files in subdirectories.")(func)
        func = click.option("--encoding", metavar="NAME", help="Encoding of the input files (default: detect).")(func)
        return click.argument("inputs", nargs=-1, required=required, metavar="INPUT...")(func)

    return decorate


input_options = _input_options(required=True)
optional_input_options = _input_options(required=False)  # for `clean`, whose --list-rules needs no input


def output_options(func: F) -> F:
    func = click.option("--force", is_flag=True, help="Overwrite existing files, including inputs.")(func)
    func = click.option(
        "--line-ending", type=click.Choice(sorted(LINE_ENDINGS)), default="crlf", show_default=True,
        help="Line endings to write; vCard requires CRLF.",
    )(func)  # fmt: skip
    return click.option(
        "--vcard-version",
        type=click.Choice(SUPPORTED_VERSIONS),
        default="3.0",
        show_default=True,
        help="vCard version to write.",
    )(func)


def _flag(name: str) -> bool:
    return bool(click.get_current_context().meta.get(f"vcardmanager.{name}"))


def say(message: str) -> None:
    """A progress or summary message on stderr, unless --quiet."""
    if not _flag("quiet"):
        click.echo(message, err=True)


@dataclass
class Tally:
    """Counts cards and collects their warnings as they stream past, without keeping the cards."""

    cards: int = 0
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def watch(self, cards: Iterable[VCard]) -> Iterator[VCard]:
        for card in cards:
            self.cards += 1
            self.warnings.extend((card.source or "?", warning) for warning in card.warnings)
            yield card


def show_warnings(source: Tally | list[VCard]) -> None:
    if isinstance(source, Tally):
        warnings = source.warnings
    else:
        warnings = [(card.source or "?", w) for card in source for w in card.warnings]
    if _flag("verbose") and not _flag("quiet"):
        for where, warning in warnings:
            click.echo(f"{where}: {warning}", err=True)
    elif warnings:
        say(f"{len(warnings)} warnings (use -v to list them)")


def progress(label: str) -> Callable[[list[VCard]], Iterator[VCard]]:
    """Wrap a per-card loop with a progress bar on stderr for large jobs; click hides it when not a terminal."""

    def wrap(cards: list[VCard]) -> Iterator[VCard]:
        if _flag("quiet") or len(cards) < PROGRESS_MIN_CARDS:
            yield from cards
            return
        with click.progressbar(cards, label=label, file=sys.stderr) as bar:
            yield from bar

    return wrap


def stream(inputs: tuple[str, ...], recursive: bool, encoding: str | None) -> Iterator[VCard]:
    """Cards from all inputs, one at a time. Read errors become usage errors when they happen."""
    try:
        yield from iter_inputs(inputs, recursive, encoding)
    except (FileNotFoundError, LookupError, UnicodeDecodeError) as exc:
        raise click.UsageError(str(exc)) from exc


def load(inputs: tuple[str, ...], recursive: bool, encoding: str | None) -> list[VCard]:
    """All cards at once, for commands that need to see every card (split, dedupe)."""
    return list(stream(inputs, recursive, encoding))


def output_option(suffix: str) -> Callable[[F], F]:
    return click.option(
        "-o",
        "--output",
        metavar="FILE",
        help=f"Output file, or - for stdout. Default: a new file next to the first input, "
        f"e.g. contacts_{suffix}.vcf (numbered if that exists).",
    )


def prepare_output(output: str | None, inputs: tuple[str, ...], recursive: bool, force: bool, suffix: str) -> str:
    """The file to write: `output` if given (refused if it exists, unless --force), else a new default name."""
    try:
        files = collect_inputs(inputs, recursive)
        if output is None:
            return default_output(inputs, suffix)
        check_output(output, files, force)
        return output
    except (FileNotFoundError, OutputError) as exc:
        raise click.UsageError(str(exc)) from exc


def written_to(output: str) -> str:
    return "" if output == STDIO else f" to {output}"


# ---------------------------------------------------------------- commands
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="vcardmanager")
def main() -> None:
    """Merge, split, clean, strip and deduplicate vCard (.vcf) files.

    INPUT can be files, directories or `-` for stdin.
    """


@main.command()
@input_options
@output_options
@output_option("merged")
@common_options
def merge(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    output: str | None,
    force: bool,
    vcard_version: str,
    line_ending: str,
) -> None:
    """Combine the contacts of all inputs into one file."""
    target = prepare_output(output, inputs, recursive, force, "merged")
    tally = Tally()
    write_output(tally.watch(stream(inputs, recursive, encoding)), target, vcard_version, line_ending)
    show_warnings(tally)
    say(f"Wrote {tally.cards} cards{written_to(target)}")


@main.command()
@input_options
@output_options
@click.option("-d", "--directory", required=True, help="Directory for the output files (created if missing).")
@click.option("--per-file", type=click.IntRange(min=1), help="Contacts per file; 1 names each file after its FN.")
@click.option("--max-size", metavar="SIZE", help="Maximum file size, e.g. 500KB or 1MB.")
@click.option("--by", metavar="group|PROPERTY", help="One file per contact group, or per value of a property.")
@common_options
def split(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    force: bool,
    vcard_version: str,
    line_ending: str,
    directory: str,
    per_file: int | None,
    max_size: str | None,
    by: str | None,
) -> None:
    """Split the contacts into several files."""
    if sum(option is not None for option in (per_file, max_size, by)) != 1:
        raise click.UsageError("give exactly one of --per-file, --max-size, --by")
    try:
        max_bytes = parse_size(max_size) if max_size is not None else None
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--max-size") from exc
    cards = load(inputs, recursive, encoding)
    chunks = split_cards(cards, per_file=per_file, max_bytes=max_bytes, by=by, version=vcard_version)
    try:
        paths = write_split(chunks, directory, vcard_version, force, line_ending)
    except OutputError as exc:
        raise click.UsageError(str(exc)) from exc
    show_warnings(cards)
    say(f"Wrote {len(paths)} files to {directory}")


@main.command()
@optional_input_options
@output_options
@output_option("cleaned")
@click.option("--region", metavar="CC", help="Country assumed for phone numbers without a country code, e.g. CN.")
@click.option("--phone-format", type=click.Choice(sorted(FORMATS)), default="e164", show_default=True)
@click.option("--enable", multiple=True, metavar="RULE", help="Turn on a rule that is off by default.")
@click.option("--disable", multiple=True, metavar="RULE", help="Turn off a rule that is on by default.")
@click.option("--photo-timeout", type=float, default=10.0, show_default=True, help="Seconds per photo download.")
@click.option(
    "--photo-max-px",
    type=click.IntRange(min=16),
    default=512,
    show_default=True,
    help="Longest side of embedded photos; larger ones are shrunk (photo-compression).",
)
@click.option("--photo-quality", type=click.IntRange(1, 95), default=85, show_default=True, help="JPEG quality.")
@click.option("--list-rules", is_flag=True, help="List the rules and exit.")
@common_options
def clean(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    force: bool,
    vcard_version: str,
    line_ending: str,
    output: str | None,
    region: str | None,
    phone_format: str,
    enable: tuple[str, ...],
    disable: tuple[str, ...],
    photo_timeout: float,
    photo_max_px: int,
    photo_quality: int,
    list_rules: bool,
) -> None:
    """Normalize contacts with a set of rules (see --list-rules)."""
    if list_rules:
        width = max(len(rule.name) for rule in RULES)
        for rule in RULES:
            click.echo(f"{rule.name:<{width}}  {'on ' if rule.default else 'off'}  {rule.description}")
        return
    if not inputs:
        raise click.UsageError("missing INPUT")
    rules = (set(DEFAULT_RULES) | set(enable)) - set(disable)
    target = prepare_output(output, inputs, recursive, force, "cleaned")
    tally = Tally()
    try:
        cleaned = iter_clean(
            stream(inputs, recursive, encoding),
            rules,
            region=region,
            phone_format=phone_format,
            photo_max_px=photo_max_px,
            photo_quality=photo_quality,
            photo_timeout=photo_timeout,
            # whole-file rules read every card first; then a progress bar helps on large inputs
            progress=progress("Cleaning") if needs_all_cards(rules) else None,
            log=say,  # e.g. one line per photo download; silent with -q
        )
        write_output(tally.watch(cleaned), target, vcard_version, line_ending)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    show_warnings(tally)
    say(f"Cleaned {tally.cards} cards{written_to(target)}")


@main.command()
@input_options
@output_options
@output_option("stripped")
@click.option(
    "-f",
    "--field",
    "fields",
    multiple=True,
    metavar="PATTERN",
    help="Remove matching properties: PHOTO, X-*, TEL[TYPE=FAX].",
)
@click.option("-k", "--keep", multiple=True, metavar="PATTERNS", help="Keep only these, e.g. FN,N,TEL,EMAIL.")
@click.option(
    "--preset",
    type=click.Choice(sorted(PRESETS)),
    help="minimal: keep FN, N, TEL, EMAIL. privacy: drop PHOTO, NOTE, BDAY, GEO, ADR, X-*.",
)
@common_options
def strip(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    force: bool,
    vcard_version: str,
    line_ending: str,
    output: str | None,
    fields: tuple[str, ...],
    keep: tuple[str, ...],
    preset: str | None,
) -> None:
    """Remove properties from every contact. FN is always kept."""
    field_texts, keep_texts = list(fields), list(keep)
    if preset:
        field_texts += PRESETS[preset].get("fields", [])
        keep_texts += PRESETS[preset].get("keep", [])
    if not field_texts and not keep_texts:
        raise click.UsageError("give --field, --keep or --preset")
    try:
        field_patterns = parse_patterns(field_texts)
        keep_patterns = parse_patterns(keep_texts) if keep_texts else None
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    target = prepare_output(output, inputs, recursive, force, "stripped")
    tally = Tally()
    removed = written = lost_groups = moved_groups = 0
    source: Iterable[VCard] = tally.watch(stream(inputs, recursive, encoding))
    if needs_group_conversion(field_patterns, keep_patterns):
        # the patterns remove what defines groups: keep membership in CATEGORIES (this needs every card)
        cards = list(source)
        moved_groups = sum(card.kind == "group" for card in cards)
        source = groups_to_categories(cards)

    def stripped_cards() -> Iterator[VCard]:
        nonlocal removed, written, lost_groups
        for card in source:
            stripped, count = strip_card(card, field_patterns, keep_patterns)
            removed += count
            written += 1
            lost_groups += card.kind == "group" and stripped.kind != "group"
            yield stripped

    write_output(stripped_cards(), target, vcard_version, line_ending)
    show_warnings(tally)
    if moved_groups:
        say(
            f"Moved the membership of {moved_groups} group cards into CATEGORIES, "
            "then removed the group cards and GROUPING links"
        )
    say(f"Removed {removed} other properties; wrote {written} of {tally.cards} cards{written_to(target)}")
    if lost_groups:
        click.echo(
            f"Warning: {lost_groups} group cards are no longer group cards, so their membership is lost. "
            "To keep it, keep CATEGORIES, so that strip can move the membership there.",
            err=True,
        )


def _describe(group: DuplicateGroup, number: int) -> str:
    lines = [f"Group {number}: {len(group.cards)} cards, matched by {', '.join(sorted(group.reasons))}"]
    for card in group.cards:
        fn = card.get("FN")
        details = [p.value for p in card.get_all("TEL")[:2] + card.get_all("EMAIL")[:2]]
        lines.append(f"  {fn.value if fn else '(no FN)'}  [{', '.join(details)}]  {card.source or ''}")
    return "\n".join(lines)


@main.command()
@input_options
@output_options
@output_option("deduped")
@click.option(
    "--match",
    default=",".join(MATCH_KINDS),
    show_default=True,
    help="What links two cards: any of phone, email, name (comma-separated).",
)
@click.option(
    "--name-threshold",
    type=click.IntRange(0, 100),
    default=100,
    show_default=True,
    help="Name similarity (0-100) that counts as a match; 100 means exact.",
)
@click.option(
    "--ignore-phone", "ignore_phones", multiple=True, metavar="NUMBER", help="Shared number that links nobody."
)
@click.option("--region", metavar="CC", help="Country assumed for phone numbers without a country code.")
@click.option("--dry-run", is_flag=True, help="Only list the groups that would be merged.")
@click.option("--report", type=click.Path(dir_okay=False), help="Write a CSV of the merged groups.")
@click.option("-i", "--interactive", is_flag=True, help="Ask before merging each group.")
@common_options
def dedupe(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    force: bool,
    vcard_version: str,
    line_ending: str,
    output: str | None,
    match: str,
    name_threshold: int,
    ignore_phones: tuple[str, ...],
    region: str | None,
    dry_run: bool,
    report: str | None,
    interactive: bool,
) -> None:
    """Find contacts that are the same person and merge them."""
    target = STDIO
    if not dry_run:
        target = prepare_output(output, inputs, recursive, force, "deduped")
        if report:
            try:
                check_output(report, force=force)
            except OutputError as exc:
                raise click.UsageError(str(exc)) from exc
    cards = load(inputs, recursive, encoding)
    try:
        groups = find_duplicate_groups(
            cards,
            [kind.strip() for kind in match.split(",") if kind.strip()],
            name_threshold=name_threshold,
            ignore_phones=ignore_phones,
            region=region,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    if dry_run:
        for number, group in enumerate(groups, 1):
            click.echo(_describe(group, number) + "\n")
        say(f"{len(groups)} groups, {sum(len(g.cards) for g in groups)} cards would be merged")
        return
    if interactive:
        chosen = []
        for number, group in enumerate(groups, 1):
            click.echo(_describe(group, number), err=True)
            if click.confirm("Merge these?", default=True, err=True):
                chosen.append(group)
        groups = chosen

    result = apply_merges(cards, [g.cards for g in groups])
    write_output(result, target, vcard_version, line_ending)
    if report:
        _write_report(report, groups)
    show_warnings(cards)
    merged = sum(len(g.cards) for g in groups)
    say(f"Merged {merged} cards into {len(groups)}; {len(result)} cards written{written_to(target)}")


def _write_report(path: str, groups: list[DuplicateGroup]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:  # BOM so Excel reads UTF-8
        writer = csv.writer(handle)
        writer.writerow(["group", "matched_by", "fn", "tel", "email", "source"])
        for number, group in enumerate(groups, 1):
            for card in group.cards:
                fn = card.get("FN")
                writer.writerow(
                    [
                        number,
                        " ".join(sorted(group.reasons)),
                        fn.value if fn else "",
                        " ".join(p.value for p in card.get_all("TEL")),
                        " ".join(p.value for p in card.get_all("EMAIL")),
                        card.source or "",
                    ]
                )


@main.command()
@input_options
@common_options
def info(inputs: tuple[str, ...], recursive: bool, encoding: str | None) -> None:
    """Show how many cards there are, which versions and properties they use, and parse warnings."""
    click.echo(format_summary(summarize(stream(inputs, recursive, encoding))))


@main.command(name="validate")
@input_options
@click.option("--errors-only", is_flag=True, help="Do not list warnings.")
@common_options
def validate_command(inputs: tuple[str, ...], recursive: bool, encoding: str | None, errors_only: bool) -> None:
    """Report problems against the vCard standard. Exits with status 1 if there are errors."""
    errors = warnings = 0
    for card in stream(inputs, recursive, encoding):
        for issue in validate(card):
            if issue.level == ERROR:
                errors += 1
            else:
                warnings += 1
                if errors_only:
                    continue
            click.echo(f"{card.source or '?'}: {issue.level}: {issue.message}")
    say(f"{errors} errors, {warnings} warnings")
    if errors:
        raise SystemExit(1)
