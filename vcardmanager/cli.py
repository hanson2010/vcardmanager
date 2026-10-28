"""Command-line interface."""

import csv
import difflib
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import unquote, urlparse

import click

from vcardmanager import __version__
from vcardmanager.clean import DEFAULT_RULES, RULES, iter_clean_pairs, needs_all_cards
from vcardmanager.clean.phones import FORMATS
from vcardmanager.carddav import AddressBook, CardDAVError, Changes, Resource, plan_changes
from vcardmanager.config import ConfigError, defaults_for, load_rules
from vcardmanager.dedupe import DEFAULT_MATCH, MATCH_KINDS, DuplicateGroup, apply_merges, find_duplicate_groups
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
    make_backup,
    safe_filename,
    write_output,
)
from vcardmanager.model import VCard
from vcardmanager.split import parse_size, split_cards, write_split
from vcardmanager.strip import (
    PRESETS,
    needs_group_conversion,
    parse_patterns,
    parse_type_patterns,
    strip_card,
    strip_types,
)
from vcardmanager.validate import ERROR, validate
from vcardmanager.writer import SUPPORTED_VERSIONS, dumps

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
optional_input_options = _input_options(required=False)  # for `clean --list-rules`


def output_options(func: F) -> F:
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


force_option = click.option("--force", is_flag=True, help="Overwrite existing files.")


username_option = click.option(
    "--username", metavar="NAME", help="CardDAV user name when INPUT is an address book URL; the password is asked for."
)


def in_place_options(func: F) -> F:
    func = username_option(func)
    return click.option(
        "-b",
        "--backup",
        is_flag=True,
        help="Keep a local copy of each input file or of the address book, e.g. contacts_backup.vcf.",
    )(func)


dry_run_option = click.option("--dry-run", is_flag=True, help="Only report what would change; write nothing.")
interactive_option = click.option(
    "-i", "--interactive", is_flag=True, help="Show each changed contact and ask before changing it."
)


def input_files(inputs: tuple[str, ...], recursive: bool) -> list[str]:
    try:
        return collect_inputs(inputs, recursive)
    except FileNotFoundError as exc:
        raise click.UsageError(str(exc)) from exc


def rewrite(path: str, cards: Iterable[VCard], vcard_version: str, line_ending: str, keep_backup: bool) -> None:
    """Replace an input file with `cards` (stdin: write them to stdout)."""
    if keep_backup and path != STDIO:
        make_backup(path)
    write_output(cards, path, vcard_version, line_ending)


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def connect(inputs: tuple[str, ...], username: str | None) -> AddressBook | None:
    """The address book when INPUT is its URL (asking for the password), or None for INPUT files."""
    if not inputs:
        raise click.UsageError("missing INPUT")
    if not any(is_url(i) for i in inputs):
        if username:
            raise click.UsageError("--username is only for an address book URL")
        return None
    if len(inputs) > 1:
        raise click.UsageError("give one address book URL, or files")
    if not username:
        raise click.UsageError("give --username with an address book URL")
    password = click.prompt(f"CardDAV password for {username}", hide_input=True, err=True)
    return AddressBook(inputs[0], username, password)


def read_cards(
    inputs: tuple[str, ...], recursive: bool, encoding: str | None, username: str | None
) -> Iterable[VCard]:
    """Cards from INPUT files, streamed, or from the address book INPUT points to."""
    book = connect(inputs, username)
    if book is None:
        return stream(inputs, recursive, encoding)
    try:
        resources = book.fetch()
    except CardDAVError as exc:
        raise click.ClickException(str(exc)) from exc
    say(f"Read {len(resources)} contacts from {book.url}")
    return [card for resource in resources for card in resource.cards]


def backup_address_book(url: str, resources: list[Resource]) -> str:
    """Save the address book as it is on the server to a new file in the current directory."""
    name = safe_filename(unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]))
    target = default_output([f"{name}.vcf"], "backup")
    with open(target, "w", encoding="utf-8", newline="") as handle:
        for resource in resources:
            handle.write(resource.text if resource.text.endswith("\n") else resource.text + "\r\n")
    return target


Transform = Callable[[Iterable[VCard]], Iterable[VCard] | None]

DIFF_WIDTH = 120  # long values such as photos are cut to this many characters


def _property_lines(card: VCard, version: str) -> list[str]:
    """The card as unfolded property lines, for showing a diff."""
    lines: list[str] = []
    for line in dumps([card], version, "\n").splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return [line if len(line) <= DIFF_WIDTH else line[: DIFF_WIDTH - 1] + "…" for line in lines]


