# Instagram Followback Checker

A small local CLI tool that reads an official Instagram `JSON` export and helps you inspect followback relationships:

- accounts you follow that do not follow you back
- accounts that follow you, but you do not follow back
- mutual follows

It works entirely offline after you download your export.

## Highlights

- Uses the official Instagram export instead of an unofficial API
- Supports extracted folders and `.zip` archives
- Includes multiple analysis modes: non-followers, fans, and mutuals
- Can export results to `CSV`, `TXT`, and `JSON`
- Provides summary-only mode, sorting, and output limits
- Ships with tests, packaging metadata, and CI

## Requirements

- Python `3.9+`

## Request your Instagram export

Request your account data from Instagram in `JSON` format.

Typical path:

1. `Settings`
2. `Accounts Center`
3. `Your information and permissions`
4. `Download your information`

Choose `JSON`, then download the archive when it is ready.

The tool can read either:

- the downloaded `.zip` file directly
- or the extracted export folder

## Quick Start

Run the main module directly:

```bash
python3 instagram_followback_checker.py /path/to/instagram-export.zip
```

The legacy filename still works too:

```bash
python3 instagram_nonfollowers.py /path/to/instagram-export.zip
```

If you install the project locally, you also get a console command:

```bash
pip install .
ig-followback /path/to/instagram-export.zip
```

## Analysis Modes

### Default: accounts that do not follow you back

```bash
python3 instagram_followback_checker.py /path/to/export.zip
```

### Fans: accounts that follow you, but you do not follow back

```bash
python3 instagram_followback_checker.py /path/to/export.zip --fans
```

### Mutual follows

```bash
python3 instagram_followback_checker.py /path/to/export.zip --mutuals
```

## Output Controls

### Show summary counts only

```bash
python3 instagram_followback_checker.py /path/to/export.zip --stats-only
```

### Sort and limit results

```bash
python3 instagram_followback_checker.py /path/to/export.zip --sort length --limit 50
```

### Show which export files were used

```bash
python3 instagram_followback_checker.py /path/to/export.zip --verbose
```

## Save Reports

### Save all selected results

```bash
python3 instagram_followback_checker.py /path/to/export.zip \
  --csv output.csv \
  --txt output.txt \
  --json output.json
```

### CSV format

The CSV report includes:

- `username`
- `profile_url`

## Example

```bash
python3 instagram_followback_checker.py ~/Downloads/instagram-export.zip \
  --fans \
  --sort alpha \
  --limit 100 \
  --csv fans.csv
```

## Testing

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Notes

- The export must be in `JSON` format, not `HTML`
- Usernames are normalized to lowercase because Instagram usernames are case-insensitive
- The parser is intentionally narrow and focuses on follower/following sections to avoid false positives
