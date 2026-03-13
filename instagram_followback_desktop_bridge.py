#!/usr/bin/env python3
"""Machine-readable bridge between Tauri commands and the Python followback engine."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from instagram_followback_checker import (
    MODE_LABELS,
    AnalysisResult,
    apply_limit,
    parse_limit,
    profile_url_for,
    sort_usernames,
)
from instagram_followback_live import (
    DEFAULT_LOGIN_WAIT_MS,
    DEFAULT_SESSION_DIR,
    LiveModeError,
    analyze_live_session,
    clear_live_session,
    load_login_state,
    load_session_avatar_data_url,
    load_session_username,
    login_only,
    normalize_profile_username,
    resolve_requested_username,
    resolve_saved_session_identity,
    save_session_username,
    session_has_authenticated_instagram_cookies,
    session_has_browser_state,
)


def emit_json(payload: dict[str, Any]) -> None:
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


@contextlib.contextmanager
def suppress_internal_stdout():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def session_status_payload(session_dir: Path) -> dict[str, Any]:
    username = load_session_username(session_dir)
    avatar_data_url = load_session_avatar_data_url(session_dir)
    browser_state_present = session_has_browser_state(session_dir)
    authenticated_cookie_present = session_has_authenticated_instagram_cookies(session_dir)
    login_state = load_login_state(session_dir)
    return {
        "connected": bool(browser_state_present and authenticated_cookie_present),
        "username": username,
        "avatar_data_url": avatar_data_url,
        "browser_state_present": browser_state_present,
        "authenticated_cookie_present": authenticated_cookie_present,
        "login_in_progress": bool(login_state.get("in_progress")),
        "login_phase": login_state.get("phase"),
        "session_dir": str(session_dir),
    }


def build_entries(usernames: list[str]) -> list[dict[str, str]]:
    return [
        {
            "username": username,
            "profile_url": profile_url_for(username),
        }
        for username in usernames
    ]


def build_report_payload(
    *,
    scan_username: str,
    result: AnalysisResult,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
) -> dict[str, Any]:
    selected_usernames = sort_usernames(result.usernames_for_mode(mode), sort_mode)
    displayed_usernames = apply_limit(selected_usernames, limit)
    return {
        "scan_username": scan_username,
        "created_at": utc_now_iso(),
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "sort": sort_mode,
        "limit": limit,
        "stats_only": stats_only,
        "stats": result.stats(),
        "warnings": result.warnings(),
        "time_ranges": result.relation_time_ranges(),
        "followers_usernames": sorted(result.followers),
        "following_usernames": sorted(result.following),
        "entries": build_entries(displayed_usernames),
        "all_entries": build_entries(selected_usernames),
        "shown_matches": len(displayed_usernames),
        "total_matches": len(selected_usernames),
        "used_files": {
            "followers": result.follower_files,
            "following": result.following_files,
        },
    }


def run_session_status(args: argparse.Namespace) -> int:
    emit_json({"type": "session", "payload": session_status_payload(Path(args.session_dir))})
    return 0


def run_disconnect(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    clear_live_session(session_dir)
    emit_json({"type": "session", "payload": session_status_payload(session_dir)})
    return 0


def run_login(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_profile_username(args.username) if args.username else None
    clear_live_session(session_dir)

    with suppress_internal_stdout():
        resolved_username = login_only(
            username=requested_username,
            session_dir=session_dir,
            headless=False,
            terminal_prompt=False,
            login_timeout_ms=args.login_timeout_ms,
            verbose=args.verbose,
        )

    if resolved_username:
        save_session_username(session_dir, resolved_username)

    emit_json({"type": "session", "payload": session_status_payload(session_dir)})
    return 0


def run_resolve_identity(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)

    with suppress_internal_stdout():
        resolved_username, _avatar_data_url = resolve_saved_session_identity(
            session_dir,
            verbose=args.verbose,
        )

    if resolved_username:
        save_session_username(session_dir, resolved_username)

    emit_json({"type": "session", "payload": session_status_payload(session_dir)})
    return 0


def run_scan(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = resolve_requested_username(args.username, session_dir, allow_prompt=False)

    def progress_callback(phase: str, message: str, progress: Optional[int]) -> None:
        emit_json(
            {
                "type": "progress",
                "payload": {
                    "phase": phase,
                    "message": message,
                    "progress": progress,
                },
            }
        )

    with suppress_internal_stdout():
        resolved_username, result = analyze_live_session(
            username=requested_username,
            session_dir=session_dir,
            headless=args.headless,
            max_scrolls=args.max_scrolls,
            scroll_pause_ms=args.scroll_pause_ms,
            verbose=args.verbose,
            terminal_prompt=False,
            login_timeout_ms=args.login_timeout_ms,
            progress_callback=progress_callback,
        )

    save_session_username(session_dir, resolved_username)
    emit_json(
        {
            "type": "result",
            "payload": build_report_payload(
                scan_username=resolved_username,
                result=result,
                mode=args.mode,
                sort_mode=args.sort,
                limit=args.limit,
                stats_only=args.stats_only,
            ),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="JSON bridge for the Tauri desktop app."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_session_dir_argument(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--session-dir",
            default=str(DEFAULT_SESSION_DIR),
            help="Directory where the persistent Instagram browser session is stored.",
        )

    status_parser = subparsers.add_parser("session-status", help="Return saved session metadata.")
    add_session_dir_argument(status_parser)

    disconnect_parser = subparsers.add_parser("disconnect", help="Delete the saved live session.")
    add_session_dir_argument(disconnect_parser)

    login_parser = subparsers.add_parser("login", help="Open Instagram and save a live session.")
    add_session_dir_argument(login_parser)
    login_parser.add_argument("--username", help="Optional Instagram username.")
    login_parser.add_argument(
        "--login-timeout-ms",
        default=DEFAULT_LOGIN_WAIT_MS,
        type=int,
        help="How long to wait for manual Instagram login before failing.",
    )
    login_parser.add_argument("--verbose", action="store_true", help="Enable verbose Playwright output.")

    resolve_identity_parser = subparsers.add_parser(
        "resolve-identity",
        help="Try to resolve the connected Instagram account from the saved session.",
    )
    add_session_dir_argument(resolve_identity_parser)
    resolve_identity_parser.add_argument("--verbose", action="store_true", help="Enable verbose Playwright output.")

    scan_parser = subparsers.add_parser("scan", help="Run a live scan and return report JSON.")
    add_session_dir_argument(scan_parser)
    scan_parser.add_argument("--username", help="Optional Instagram username.")
    scan_parser.add_argument(
        "--mode",
        choices=sorted(MODE_LABELS.keys()),
        default="nonfollowers",
        help="Which relationship list to return.",
    )
    scan_parser.add_argument(
        "--sort",
        choices=["alpha", "length"],
        default="alpha",
        help="How to sort usernames in the report.",
    )
    scan_parser.add_argument(
        "--limit",
        type=parse_limit,
        help="Optional limit for the displayed result list.",
    )
    scan_parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Keep the report in summary mode.",
    )
    scan_parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the scan without showing the browser window.",
    )
    scan_parser.add_argument(
        "--max-scrolls",
        default=250,
        type=int,
        help="Maximum scroll rounds per Instagram dialog.",
    )
    scan_parser.add_argument(
        "--scroll-pause-ms",
        default=1100,
        type=int,
        help="Pause between scroll attempts in milliseconds.",
    )
    scan_parser.add_argument(
        "--login-timeout-ms",
        default=DEFAULT_LOGIN_WAIT_MS,
        type=int,
        help="How long to wait for manual Instagram login before failing.",
    )
    scan_parser.add_argument("--verbose", action="store_true", help="Enable verbose Playwright output.")

    return parser


COMMANDS = {
    "session-status": run_session_status,
    "disconnect": run_disconnect,
    "login": run_login,
    "resolve-identity": run_resolve_identity,
    "scan": run_scan,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return COMMANDS[args.command](args)
    except LiveModeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected failures should surface cleanly
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
