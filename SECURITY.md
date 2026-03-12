# Security Policy

## Supported Versions

Only the latest version on the `main` branch is considered supported.

At the moment, that means:

- `0.2.x` — supported
- older versions — not supported

## What This Project Treats As Sensitive

This repository is a local-first project and may interact with a real Instagram session.

Sensitive data may include:

- the saved local browser session under `~/.instagram-followback-checker/live-session`
- Playwright browser state, cookies, or profile data
- exported reports that contain usernames or profile URLs
- local history files created by the app
- any screenshots that contain your real Instagram account data

Do **not** upload or attach any of the above in a public issue.

## Safe Reporting

If you discover a security issue, report it privately instead of opening a public issue with sensitive details.

Use GitHub private reporting if it is enabled for the repository.

If private reporting is not available, contact the maintainer directly and include:

- a short description of the issue
- impact
- clear reproduction steps
- whether real Instagram session data is involved
- whether the issue affects only local use or could leak data more broadly

## Good Security Practices For This Repo

When working with this project:

- keep the app bound to `127.0.0.1` unless you intentionally know why you need another host
- never commit `~/.instagram-followback-checker/`
- never commit exported Instagram archives, local reports, or history files
- use the built-in `Disconnect` action if you want to remove the saved local session
- review screenshots before publishing them to make sure they do not contain real account data

## Scope Notes

This project:

- does **not** provide a hosted service
- does **not** require a cloud backend for the live workflow
- stores session data locally on the user machine

Because of that, most realistic security risks here are:

- accidental leakage of local session data
- accidental publication of screenshots or exports with real usernames
- running the local web UI on a non-local interface unintentionally

## Disclosure Expectations

Please give the maintainer reasonable time to review and fix a valid report before publishing full technical details.
