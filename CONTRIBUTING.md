# Contributing

## Development Setup

1. Clone the repository
2. Use Python `3.9+`
3. Install Python and desktop dependencies:

```bash
python3 -m pip install ".[live]"
npm install
```

4. Prepare the bundled desktop runtime when working on the Tauri app:

```bash
npm run desktop:prepare-runtime
```

## Core Checks

Run the Python test suite:

```bash
python3 -m unittest tests.test_instagram_nonfollowers tests.test_instagram_followback_live tests.test_instagram_followback_desktop_bridge -v
```

Run a quick syntax check:

```bash
python3 -m py_compile instagram_followback_web.py instagram_followback_live.py instagram_followback_checker.py instagram_followback_desktop_bridge.py scripts/prepare_desktop_runtime.py scripts/capture_desktop_screenshots.py
```

Check the Tauri backend:

```bash
cargo check --manifest-path src-tauri/Cargo.toml
```

Run the desktop app:

```bash
npm run desktop:dev
```

## Docs And Screenshots

The repository docs use synthetic desktop screenshots, not real account captures.

Refresh them with:

```bash
python3 scripts/capture_desktop_screenshots.py
```

Before committing docs:

- make sure screenshots do not contain real Instagram data
- update `README.md` if user-facing behavior changed
- update `SECURITY.md` if session handling or sensitive-data boundaries changed
- update `CHANGELOG.md` for meaningful product-facing changes

## Guidelines

- keep changes focused
- prefer local-first behavior over remote dependencies
- add tests when scanner, bridge, or parser behavior changes
- avoid committing local session data, exported reports, or personal screenshots

## Pull Requests

- summarize the user-facing change clearly
- mention any Instagram UI assumptions if the scanner depends on them
- include the commands you used to validate the change
