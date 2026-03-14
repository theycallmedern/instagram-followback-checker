#!/usr/bin/env python3
"""Machine-readable bridge between Tauri commands and the Python followback engine."""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from instagram_followback_checker import (
    MODE_LABELS,
    AnalysisResult,
    apply_limit,
    normalize_username,
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

HISTORY_DIRNAME = "history"
MAX_HISTORY_ITEMS = 6


@dataclass
class HistoryEntry:
    snapshot_id: str
    username: str
    created_at: str
    followers: set[str]
    following: set[str]
    stats: dict[str, int]
    warnings: list[str]


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
        "snapshot_id": uuid.uuid4().hex,
        "scan_username": scan_username,
        "created_at": utc_now_iso(),
        "report_source": "live",
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


def build_report_payload_from_entry(
    *,
    entry: HistoryEntry,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
) -> dict[str, Any]:
    selected_usernames = sort_usernames(
        {
            "nonfollowers": list(entry.following - entry.followers),
            "fans": list(entry.followers - entry.following),
            "mutuals": list(entry.followers & entry.following),
        }[mode],
        sort_mode,
    )
    displayed_usernames = apply_limit(selected_usernames, limit)
    return {
        "snapshot_id": entry.snapshot_id,
        "scan_username": entry.username,
        "created_at": entry.created_at,
        "report_source": "history",
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "sort": sort_mode,
        "limit": limit,
        "stats_only": stats_only,
        "stats": entry.stats,
        "warnings": entry.warnings,
        "time_ranges": {},
        "followers_usernames": sorted(entry.followers),
        "following_usernames": sorted(entry.following),
        "entries": build_entries(displayed_usernames),
        "all_entries": build_entries(selected_usernames),
        "shown_matches": len(displayed_usernames),
        "total_matches": len(selected_usernames),
        "used_files": {
            "followers": [],
            "following": [],
        },
    }


def resolve_history_dir(session_dir: Path) -> Path:
    history_dir = session_dir.parent / HISTORY_DIRNAME
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def stats_payload_from_entry(entry: HistoryEntry) -> dict[str, Any]:
    return {
        "snapshot_id": entry.snapshot_id,
        "username": entry.username,
        "created_at": entry.created_at,
        "stats": entry.stats,
        "warning_count": len(entry.warnings),
        "has_warnings": bool(entry.warnings),
    }


def detail_payload_from_entry(entry: HistoryEntry) -> dict[str, Any]:
    return {
        **stats_payload_from_entry(entry),
        "warnings": entry.warnings,
        "mode_lists": {
            "nonfollowers": sorted(entry.following - entry.followers),
            "fans": sorted(entry.followers - entry.following),
            "mutuals": sorted(entry.followers & entry.following),
        },
    }


def build_history_changes(
    current_entry: Optional[HistoryEntry],
    previous_entry: Optional[HistoryEntry],
) -> dict[str, list[str]]:
    if current_entry is None or previous_entry is None:
        return {
            "new_nonfollowers": [],
            "returned_mutuals": [],
            "disappeared_fans": [],
        }

    current_nonfollowers = current_entry.following - current_entry.followers
    previous_nonfollowers = previous_entry.following - previous_entry.followers
    current_mutuals = current_entry.following & current_entry.followers
    previous_mutuals = previous_entry.following & previous_entry.followers
    current_fans = current_entry.followers - current_entry.following
    previous_fans = previous_entry.followers - previous_entry.following

    return {
        "new_nonfollowers": sorted(current_nonfollowers - previous_nonfollowers),
        "returned_mutuals": sorted(current_mutuals - previous_mutuals),
        "disappeared_fans": sorted(previous_fans - current_fans),
    }


def save_history_snapshot(
    session_dir: Path,
    *,
    snapshot_id: str,
    scan_username: str,
    created_at: str,
    result: AnalysisResult,
) -> None:
    payload = {
        "snapshot_id": snapshot_id,
        "username": scan_username,
        "created_at": created_at,
        "followers": sorted(result.followers),
        "following": sorted(result.following),
        "stats": result.stats(),
        "warnings": result.warnings(),
    }
    path = resolve_history_dir(session_dir) / f"{snapshot_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history_entries(
    session_dir: Path,
    *,
    username: Optional[str] = None,
    limit: int = MAX_HISTORY_ITEMS,
    exclude_snapshot_id: Optional[str] = None,
) -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    history_dir = resolve_history_dir(session_dir)

    normalized_username = normalize_username(username) if username else None
    for path in history_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        raw_username = normalize_username(payload.get("username"))
        if not raw_username:
            continue
        if normalized_username and raw_username != normalized_username:
            continue

        snapshot_id = str(payload.get("snapshot_id") or path.stem)
        if exclude_snapshot_id and snapshot_id == exclude_snapshot_id:
            continue

        followers = {
            normalized
            for item in payload.get("followers", [])
            if (normalized := normalize_username(item))
        }
        following = {
            normalized
            for item in payload.get("following", [])
            if (normalized := normalize_username(item))
        }
        created_at = str(payload.get("created_at") or "")
        raw_stats = payload.get("stats")
        if isinstance(raw_stats, dict):
            stats = {
                key: int(raw_stats.get(key, 0))
                for key in ("followers", "following", "nonfollowers", "fans", "mutuals")
            }
        else:
            stats = {
                "followers": len(followers),
                "following": len(following),
                "nonfollowers": len(following - followers),
                "fans": len(followers - following),
                "mutuals": len(followers & following),
            }

        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()]
        entries.append(
            HistoryEntry(
                snapshot_id=snapshot_id,
                username=raw_username,
                created_at=created_at,
                followers=followers,
                following=following,
                stats=stats,
                warnings=warnings,
            )
        )

    entries.sort(key=lambda item: item.created_at, reverse=True)
    return entries[:limit]


