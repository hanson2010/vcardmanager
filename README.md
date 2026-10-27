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
python -m vcardmanager merge phone.vcf google.vcf -o all.vcf
python -m vcardmanager clean all.vcf --region CN
python -m vcardmanager dedupe all_cleaned.vcf --dry-run      # look before merging
python -m vcardmanager dedupe all_cleaned.vcf
```

**Files are never overwritten.** Without `-o`, the result goes to a new file next to the input
(`all.vcf` → `all_cleaned.vcf`, then `all_cleaned_2.vcf`, …). An existing `-o FILE` is only replaced with
`--force`. `-o -` writes to stdout.

Common options: `-r` (read subdirectories), `--encoding NAME`, `--rules FILE` (see [Rules file](#rules-file)),
`-v` (list warnings), `-q` (quiet), and for commands that write: `--vcard-version 3.0|4.0`.

## Commands

### merge

```bash
python -m vcardmanager merge a.vcf b.vcf exports/ -o all.vcf
```

Puts all contacts into one file. It doesn't compare them; use `dedupe` for that.

### split

```bash
python -m vcardmanager split all.vcf -d out/ --per-file 1     # one file per contact, named after it
python -m vcardmanager split all.vcf -d out/ --per-file 500   # 500 contacts per file
python -m vcardmanager split all.vcf -d out/ --max-size 1MB
python -m vcardmanager split all.vcf -d out/ --by group       # one file per contact group
```

`--by group` understands Google/Android labels (`CATEGORIES`), iCloud groups, vCard 4.0 groups and old phone
group cards. A contact in several groups goes into each group's file; the rest go to `ungrouped.vcf`.

### clean

```bash
python -m vcardmanager clean all.vcf --region CN                    # CN: country for numbers without +86
python -m vcardmanager clean all.vcf --enable photo-embed           # also download and embed photos
python -m vcardmanager clean --list-rules
```

| Rule | Default | What it does |
|---|---|---|
| `fix-encoding` | on | Remove invisible and control characters |
| `upgrade-version` | on | Remove old 2.1 leftovers |
| `trim-whitespace` | on | Remove extra spaces |
| `drop-empty` | on | Remove empty fields |
| `drop-extensions` | on | Remove all `X-` fields; group membership is kept in `CATEGORIES`. Turn off if you import into iCloud/macOS |
| `email-lowercase` | on | Lowercase emails, unwrap `"Name" <addr>` |
| `phone-format` | on | `138 0013 8000` → `+8613800138000` (or `--phone-format national` / `international`). Needs `--region` for numbers without a country code |
| `phone-type` | on | Fix mobile vs landline: a landline marked `CELL` becomes `VOICE` |
| `type-normalize` | on | `cell`, `mobile`, `iPhone` → `CELL` |
| `dedupe-values` | on | Remove the same phone or email repeated in one contact |
| `name-fill` | on | Add a missing name from the other name field, the company, the email or the phone |
| `name-label` | on | `沈云/APA`: the `/APA` label goes to the name suffix |
| `name-split` | on | Put surname and given name in the right fields (see below) |
| `name-case` | on | English surnames in capitals: `John Smith`, `JOHN SMITH` → `John SMITH`. Leaves Chinese surnames, single words (`IBM`) and handles like `lin_shan` alone |
| `photo-embed` | off | Download photo links and embed the photos (slow; prints each download) |
| `photo-compression` | on | Shrink embedded photos larger than `--photo-max-px` (512) to JPEG (`--photo-quality`, 85), if that makes them smaller |
| `sort-properties` | on | Put fields in a readable order |

How names are split into `N` (family;given;…;suffix):

| Name | `N` |
|---|---|
| `张三/suffix` | `张;三;;;/suffix` |
| `John Smith/suffix` | `Smith;John;;;/suffix` |
| `Bob 张三/suffix` (one English word) | `Bob 张;三;;;/suffix` |
| `史强 John Smith/suffix` (English full name) | `Smith;史强 John;;;/suffix` |

`name-case` then writes English surnames in capitals (`SMITH;John`, FN `John SMITH`).
Compound surnames (`欧阳`, `司马`, …) stay together. Company entries such as `单位-快递-顺丰` are left alone.

### strip

```bash
python -m vcardmanager strip all.vcf --field PHOTO --field "X-*"
python -m vcardmanager strip all.vcf --field "TEL[TYPE=FAX]"   # only fax numbers
python -m vcardmanager strip all.vcf --keep FN,N,TEL,EMAIL     # keep only these
python -m vcardmanager strip all.vcf --preset privacy          # drop PHOTO, NOTE, BDAY, GEO, ADR, X-*
```

The name (`FN`) is always kept. Group membership survives: if a pattern would remove the group fields,
membership is moved into `CATEGORIES` first.

### dedupe

```bash
python -m vcardmanager dedupe all.vcf --dry-run          # show what would be merged
python -m vcardmanager dedupe all.vcf --interactive      # confirm each group
python -m vcardmanager dedupe all.vcf --report r.csv     # also write a CSV of what was merged
python -m vcardmanager dedupe all.vcf --ignore-phone "+86 10 5765 0000"   # a shared office number
```

Contacts are the same person if they share a **phone number** (`+86 138…` = `138…`), an **email** or the
exact **name**. `--match phone,email` picks which; `--name-threshold 90` also matches similar names.

The merged contact keeps every phone, email, address and note once, and the best name: the fullest one
(`Joy 杨伟云` over `Joy Yang`, `宋平国/CS` over `宋平国`), else the one on most of the cards.

### info, validate

```bash
python -m vcardmanager info all.vcf       # counts, versions, which fields are used
python -m vcardmanager validate all.vcf   # problems against the vCard standard; exit status 1 on errors
```

## Rules file

Save your usual options in a TOML file and pass it with `--rules rules.toml`. Options on the command line win.

```toml
[clean]
region = "CN"
enable = ["photo-embed"]
photo-max-px = 512

[dedupe]
ignore-phones = ["+862112345678"]

[output]
version = "3.0"
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
mypy --strict vcardmanager
```

## License

[MIT](LICENSE)
