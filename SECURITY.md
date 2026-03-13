# Security Policy

## Supported Versions

Only the latest state of the `main` branch is treated as supported.

At the moment that means:

- `0.2.x` — supported
- older versions — unsupported

## Sensitive Data In This Project

This repository is local-first and may interact with a real Instagram session.

Treat the following as sensitive:

- desktop session data under `~/Library/Application Support/com.mishabelyakov.instagramfollowback/live-session`
- web UI session data under `~/.instagram-followback-checker/live-session`
- browser state, cookies, or Playwright profile files
- exported reports with real usernames or profile URLs
- local history files
- screenshots that show real Instagram data

The desktop session metadata may also include:

- the connected Instagram username
- a cached local avatar preview used in the session card

Do not attach any of the above to a public issue.

## Documentation Screenshots

The screenshots committed under [`docs/screenshots`](./docs/screenshots/) are synthetic product captures generated from mocked demo data.

If you refresh them locally:

- do not use your real account data
- do not capture real usernames, profile photos, or relationship lists
- review the output before committing

## Safe Reporting

If you discover a security issue, report it privately instead of opening a public issue with sensitive details.

Include:

- a short description of the issue
- impact
- clear reproduction steps
- whether real Instagram session data is involved
- whether the issue affects only local use or could leak data more broadly

## Good Security Practices

When working on this repo:

- keep the browser UI bound to `127.0.0.1` unless you intentionally need something else
- never commit local session directories
- never commit exported Instagram archives or personal reports
- use `Disconnect` if you want the saved Instagram session removed from the machine
- prefer synthetic demo data for docs, screenshots, and issue reproduction

## Scope Notes

This project:

- does not run a hosted backend
- does not require a cloud account
- stores live session data locally on the user machine

The most realistic risks are therefore:

- accidental leakage of local session data
- accidental publication of real screenshots or exported reports
- running the browser UI on a non-local interface unintentionally

## Disclosure Expectations

Please allow reasonable time to review and fix a valid report before publishing full technical details.
