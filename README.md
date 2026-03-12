# Instagram Followback

A local Instagram followback checker with a live web interface.

It opens Instagram in a real local browser session, reads your current `followers` and `following`, and shows:

- `Non-followers` — accounts you follow that do not follow you back
- `Fans` — accounts that follow you, but you do not follow back
- `Mutuals` — accounts that follow each other

No unofficial API is required. No cloud backend is involved. Your live session stays on your machine.

![Overview](docs/screenshots/overview.png)

![Results](docs/screenshots/results.png)

## What It Looks Like

- Clean local web app
- Live Instagram session connect / disconnect
- One-click live scan
- In-browser search, inspect, ignore list, and history
- `CSV`, `TXT`, and `JSON` downloads

## Why This Project

Most Instagram followback tools are either:

- sketchy web apps
- browser extensions with unclear data handling
- broken scrapers
- export-only scripts with weak UX

This project takes a different path:

- local-first
- browser-based live scan
- readable UI
- explicit privacy model

## Features

- Live local web UI built around the current Instagram session
- Connect once, reuse the saved session locally
- Disconnect button that clears the saved session
- Modes: `Non-followers`, `Fans`, `Mutuals`
- Sorting: `alpha`, `length`
- Optional result limit
- `Summary only` mode
- Search results by username
- Inspect one username against the latest live scan
- Ignore noisy accounts from the table
- Local scan history with simple change tracking
- Export reports as `CSV`, `TXT`, or `JSON`
- CLI support for both live mode and export mode

## Privacy

This app is designed to keep your data local.

- The live browser session is stored locally under `~/.instagram-followback-checker/live-session`
- That session directory is ignored by git
- No Instagram session files are stored in this repository
- No server uploads are required for the live workflow
- The web UI runs locally on `127.0.0.1`

If you click `Disconnect`, the saved local Instagram session is deleted.

## Requirements

- Python `3.9+`
- macOS / Linux / Windows with Python installed
- For live mode: Playwright + Chromium

## Quick Start

### 1. Install dependencies

```bash
python3 -m pip install ".[live]"
python3 -m playwright install chromium
```

### 2. Start the local web app

```bash
python3 instagram_followback_web.py
```

Open:

```text
http://127.0.0.1:8000
```

### 3. Connect Instagram

In the UI:

1. Click `Connect`
2. Log in inside the opened Instagram browser window if needed
3. Return to the local app
4. Click `Run scan`

## Main Workflow

### Connect

Creates or reuses a local Instagram browser session.

### Run scan

Collects your current `followers` and `following` directly from Instagram Web and builds a report.

### Review

Use the results panel to:

- switch between `Non-followers`, `Fans`, and `Mutuals`
- search usernames
- inspect one account
- ignore low-value entries
- download reports

### Disconnect

Deletes the saved local Instagram session from your machine.

## Install as Commands

```bash
pip install .
```

Then you can use:

```bash
ig-followback-ui
ig-followback-live
ig-followback
```

## CLI

### Live mode

Save a session only:

```bash
ig-followback-live --login-only
```

Run a live scan:

```bash
ig-followback-live
```

Useful examples:

```bash
ig-followback-live --stats-only
ig-followback-live --fans
ig-followback-live --mutuals --sort length --limit 50
ig-followback-live --inspect some_account
```

### Export mode

The repo still includes the original export analyzer.

Run it on an official Instagram `JSON` export:

```bash
ig-followback /path/to/instagram-export.zip
```

Examples:

```bash
ig-followback /path/to/export.zip --fans
ig-followback /path/to/export.zip --mutuals --sort length --limit 50
ig-followback /path/to/export.zip --csv output.csv --txt output.txt --json output.json
```

## Project Structure

```text
instagram_followback_checker.py   Export-based CLI
instagram_followback_live.py      Live scanner via Playwright
instagram_followback_web.py       Local web UI
instagram_nonfollowers.py         Legacy wrapper
tests/                            Test suite
```

## Development

Run tests:

```bash
python3 -m unittest tests.test_instagram_nonfollowers tests.test_instagram_followback_live -v
```

Quick syntax check:

```bash
python3 -m py_compile instagram_followback_web.py instagram_followback_live.py instagram_followback_checker.py
```

## Notes

- Live mode is more useful than export mode for current account state, but also more fragile because Instagram can change its web UI
- Export mode is still useful when you want an offline or archive-based workflow
- Instagram may show login or verification prompts during live mode; the app is built around a visible browser for that reason
- For best results, use the local web UI as the primary interface

## License

MIT