@dataclass
class Reviewer:
    """Asks before each changed card is kept (--interactive); unchanged cards pass without a question."""

    version: str
    enabled: bool
    changed: int = 0
    accepted: int = 0
    answer: str = ""  # "a" (all) or "q" (quit) once given, for the rest of the run

    def review(self, before: VCard, after: VCard) -> VCard:
        if not self.enabled:
            return after
        old, new = _property_lines(before, self.version), _property_lines(after, self.version)
        if old == new:
            return after
        self.changed += 1
        if self.answer != "a":
            if self.answer == "q":
                return before
            fn = after.get("FN") or before.get("FN")
            click.echo(f"\n{fn.value if fn else '(no name)'} ({before.source or '?'})", err=True)
            for line in list(difflib.unified_diff(old, new, lineterm="", n=0))[2:]:
                if not line.startswith("@@"):
                    click.echo(line, err=True)
            self.answer = click.prompt(
                "Change this contact? [y]es, [n]o, [a]ll the rest, [q]uit (keep the rest)",
                type=click.Choice(["y", "n", "a", "q"]),
                default="y",
                show_choices=False,
                err=True,
            )
            if self.answer in ("n", "q"):
                return before
        self.accepted += 1
        return after

    def summary(self) -> None:
        if self.enabled:
            say(f"Changed {self.accepted} of {self.changed} contacts that had changes")


def check_interactive(inputs: tuple[str, ...]) -> None:
    if STDIO in inputs:
        raise click.UsageError("--interactive needs the answers on stdin, so INPUT cannot be `-`")


@dataclass
class InPlace:
    """What clean, strip and dedupe change: the input files, or a CardDAV address book."""

    files: list[str]
    book: AddressBook | None
    encoding: str | None
    vcard_version: str
    line_ending: str
    keep_backup: bool
    dry_run: bool = False

    @classmethod
    def from_options(cls, dry_run: bool = False) -> "InPlace":
        params = click.get_current_context().params
        inputs = params["inputs"]
        book = connect(inputs, params["username"])
        files = input_files(inputs, params["recursive"]) if book is None else []
        return cls(
            files,
            book,
            params["encoding"],
            params["vcard_version"],
            params["line_ending"],
            params["backup"],
            dry_run,
        )

    @property
    def where(self) -> str:
        """Where the result went, for the summary line."""
        if self.book is not None:
            return f" in {self.book.url}"
        written = [f for f in self.files if f != STDIO]
        if not written:
            return ""
        return f" in {written[0]}" if len(written) == 1 else f" in {len(written)} files"

    def run(self, transform: Transform) -> None:
        """Replace the cards of each file, or of the address book, with `transform(cards)`; None writes nothing.

        With `dry_run`, the cards are transformed and compared, but nothing is written.
        """
        if self.book is None:
            if self.dry_run:
                self._compare_files(transform)
                return
            for path in self.files:
                result = transform(stream((path,), False, self.encoding))
                if result is not None:
                    rewrite(path, result, self.vcard_version, self.line_ending, self.keep_backup)
            return
        try:
            resources = self.book.fetch()
            say(f"Read {len(resources)} contacts from {self.book.url}")
            if self.keep_backup and not self.dry_run:
                say(f"Saved them to {backup_address_book(self.book.url, resources)}")
            result = transform([card for resource in resources for card in resource.cards])
            if result is None:
                return
            changes = plan_changes(resources, result, self.vcard_version)
            if self.dry_run:
                say(
                    f"Server: {len(changes.updates)} contacts would be updated, {len(changes.creates)} added, "
                    f"{len(changes.deletes)} deleted; nothing was changed"
                )
                return
            failures = self._upload(self.book, changes)
        except CardDAVError as exc:
            raise click.ClickException(str(exc)) from exc
        say(
            f"Server: {len(changes.updates)} contacts updated, {len(changes.creates)} added, "
            f"{len(changes.deletes)} deleted"
        )
        if failures:
            for failure in failures:
                click.echo(f"Error: {failure}", err=True)
            raise click.ClickException(f"{len(failures)} changes were not saved on the server")

    @staticmethod
    def _upload(book: AddressBook, changes: Changes) -> list[str]:
        """Apply the changes on the server, saying how far it got after each request: each one takes a while."""
        total = len(changes.creates) + len(changes.updates) + len(changes.deletes)
        if total == 0 or _flag("quiet"):
            return book.apply(changes)
        count = 0
        width = len(str(total))

        def done(label: str, ok: bool) -> None:
            nonlocal count
            count += 1
            say(f"[{count:>{width}}/{total}] {label}{'' if ok else ' (failed)'}")

        say(f"Sending {total} changes to the server")
        return book.apply(changes, done)

    def _compare_files(self, transform: Transform) -> None:
        """Dry run on files: list those whose cards would change. Reads each file whole, to compare."""
        changed = 0
        for path in self.files:
            cards = list(stream((path,), False, self.encoding))
            before = dumps(cards, self.vcard_version)  # before the transform, which may change cards in place
            result = transform(cards)
            if result is not None and dumps(result, self.vcard_version) != before:
                changed += 1
                say(f"Would change {'stdin' if path == STDIO else path}")
        say(f"{changed} of {len(self.files)} files would change; nothing was written")


