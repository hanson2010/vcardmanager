# vcardmanager

Manage vCard (`.vcf`) contact files from the command line:

- **merge** several files into one, **split** one file into many
- **clean** contacts: phone numbers, emails, names, encodings
- **strip** fields you don't want (photos, `X-` extensions, notes, …)
- **dedupe**: find the same person in several contacts and merge them

Reads vCard 2.1, 3.0 and 4.0, including messy exports from old phones, Outlook, Android, iOS and Google
Contacts (also GBK/Big5 files). Writes vCard 3.0 (default) or 4.0.

## Installation

Python 3.11+. Run it from the project folder:

```bash
pip install -r requirements.txt
python -m vcardmanager --help
```

## Quick start

```bash
python -m vcardmanager merge phone.vcf google.vcf          # → phone_merged.vcf
python -m vcardmanager clean phone_merged.vcf --region CN --backup
python -m vcardmanager dedupe phone_merged.vcf --dry-run     # look before merging
python -m vcardmanager dedupe phone_merged.vcf
```

`clean`, `strip` and `dedupe` **change each input file in place**; `-b`/`--backup` first keeps a copy as
`contacts_backup.vcf` (`contacts_backup_2.vcf`, …). With `-` (stdin) they write to stdout; given a URL they
change a [CardDAV](#carddav) address book. `--dry-run` reports what would change and writes nothing;
`-i`/`--interactive` shows each changed contact and asks first.
`merge` and `split` never touch their inputs.

Common options: `-r` (read subdirectories), `--encoding NAME`, `--rules FILE` (see [Rules file](#rules-file)),
`-v` (list warnings), `-q` (quiet), and for commands that write: `--vcard-version 3.0|4.0`.

## Commands

### merge

```bash
python -m vcardmanager merge a.vcf b.vcf exports/
```

Puts all contacts into a new file named after the first input (`a_merged.vcf`, numbered if that exists). It
doesn't compare them; use `dedupe` for that.

### split

```bash
python -m vcardmanager split all.vcf -d out/ --per-file 1     # one file per contact, named after it
python -m vcardmanager split all.vcf -d out/ --per-file 500   # 500 contacts per file
python -m vcardmanager split all.vcf -d out/ --max-size 1MB
python -m vcardmanager split all.vcf -d out/ --by group       # one file per contact group
```

`--by group` understands Google/Android labels (`CATEGORIES`), iCloud groups, vCard 4.0 groups and old phone
group cards. A contact in several groups goes into each group's file; the rest go to `ungrouped.vcf`.
Existing files are only replaced with `--force`.

### clean

```bash
python -m vcardmanager clean all.vcf --region CN                    # CN: country for numbers without +86
python -m vcardmanager clean all.vcf --enable photo-embed           # also download and embed photos
python -m vcardmanager clean --list-rules
```

| Rule | Default | What it does |
|---|---|---|
| `fix-encoding` | on | Remove invisible and control characters |
| `fix-escapes` | on | Remove needless backslashes: `\:` → `:`, Google's `https\\://` → `https://` |
| `upgrade-version` | on | Remove old 2.1 leftovers |
| `trim-whitespace` | on | Remove extra spaces |
| `drop-empty` | on | Remove empty fields |
| `drop-extensions` | on | Remove all `X-` fields; group membership is kept in `CATEGORIES`. Turn off if you import into iCloud/macOS |
| `email-lowercase` | on | Lowercase emails, unwrap `"Name" <addr>` |
| `phone-format` | on | `138 0013 8000` → `+8613800138000` (or `--phone-format national` / `international`). Needs `--region` for numbers without a country code. Service numbers stay dialable: `+86 95588` → `95588` |
| `phone-type` | on | Fix mobile vs landline: a landline marked `CELL` becomes `VOICE` |
| `type-normalize` | on | `cell`, `mobile`, `iPhone` → `CELL` |
| `dedupe-values` | on | Remove the same phone or email repeated in one contact |
| `name-fill` | on | Add a missing name from the other name field, the company, the email or the phone |
| `name-label` | on | `沈云/APA`: the `/APA` label goes to the name suffix |
| `name-prefix` | on | Two or more parts, `单位-快递-顺丰-张三`: `单位-快递-顺丰-` goes to the name prefix, `张三` to the surname |
| `name-split` | on | Put surname and given name in the right fields (see below) |
| `name-case` | on | English surnames in capitals: `John Smith`, `JOHN SMITH` → `John SMITH`. Leaves Chinese surnames, single words (`IBM`) and handles like `lin_shan` alone |
| `photo-embed` | off | Download photo links and embed the photos (slow; prints each download) |
| `photo-compression` | on | Shrink embedded photos larger than `--photo-max-px` (512) to JPEG (`--photo-quality`, 85), if that makes them smaller |
| `sort-properties` | off | Put fields in a readable order |

How names are split into `N` (family;given;…;suffix). English mode applies when there are both an English
surname and an English given name: anything else (e.g. a Chinese name) goes before the English given name.
Otherwise an English word goes after the given name. A middle name is moved into the given name. FN is reordered
to match: given + surname in English mode, surname + given otherwise.

| Name | `N` | FN |
|---|---|---|
| `张三/suffix` | `张;三;;;/suffix` | `张三/suffix` |
| `John Smith/suffix` | `Smith;John;;;/suffix` | `John Smith/suffix` |
| `Bob 张三/suffix` (non-English mode) | `张;三 Bob;;;/suffix` | `张三 Bob/suffix` |
| `John Smith 史强/suffix` (English mode) | `Smith;史强 John;;;/suffix` | `史强 John Smith/suffix` |

`name-case` then writes English surnames in capitals (`SMITH;John`, FN `John SMITH`).
Compound surnames (`欧阳`, `司马`, …) stay together. Names with hyphens such as `单位-快递-顺丰` are handled by `name-prefix`; hyphenated personal names (`Mary-Jane Smith`) are not.

### strip

```bash
python -m vcardmanager strip all.vcf --field PHOTO --field "X-*"
python -m vcardmanager strip all.vcf --field "TEL[TYPE=FAX]"   # only fax numbers
python -m vcardmanager strip all.vcf --keep FN,N,TEL,EMAIL     # keep only these
python -m vcardmanager strip all.vcf --preset privacy          # drop PHOTO, NOTE, BDAY, GEO, ADR, X-*
python -m vcardmanager strip all.vcf --preset privacy --dry-run   # list the files that would change
python -m vcardmanager strip all.vcf --drop-type EMAIL=INTERNET   # remove a type, keep the emails
```

`--drop-type PROPERTY=TYPE` removes only a type value (`EMAIL=INTERNET`, `*=PREF`) and keeps the property;
`--field "EMAIL[TYPE=INTERNET]"` would remove the emails themselves. The name (`FN`) is always kept. Group membership survives: if a pattern would remove the group fields,
membership is moved into `CATEGORIES` first.

### dedupe

```bash
python -m vcardmanager dedupe all.vcf --dry-run          # show what would be merged
python -m vcardmanager dedupe all.vcf                    # asks before each group (in a terminal)
python -m vcardmanager dedupe all.vcf --no-interactive   # merge without asking
python -m vcardmanager dedupe all.vcf --report r.csv     # also write a CSV of what was merged
python -m vcardmanager dedupe all.vcf --ignore-phone "+86 10 5765 0000"   # a shared office number
```

Each file is deduplicated on its own; `merge` first to find duplicates across files.
Contacts are the same person if they share a **phone number** (`+86 138…` = `138…`), an **email**
(`user@example.com` = `user@example.com.cn`) or the exact **name**, with or without a label
(`王小明` = `王小明/Mkt`; `王小明/Mkt` and `王小明/IT` stay apart). `--match phone,email` picks which; `--name-threshold 90` also matches similar names.
`--match phone,email,name,pinyin` also matches a Chinese name with its pinyin (`陈磊` = `Lei Chen`, `CHEN Lei`);
a spelling that fits two Chinese names in the file (`陈磊`, `陈雷`) matches neither.

The merged contact keeps every phone, email, address and note once, and the best name: the fullest one
(`Joy 杨伟云` over `Joy Yang`, `宋平国/CS` over `宋平国`), else the one on most of the cards.

### info, validate

```bash
python -m vcardmanager info all.vcf       # counts, versions, which fields are used
python -m vcardmanager validate all.vcf   # problems against the vCard standard; exit status 1 on errors
```

Both also read a [CardDAV](#carddav) address book given by its URL.

## CardDAV

`clean`, `strip` and `dedupe` can change an address book on a CardDAV server (Nextcloud, Radicale, iCloud with
an app password, …) instead of files; `info` and `validate` can read one. Give the address book URL as INPUT,
with `--username`; the password is asked for:

```bash
python -m vcardmanager dedupe https://dav.example.com/addressbooks/ann/contacts/ --username ann --backup
```

The URL is the address book itself, not the server. Only changed contacts are uploaded; contacts merged into
another are deleted. A contact changed on the server in the meantime is skipped. `--backup` first saves the address
book as it is on the server to a local file, `contacts_backup.vcf` in the current directory; nothing is backed up
on the server.

## Rules file

Save your usual options in a TOML file and pass it with `--rules rules.toml`. Options on the command line win.
Every command has a `[section]` with its long options; [rules.toml.example](rules.toml.example) lists them all.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
mypy --strict vcardmanager
```

## License

[MIT](LICENSE)
