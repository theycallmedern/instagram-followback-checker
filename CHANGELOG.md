# Changelog

## 0.3.1 - 2026-03-14

- refined the desktop Instagram connect flow after the 0.3.0 product polish release
- improved repeated login, reconnect, and account switching behavior after `Disconnect`
- fixed cases where the desktop app could remain stuck while finishing account detection
- improved synchronization between the saved local session and the visible desktop UI state
- improved post-login identity resolution for the connected account username and avatar
- improved cleanup of lingering login browser processes after the session is already connected
- kept live scans fully local while making the desktop session flow much more dependable

## 0.3.0 - 2026-03-13

- introduced the Tauri desktop app as the primary polished local experience
- added bundled desktop runtime preparation and local app installation scripts
- added direct Rust-to-Python desktop bridge commands for session status, connect, disconnect, and live scan
- added background scan behavior after the Instagram session is connected
- added inspector, diagnostics, search, metrics, and richer desktop session presentation
- added cached session avatar support for the current connected account card
- refreshed the desktop visual system, app icons, and product screenshots
- rewrote the repository documentation in a more product-oriented, desktop-first style
- kept the browser UI and export-based CLI available as local fallbacks