# ---------------------------------------------------------------- commands
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="vcardmanager")
def main() -> None:
    """Merge, split, clean, strip and deduplicate vCard (.vcf) files.

    INPUT can be files, directories, `-` for stdin, or a CardDAV address book URL (with --username).
    """


@main.command()
@input_options
@output_options
@common_options
def merge(inputs: tuple[str, ...], recursive: bool, encoding: str | None, vcard_version: str, line_ending: str) -> None:
    """Combine the contacts of all inputs into a new file, e.g. contacts_merged.vcf next to the first input."""
    input_files(inputs, recursive)
    target = default_output(inputs, "merged")
    tally = Tally()
    write_output(tally.watch(stream(inputs, recursive, encoding)), target, vcard_version, line_ending)
    show_warnings(tally)
    say(f"Wrote {tally.cards} cards to {target}")


@main.command()
@input_options
@output_options
@force_option
@click.option("-d", "--directory", required=True, help="Directory for the output files (created if missing).")
@click.option("--per-file", type=click.IntRange(min=1), help="Contacts per file; 1 names each file after its FN.")
@click.option("--max-size", metavar="SIZE", help="Maximum file size, e.g. 500KB or 1MB.")
@click.option("--by", metavar="group|PROPERTY", help="One file per contact group, or per value of a property.")
@common_options
def split(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    vcard_version: str,
    line_ending: str,
    force: bool,
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
@in_place_options
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
@dry_run_option
@interactive_option
@common_options
def clean(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    vcard_version: str,
    line_ending: str,
    backup: bool,
    username: str | None,
    region: str | None,
    phone_format: str,
    enable: tuple[str, ...],
    disable: tuple[str, ...],
    photo_timeout: float,
    photo_max_px: int,
    photo_quality: int,
    list_rules: bool,
    dry_run: bool,
    interactive: bool,
) -> None:
    """Normalize contacts in place with a set of rules (see --list-rules)."""
    if list_rules:
        width = max(len(rule.name) for rule in RULES)
        for rule in RULES:
            click.echo(f"{rule.name:<{width}}  {'on ' if rule.default else 'off'}  {rule.description}")
        return
    rules = (set(DEFAULT_RULES) | set(enable)) - set(disable)
    try:
        whole_file = needs_all_cards(rules)  # also checks the rule names before anything is touched
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    interactive = interactive and not dry_run
    if interactive:
        check_interactive(inputs)
    target = InPlace.from_options(dry_run)
    tally = Tally()
    reviewer = Reviewer(vcard_version, interactive)

    def clean_all(cards: Iterable[VCard]) -> Iterable[VCard]:
        pairs = iter_clean_pairs(
            cards,
            rules,
            region=region,
            phone_format=phone_format,
            photo_max_px=photo_max_px,
            photo_quality=photo_quality,
            photo_timeout=photo_timeout,
            # whole-file rules read every card first; then a progress bar helps on large inputs
            progress=progress("Cleaning") if whole_file and not interactive else None,
            log=say,  # e.g. one line per photo download; silent with -q
        )
        return tally.watch(reviewer.review(before, after) for before, after in pairs)

    try:
        target.run(clean_all)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    show_warnings(tally)
    reviewer.summary()
    say(f"{'Would clean' if dry_run else 'Cleaned'} {tally.cards} cards{target.where}")


@main.command()
@input_options
@output_options
@in_place_options
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
@click.option(
    "--drop-type",
    "drop_types",
    multiple=True,
    metavar="PROPERTY=TYPE",
    help="Remove a TYPE value but keep the property: EMAIL=INTERNET, *=PREF.",
)
@dry_run_option
@interactive_option
@common_options
def strip(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    vcard_version: str,
    line_ending: str,
    backup: bool,
    username: str | None,
    fields: tuple[str, ...],
    keep: tuple[str, ...],
    preset: str | None,
    drop_types: tuple[str, ...],
    dry_run: bool,
    interactive: bool,
) -> None:
    """Remove properties, or single TYPE values, from every contact, in place. FN is always kept."""
    field_texts, keep_texts = list(fields), list(keep)
    if preset:
        field_texts += PRESETS[preset].get("fields", [])
        keep_texts += PRESETS[preset].get("keep", [])
    if not field_texts and not keep_texts and not drop_types:
        raise click.UsageError("give --field, --keep, --preset or --drop-type")
    try:
        field_patterns = parse_patterns(field_texts)
        keep_patterns = parse_patterns(keep_texts) if keep_texts else None
        type_patterns = parse_type_patterns(drop_types)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    interactive = interactive and not dry_run
    if interactive:
        check_interactive(inputs)
    target = InPlace.from_options(dry_run)
    tally = Tally()
    reviewer = Reviewer(vcard_version, interactive)
    removed = types_removed = written = lost_groups = moved_groups = 0

    def stripped_cards(source: Iterable[VCard]) -> Iterator[VCard]:
        nonlocal removed, types_removed, written, lost_groups
        for card in source:
            stripped, count = strip_card(card, field_patterns, keep_patterns)
            stripped, type_count = strip_types(stripped, type_patterns) if type_patterns else (stripped, 0)
            kept = reviewer.review(card, stripped)
            if kept is stripped:
                removed += count
                types_removed += type_count
                lost_groups += card.kind == "group" and stripped.kind != "group"
            written += 1
            yield kept

    def strip_all(cards: Iterable[VCard]) -> Iterable[VCard]:
        nonlocal moved_groups
        source: Iterable[VCard] = tally.watch(cards)
        if needs_group_conversion(field_patterns, keep_patterns):
            # the patterns remove what defines groups: keep membership in CATEGORIES (this needs every card)
            whole = list(source)
            moved_groups += sum(card.kind == "group" for card in whole)
            source = groups_to_categories(whole)
        return stripped_cards(source)

    target.run(strip_all)
    show_warnings(tally)
    reviewer.summary()
    if moved_groups:
        verbs = ("Would move", "remove") if dry_run else ("Moved", "removed")
        say(
            f"{verbs[0]} the membership of {moved_groups} group cards into CATEGORIES, "
            f"then {verbs[1]} the group cards and GROUPING links"
        )
    types = f" and {types_removed} type values" if type_patterns else ""
    if dry_run:
        say(f"Would remove {removed} other properties{types} and keep {written} of {tally.cards} cards{target.where}")
    else:
        say(f"Removed {removed} other properties{types}; kept {written} of {tally.cards} cards{target.where}")
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
@in_place_options
@force_option
@click.option(
    "--match",
    default=",".join(DEFAULT_MATCH),
    show_default=True,
    help=f"What links two cards: any of {', '.join(MATCH_KINDS)} (comma-separated). "
    "pinyin: 陈磊 = Lei Chen.",
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
    vcard_version: str,
    line_ending: str,
    backup: bool,
    username: str | None,
    force: bool,
    match: str,
    name_threshold: int,
    ignore_phones: tuple[str, ...],
    region: str | None,
    dry_run: bool,
    report: str | None,
    interactive: bool,
) -> None:
    """Find contacts that are the same person and merge them, in place. Each file is deduplicated on its own."""
    if report and not dry_run:
        try:
            check_output(report, force=force)
        except OutputError as exc:
            raise click.UsageError(str(exc)) from exc
    kinds = [kind.strip() for kind in match.split(",") if kind.strip()]
    target = InPlace.from_options()
    all_cards: list[VCard] = []
    all_groups: list[DuplicateGroup] = []
    number = written = 0

    def deduplicate(source: Iterable[VCard]) -> list[VCard] | None:
        nonlocal number, written
        cards = list(source)
        all_cards.extend(cards)
        try:
            groups = find_duplicate_groups(
                cards, kinds, name_threshold=name_threshold, ignore_phones=ignore_phones, region=region
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        if dry_run:
            for group in groups:
                number += 1
                click.echo(_describe(group, number) + "\n")
            all_groups.extend(groups)
            return None
        if interactive:
            chosen = []
            for group in groups:
                number += 1
                click.echo(_describe(group, number), err=True)
                if click.confirm("Merge these?", default=True, err=True):
                    chosen.append(group)
            groups = chosen
        all_groups.extend(groups)
        result = apply_merges(cards, [g.cards for g in groups])
        written += len(result)
        return result

    target.run(deduplicate)

    merged = sum(len(g.cards) for g in all_groups)
    if dry_run:
        say(f"{len(all_groups)} groups, {merged} cards would be merged")
        return
    if report:
        _write_report(report, all_groups)
    show_warnings(all_cards)
    say(f"Merged {merged} cards into {len(all_groups)}; {written} cards left{target.where}")


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
@username_option
@common_options
def info(
    inputs: tuple[str, ...], recursive: bool, encoding: str | None, username: str | None
) -> None:
    """Show how many cards there are, which versions and properties they use, and parse warnings."""
    click.echo(format_summary(summarize(read_cards(inputs, recursive, encoding, username))))


@main.command(name="validate")
@input_options
@username_option
@click.option("--errors-only", is_flag=True, help="Do not list warnings.")
@common_options
def validate_command(
    inputs: tuple[str, ...],
    recursive: bool,
    encoding: str | None,
    username: str | None,
    errors_only: bool,
) -> None:
    """Report problems against the vCard standard. Exits with status 1 if there are errors."""
    errors = warnings = 0
    for card in read_cards(inputs, recursive, encoding, username):
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
