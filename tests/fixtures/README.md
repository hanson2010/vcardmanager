# Test fixtures

Both files are real exports with all personal data replaced by dummy values.
Names, phone numbers, emails, UIDs, photo IDs, notes, organisations and category names
were mapped one-to-one, so that every structural quirk is kept:

- A value that repeats in the original still repeats (also across the two files).
- Number formats (spaces, `+86`, trunk `0`, short service numbers) are unchanged.
  Only the subscriber digits were randomised.
- Encoding, line endings, folding and property order are unchanged.

Email domains use the reserved `.example` TLD. Generic words (departments, cities, airport codes) were kept.
Some are slightly different, for example `顺丰` → `速递`.

## `google_v30.vcf`: Google Contacts via DAVx5, vCard 3.0, UTF-8, CRLF

55 contacts. Quirks:

- `CATEGORIES` holds Google labels, including the system label `myContacts`.
  `work-ops`, `work-rd` and `work-intl` also exist as groups in the legacy file.
- Duplicate email inside one card: `EMAIL;TYPE=INTERNET,WORK:x` and `item1.EMAIL;TYPE=INTERNET:x`, plus `item1.X-ABLABEL`.
- An empty `item1.X-ABLABEL:` value.
- `NOTE:Photo: https\\://…` is folded at 75 octets and has a double-escaped colon (a Google bug).
  One card also has the same URL as `PHOTO`.
- Lowercase parameter values from DAVx5 (`TYPE=cell`, `TYPE=work`).
- Names stuffed with org/department suffixes: `沈云/APA`, `刘南/Ops 安质` (with `N:刘南/Ops;安质;;;`),
  `单位-快递-速递`, and Latin + Han mixes such as `傅清洋Lena` and `Holly 王月思/ASN`.
- `N` with the middle name in the additional-names slot: `FN:张伟诚`, `N:张;诚;伟;;`.
- Bopomofo `X-PHONETIC-*` names that include the suffix (`X-PHONETIC-FIRST-NAME:/ ㄕㄣ ㄓㄣ`).
- Phone formats: `137 1831 2288`, `+86 13947454933`, `01085310330`, `+8610 89777615`,
  `+86 1055389007`, `022 2631 5694`, `95338`, `+852 2133 7994`, `+853 858 89381`.
- A landline stored as `TYPE=CELL` (`+86 10 55791816`).
- A `NOTE` with an escaped newline (`公司A\n某云计算…`).

## `legacy_v21_gbk.vcf`: legacy client export, vCard 2.1, GBK, no `CHARSET`

72 contacts and 7 group cards. The file was trimmed from 170 contacts by dropping cards with the same format.
UIDs 208–211 were added as cross-file duplicates (see the table below). Quirks:

- The whole file is wrapped in one outer `BEGIN:VCARD` / `VERSION:2.1` … `END:VCARD`.
  The inner cards have no `VERSION` line.
- Han text is GBK-encoded, but no `CHARSET` parameter says so.
- Lowercase property name `rev:`, a non-standard `Grouping:` property, and an odd parameter `EMAIL;#1:`.
- Groups are cards with `X-TYPE:GROUP`, `N:<group name>` and `X-MEMBERS:<uid>;<uid>;…`.
  Contacts also point to their group with `Grouping:<group uid>`. **The two sources disagree:**
  9 contacts are listed only through `Grouping` (125, 128, 143, 153, 166, 198, 200, 202, 206),
  and `work-vendor-c` has no `X-MEMBERS` at all.
- 2.1 bare parameters on a phone number: `TEL;CELL;PREF:15376400148` (UID 211).
- Exact duplicates: UID 80/168 (`卢玉`) and 30/180 (`林博天`).
- Near duplicates that must not be merged by default: `Clyde Mui` (62) and `Clyde Mui/HK` (87),
  `Nina Bao/HK` (74) and `Nina Bao` (101).
- `FN` that is just the email handle (`FN:caiyujie`), and `N:;handle`.
- `FN` that is an email address (UID 194), and a card with no `FN` at all (UID 195).

## Shared between the two files

| Person | `google_v30.vcf` | `legacy_v21_gbk.vcf` | Should match by default? |
|---|---|---|---|
| 林博天 | phone only | UID 30, 180 (same email) | yes (name, email) |
| 彭杰 | `pengjie@airtech.example` | UID 52, `pengjie@cn.airtech.example` | yes (name) |
| 蔡思 | `TEL:+86 15376400148` | UID 211, `TEL;CELL;PREF:15376400148` | yes (name, phone) |
| 萧清强 | `xiaoqingqiang@airtech.example` | UID 208, `FN:xiaoqingqiang`, same email | yes (email) |
| Joy 杨伟云 | `Joy.Yang@vendor-d.example` | UID 209, `FN:Joy Yang`, `joy.yang@…` | yes (email, ignoring case) |
| 宋平国 | second email `songpingguo2@mail.example` | UID 210, `FN:宋平国/CS`, same email | yes (email) |
| 陈磊 / Lei Chen | `FN:陈磊` | UID 205, `FN:Lei Chen` | no (needs pinyin matching) |
