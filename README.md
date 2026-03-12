# Instagram Followback Checker

A small local CLI tool that reads an official Instagram `JSON` export and helps you inspect followback relationships:

- accounts you follow that do not follow you back
- accounts that follow you, but you do not follow back
- mutual follows

It works entirely offline after you download your export.

There is also a dedicated live web UI that uses a local Playwright browser session to inspect your current Instagram `followers` and `following` directly.

## Highlights

- Uses the official Instagram export instead of an unofficial API
- Supports extracted folders and `.zip` archives
- Includes multiple analysis modes: non-followers, fans, and mutuals
- Can export results to `CSV`, `TXT`, and `JSON`
- Provides summary-only mode, sorting, and output limits
- Includes an experimental live scanner for current on-screen Instagram data
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

### Run the local web interface

```bash
python3 instagram_followback_web.py
```

Then open:

```text
http://127.0.0.1:8000
```

If you install the project locally, you also get a console command:

```bash
pip install .
ig-followback-ui
```

The web UI lets you:

- connect a live Instagram browser session without using terminal prompts
- switch between non-followers, fans, and mutuals
- keep the session locally on your machine
- watch progress while the live scan is running
- search the result list in the browser
- inspect one username against the current live scan
- keep an ignore list for noisy accounts
- save local scan history and compare the latest scan with the previous one
- see stats cards and a result table
- download `CSV`, `TXT`, and `JSON` directly from the browser

The web UI is now focused on live mode only. Export-file analysis is still available from the CLI, but the browser interface is intentionally built around the current Instagram session flow.

### Live mode in the web UI

Open the local UI, then:

1. Click `Connect Instagram`
2. A separate Instagram browser window opens
3. Log in there if Instagram asks
4. Return to the local UI page after it finishes
5. Click `Run Live Scan`

The UI stores the live session locally and reuses it on the next run.

For the CLI, local installation also gives you a short command:

```bash
pip install .
ig-followback /path/to/instagram-export.zip
```

## Experimental Live Mode

Live mode is different from export mode:

- `export mode` reads the `.zip` archive you downloaded from Instagram
- `live mode` opens Instagram in a real browser window and collects the lists that are visible right now

Install the optional dependency first:

```bash
pip install ".[live]"
python3 -m playwright install chromium
```

Save a logged-in browser session:

```bash
python3 instagram_followback_live.py --login-only
```

Then run a live scan:

```bash
python3 instagram_followback_live.py
```

The script will ask for your Instagram username in Terminal. You can also leave it empty and let it try to detect the username from the logged-in session.

Or use the installed console command:

```bash
ig-followback-live
```

Useful live-mode examples:

```bash
ig-followback-live --username <your_username> --stats-only
ig-followback-live --username <your_username> --fans
ig-followback-live --username <your_username> --mutuals --sort length --limit 50
ig-followback-live --username <your_username> --inspect some_account
```

If the session already belongs to your Instagram account, you can also omit `--username` and let the script detect it automatically:

```bash
python3 instagram_followback_live.py
```

Notes for live mode:

- it uses a visible local browser by default
- the browser session is stored under `~/.instagram-followback-checker/live-session`
- the first login is manual on purpose
- Instagram can change the web UI, so live mode is more fragile than export mode

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
- If follower dates start much later than following dates, the export was likely requested with a limited time range and may produce false non-followers
