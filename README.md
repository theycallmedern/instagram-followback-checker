# Instagram Followback

Instagram Followback is a local-first macOS desktop app for checking who you follow, who follows you back, and which relationships have drifted apart.

It is built for one clean loop:

- connect a real Instagram session once
- run a live scan in the background
- review non-followers, fans, mutuals, and diagnostics in one polished workspace

No cloud backend. No unofficial hosted service. No need to export your account data every time you want an up-to-date answer.

## Why This Exists

Most Instagram followback tools feel disposable: noisy UIs, browser extensions with unclear trust boundaries, or scripts that only work once.

This project takes the opposite approach:

- desktop-first workflow with a focused local UI
- private session storage on your own machine
- current live data from a real logged-in session
- background scanning after the session is connected
- CLI and web fallbacks for people who still want them

## Screenshots

The screenshots below are synthetic product captures generated from the desktop UI. They do not contain real account data.

<table>
  <tr>
    <td align="center">
      <img src="./docs/screenshots/overview.png" alt="Desktop overview showing connected Instagram session and ready-to-scan workspace" width="460" />
      <br />
      <strong>Connect once, keep the workspace ready</strong>
    </td>
    <td align="center">
      <img src="./docs/screenshots/results.png" alt="Desktop results view showing populated metrics, relationship table, inspector, and diagnostics" width="460" />
      <br />
      <strong>Run a scan and review the relationship picture fast</strong>
    </td>
  </tr>
</table>

## What You Get

### Desktop experience

- native Tauri desktop shell
- bundled Python runtime for desktop builds
- local Instagram session reuse between runs
- background scans by default after the account is connected
- session card with the connected account handle and cached avatar

### Relationship analysis

- `Non-followers`: people you follow who do not follow you back
- `Fans`: people who follow you, but you do not follow back
- `Mutuals`: accounts that follow each other
- fast search inside the current result table
- one-account inspector against the latest scan
- optional diagnostics for warnings and date ranges

### Local privacy

- the live browser session stays on your machine
- no hosted API or remote storage is required
- `Disconnect` wipes the saved desktop session
- screenshots and reports remain under your control

## Desktop Quick Start

### Requirements

- macOS
- Python `3.9+`
- Node.js and npm
- Rust and Cargo

### Install dependencies

```bash
npm install
python3 -m pip install ".[live]"
```

### Prepare the bundled runtime

```bash
npm run desktop:prepare-runtime
```

### Start the desktop app

```bash
npm run desktop:dev
```

### Build and install the app bundle

```bash
npm run desktop:install
```

The installed app is copied to:

```text
/Applications/Instagram Followback.app
```

### Daily use

1. Click `Connect Instagram`
2. Finish login in the visible Instagram browser window if Instagram asks for it
3. Return to the desktop app
4. Click `Run scan`
5. Review `Non-followers`, `Fans`, `Mutuals`, `Inspector`, and `Diagnostics`

After the session is connected, scans run in the background by default. If Instagram invalidates the session, reconnect once and continue.

## Browser And CLI Fallbacks

The repository still ships two other interfaces:

### Browser UI

```bash
python3 instagram_followback_web.py
```

Then open:

```text
http://127.0.0.1:8000
```

### CLI

Save a live session only:

```bash
ig-followback-live --login-only
```

Run a live scan:

```bash
ig-followback-live
```

Run the export-based analyzer:

```bash
ig-followback /path/to/instagram-export.zip
```

## Command Reference

### Desktop commands

```bash
npm run desktop:dev
npm run desktop:build-app
npm run desktop:build-dmg
npm run desktop:install
```

### Python commands

```bash
ig-followback-ui
ig-followback-live
ig-followback
```

## Project Structure

| File | Responsibility |
| --- | --- |
| [`desktop-shell/index.html`](./desktop-shell/index.html) | Desktop UI rendered by Tauri |
| [`src-tauri/src/main.rs`](./src-tauri/src/main.rs) | Desktop commands and runtime orchestration |
| [`src-tauri/tauri.conf.json`](./src-tauri/tauri.conf.json) | Tauri app configuration and bundle resources |
| [`instagram_followback_desktop_bridge.py`](./instagram_followback_desktop_bridge.py) | Python bridge used by the desktop app |
| [`instagram_followback_live.py`](./instagram_followback_live.py) | Live Instagram session handling and scanning |
| [`instagram_followback_web.py`](./instagram_followback_web.py) | Browser-based local UI |
| [`instagram_followback_checker.py`](./instagram_followback_checker.py) | Export-based followback analyzer |
| [`scripts/prepare_desktop_runtime.py`](./scripts/prepare_desktop_runtime.py) | Builds the bundled Python runtime for desktop release builds |
| [`scripts/capture_desktop_screenshots.py`](./scripts/capture_desktop_screenshots.py) | Generates the synthetic desktop screenshots used in the docs |

## Development

Run tests:

```bash
python3 -m unittest tests.test_instagram_nonfollowers tests.test_instagram_followback_live tests.test_instagram_followback_desktop_bridge -v
```

Run a quick Python syntax check:

```bash
python3 -m py_compile instagram_followback_web.py instagram_followback_live.py instagram_followback_checker.py instagram_followback_desktop_bridge.py scripts/prepare_desktop_runtime.py scripts/capture_desktop_screenshots.py
```

Check the desktop backend:

```bash
cargo check --manifest-path src-tauri/Cargo.toml
```

Refresh the documentation screenshots:

```bash
python3 scripts/capture_desktop_screenshots.py
```

## Security

See [`SECURITY.md`](./SECURITY.md) for session-handling guidance, sensitive data rules, and responsible disclosure notes.

## Changelog

See [`CHANGELOG.md`](./CHANGELOG.md) for release history.

## License

MIT