def clear_history_entries(session_dir: Path, *, username: str) -> int:
    history_dir = resolve_history_dir(session_dir)
    removed = 0
    normalized_username = normalize_username(username)
    if not normalized_username:
        return removed

    for path in history_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        raw_username = normalize_username(payload.get("username"))
        if raw_username != normalized_username:
            continue

        try:
            path.unlink()
        except OSError:
            continue
        removed += 1

    return removed


def export_history_entries(
    session_dir: Path,
    *,
    username: str,
    export_format: str,
    output_path: Path,
) -> dict[str, Any]:
    entries = load_history_entries(session_dir, username=username, limit=1000)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "json":
        payload = {
            "username": normalize_username(username),
            "exported_at": utc_now_iso(),
            "entries": [
                {
                    "snapshot_id": entry.snapshot_id,
                    "created_at": entry.created_at,
                    "stats": entry.stats,
                    "warnings": entry.warnings,
                    "followers": sorted(entry.followers),
                    "following": sorted(entry.following),
                    "nonfollowers": sorted(entry.following - entry.followers),
                    "fans": sorted(entry.followers - entry.following),
                    "mutuals": sorted(entry.followers & entry.following),
                }
                for entry in entries
            ],
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif export_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "snapshot_id",
                "created_at",
                "followers",
                "following",
                "nonfollowers",
                "fans",
                "mutuals",
                "warning_count",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry.snapshot_id,
                    entry.created_at,
                    entry.stats.get("followers", 0),
                    entry.stats.get("following", 0),
                    entry.stats.get("nonfollowers", 0),
                    entry.stats.get("fans", 0),
                    entry.stats.get("mutuals", 0),
                    len(entry.warnings),
                ]
            )
        output_path.write_text(buffer.getvalue(), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported export format: {export_format}")

    return {
        "username": normalize_username(username),
        "format": export_format,
        "path": str(output_path),
        "exported_entries": len(entries),
    }


def resolve_history_pair(
    session_dir: Path,
    *,
    username: str,
    snapshot_id: Optional[str],
) -> tuple[Optional[HistoryEntry], Optional[HistoryEntry]]:
    entries = load_history_entries(session_dir, username=username, limit=1000)
    if not entries:
        return None, None

    target_entry = entries[0]
    if snapshot_id:
        for index, entry in enumerate(entries):
            if entry.snapshot_id == snapshot_id:
                target_entry = entry
                previous_entry = entries[index + 1] if index + 1 < len(entries) else None
                return target_entry, previous_entry
        return None, None

    previous_entry = entries[1] if len(entries) > 1 else None
    return target_entry, previous_entry


def resolve_history_comparison_pair(
    session_dir: Path,
    *,
    username: str,
    snapshot_id: Optional[str],
    compare_snapshot_id: Optional[str],
) -> tuple[Optional[HistoryEntry], Optional[HistoryEntry], list[HistoryEntry], str]:
    entries = load_history_entries(session_dir, username=username, limit=1000)
    if not entries:
        return None, None, [], "previous"

    target_entry = entries[0]
    target_index = 0
    if snapshot_id:
        for index, entry in enumerate(entries):
            if entry.snapshot_id == snapshot_id:
                target_entry = entry
                target_index = index
                break
        else:
            return None, None, entries, "previous"

    comparison_mode = "previous"
    compare_entry = entries[target_index + 1] if target_index + 1 < len(entries) else None
    if compare_snapshot_id:
        comparison_mode = "custom"
        compare_entry = None
        for entry in entries:
            if entry.snapshot_id == compare_snapshot_id and entry.snapshot_id != target_entry.snapshot_id:
                compare_entry = entry
                break

    return target_entry, compare_entry, entries, comparison_mode


def build_history_payload(
    session_dir: Path,
    *,
    username: Optional[str] = None,
    limit: int = MAX_HISTORY_ITEMS,
) -> dict[str, Any]:
    entries = load_history_entries(session_dir, username=username, limit=limit)
    current_entry = entries[0] if entries else None
    previous_entry = entries[1] if len(entries) > 1 else None
    return {
        "username": current_entry.username if current_entry else (normalize_username(username) if username else None),
        "entries": [stats_payload_from_entry(entry) for entry in entries],
        "changes": build_history_changes(current_entry, previous_entry),
        "latest_snapshot_id": current_entry.snapshot_id if current_entry else None,
        "previous_snapshot_id": previous_entry.snapshot_id if previous_entry else None,
    }


def build_history_detail_payload(
    session_dir: Path,
    *,
    username: str,
    snapshot_id: Optional[str],
    compare_snapshot_id: Optional[str] = None,
) -> dict[str, Any]:
    current_entry, compare_entry, entries, comparison_mode = resolve_history_comparison_pair(
        session_dir,
        username=username,
        snapshot_id=snapshot_id,
        compare_snapshot_id=compare_snapshot_id,
    )
    if current_entry is None:
        return {
            "username": username,
            "snapshot": None,
            "previous_snapshot": None,
            "comparison_snapshot": None,
            "comparison_mode": comparison_mode,
            "available_comparisons": [],
            "changes": {
                "new_nonfollowers": [],
                "returned_mutuals": [],
                "disappeared_fans": [],
            },
        }

    return {
        "username": current_entry.username,
        "snapshot": detail_payload_from_entry(current_entry),
        "previous_snapshot": detail_payload_from_entry(compare_entry) if compare_entry else None,
        "comparison_snapshot": detail_payload_from_entry(compare_entry) if compare_entry else None,
        "comparison_mode": comparison_mode,
        "available_comparisons": [
            stats_payload_from_entry(entry)
            for entry in entries
            if entry.snapshot_id != current_entry.snapshot_id
        ],
        "changes": build_history_changes(current_entry, compare_entry),
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
    report_payload = build_report_payload(
        scan_username=resolved_username,
        result=result,
        mode=args.mode,
        sort_mode=args.sort,
        limit=args.limit,
        stats_only=args.stats_only,
    )
    save_history_snapshot(
        session_dir,
        snapshot_id=report_payload["snapshot_id"],
        scan_username=resolved_username,
        created_at=report_payload["created_at"],
        result=result,
    )
    report_payload["history"] = build_history_payload(session_dir, username=resolved_username)
    emit_json(
        {
            "type": "result",
            "payload": report_payload,
        }
    )
    return 0


def run_history(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_username(args.username) if args.username else load_session_username(session_dir)
    if not requested_username:
        emit_json(
            {
                "type": "history",
                "payload": {
                    "username": None,
                    "entries": [],
                    "changes": {
                        "new_nonfollowers": [],
                        "returned_mutuals": [],
                        "disappeared_fans": [],
                    },
                    "latest_snapshot_id": None,
                    "previous_snapshot_id": None,
                },
            }
        )
        return 0
    emit_json(
        {
            "type": "history",
            "payload": build_history_payload(
                session_dir,
                username=requested_username,
                limit=args.limit,
            ),
        }
    )
    return 0


def run_history_detail(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_username(args.username) if args.username else load_session_username(session_dir)
    if not requested_username:
        emit_json(
            {
                "type": "history-detail",
                "payload": {
                    "username": None,
                    "snapshot": None,
                    "previous_snapshot": None,
                    "comparison_snapshot": None,
                    "comparison_mode": "previous",
                    "available_comparisons": [],
                    "changes": {
                        "new_nonfollowers": [],
                        "returned_mutuals": [],
                        "disappeared_fans": [],
                    },
                },
            }
        )
        return 0

    emit_json(
        {
            "type": "history-detail",
            "payload": build_history_detail_payload(
                session_dir,
                username=requested_username,
                snapshot_id=args.snapshot_id,
                compare_snapshot_id=args.compare_snapshot_id,
            ),
        }
    )
    return 0


def run_clear_history(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_username(args.username) if args.username else load_session_username(session_dir)
    if not requested_username:
        emit_json(
            {
                "type": "history-clear",
                "payload": {
                    "username": None,
                    "removed": 0,
                    "history": build_history_payload(session_dir, username=None),
                },
            }
        )
        return 0

    removed = clear_history_entries(session_dir, username=requested_username)
    emit_json(
        {
            "type": "history-clear",
            "payload": {
                "username": requested_username,
                "removed": removed,
                "history": build_history_payload(session_dir, username=requested_username),
            },
        }
    )
    return 0


def run_export_history(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_username(args.username) if args.username else load_session_username(session_dir)
    if not requested_username:
        emit_json(
            {
                "type": "history-export",
                "payload": {
                    "username": None,
                    "format": args.format,
                    "path": None,
                    "exported_entries": 0,
                },
            }
        )
        return 0

    emit_json(
        {
            "type": "history-export",
            "payload": export_history_entries(
                session_dir,
                username=requested_username,
                export_format=args.format,
                output_path=Path(args.output_path),
            ),
        }
    )
    return 0


def run_latest_report(args: argparse.Namespace) -> int:
    session_dir = Path(args.session_dir)
    requested_username = normalize_username(args.username) if args.username else load_session_username(session_dir)
    if not requested_username:
        emit_json({"type": "report", "payload": None})
        return 0

    entries = load_history_entries(session_dir, username=requested_username, limit=1)
    if not entries:
        emit_json({"type": "report", "payload": None})
        return 0

    payload = build_report_payload_from_entry(
        entry=entries[0],
        mode=args.mode,
        sort_mode=args.sort,
        limit=args.limit,
        stats_only=args.stats_only,
    )
    payload["history"] = build_history_payload(session_dir, username=requested_username)
    emit_json({"type": "report", "payload": payload})
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

    history_parser = subparsers.add_parser(
        "history",
        help="Return the saved local scan history for the selected Instagram account.",
    )
    add_session_dir_argument(history_parser)
    history_parser.add_argument("--username", help="Optional Instagram username to filter the saved history.")
    history_parser.add_argument(
        "--limit",
        default=MAX_HISTORY_ITEMS,
        type=int,
        help="Maximum number of saved history items to return.",
    )

    history_detail_parser = subparsers.add_parser(
        "history-detail",
        help="Return local detail for one saved scan snapshot.",
    )
    add_session_dir_argument(history_detail_parser)
    history_detail_parser.add_argument("--username", help="Instagram username for the selected snapshot.")
    history_detail_parser.add_argument(
        "--snapshot-id",
        help="Optional snapshot identifier. Defaults to the newest snapshot for the account.",
    )
    history_detail_parser.add_argument(
        "--compare-snapshot-id",
        help="Optional snapshot identifier to compare against instead of the previous saved snapshot.",
    )

    clear_history_parser = subparsers.add_parser(
        "clear-history",
        help="Delete saved local history snapshots for one Instagram account.",
    )
    add_session_dir_argument(clear_history_parser)
    clear_history_parser.add_argument("--username", help="Instagram username whose history should be deleted.")

    export_history_parser = subparsers.add_parser(
        "export-history",
        help="Export saved local history snapshots for one Instagram account.",
    )
    add_session_dir_argument(export_history_parser)
    export_history_parser.add_argument("--username", help="Instagram username whose history should be exported.")
    export_history_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        required=True,
        help="Export format for the saved history snapshots.",
    )
    export_history_parser.add_argument(
        "--output-path",
        required=True,
        help="Destination file for the exported history.",
    )

    latest_report_parser = subparsers.add_parser(
        "latest-report",
        help="Return a report reconstructed from the latest saved history snapshot.",
    )
    add_session_dir_argument(latest_report_parser)
    latest_report_parser.add_argument("--username", help="Instagram username whose latest saved report should be loaded.")
    latest_report_parser.add_argument(
        "--mode",
        choices=sorted(MODE_LABELS.keys()),
        default="nonfollowers",
        help="Which relationship list to return.",
    )
    latest_report_parser.add_argument(
        "--sort",
        choices=["alpha", "length"],
        default="alpha",
        help="How to sort usernames in the report.",
    )
    latest_report_parser.add_argument(
        "--limit",
        type=parse_limit,
        help="Optional limit for the displayed result list.",
    )
    latest_report_parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Keep the report in summary mode.",
    )

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
    "history": run_history,
    "history-detail": run_history_detail,
    "clear-history": run_clear_history,
    "export-history": run_export_history,
    "latest-report": run_latest_report,
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
