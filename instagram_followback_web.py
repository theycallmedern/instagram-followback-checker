#!/usr/bin/env python3
"""Local web UI for instagram-followback-checker."""

from __future__ import annotations

import argparse
import cgi
import csv
import html
import io
import json
import threading
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from instagram_followback_checker import (
    MODE_LABELS,
    VERSION,
    AnalysisResult,
    analyze_export,
    apply_limit,
    normalize_username,
    profile_url_for,
    sort_usernames,
)
from instagram_followback_live import (
    DEFAULT_LOGIN_WAIT_MS,
    DEFAULT_SESSION_DIR,
    LiveModeError,
    analyze_live_session,
    clear_live_session,
    load_session_username,
    login_only,
    resolve_requested_username,
    save_session_username,
    session_has_browser_state,
)

IGNORE_LIST_FILENAME = "ignored_usernames.json"
HISTORY_DIRNAME = "history"
MAX_HISTORY_ITEMS = 6
MAX_HISTORY_PREVIEW = 8
CONTROL_MODE_LABELS = {
    "nonfollowers": "Non-followers",
    "fans": "Fans",
    "mutuals": "Mutuals",
}


@dataclass
class ReportBundle:
    token: str
    source_label: str
    mode: str
    sort_mode: str
    limit: Optional[int]
    stats_only: bool
    result: AnalysisResult
    stats: dict[str, int]
    total_matches: int
    shown_matches: int
    ignored_matches: int
    entries: list[dict[str, str]]
    follower_files: list[str]
    following_files: list[str]
    time_ranges: dict[str, dict[str, str] | None]
    warnings: list[str]
    csv_bytes: bytes
    txt_bytes: bytes
    json_bytes: bytes
    created_at: str
    scan_username: Optional[str] = None


@dataclass
class HistoryEntry:
    snapshot_id: str
    username: str
    created_at: str
    followers: set[str]
    following: set[str]
    stats: dict[str, int]


@dataclass
class LiveJob:
    job_id: str
    status: str = "queued"
    phase: str = "queued"
    message: str = "Preparing the live scan."
    progress: int = 0
    report_token: Optional[str] = None
    error: Optional[str] = None
    notice: Optional[str] = None


@dataclass
class AppState:
    reports: dict[str, ReportBundle] = field(default_factory=dict)
    jobs: dict[str, LiveJob] = field(default_factory=dict)
    live_session_dir: Path = field(default_factory=lambda: DEFAULT_SESSION_DIR)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_report_token: Optional[str] = None
    ignored_usernames: set[str] = field(default_factory=set)
    ignore_list_path: Path = field(init=False)
    history_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        state_dir = self.live_session_dir.parent
        state_dir.mkdir(parents=True, exist_ok=True)
        self.ignore_list_path = state_dir / IGNORE_LIST_FILENAME
        self.history_dir = state_dir / HISTORY_DIRNAME
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.ignored_usernames = load_ignored_usernames(self.ignore_list_path)

    def cleanup(self) -> None:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_ignored_usernames(path: Path) -> set[str]:
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    if not isinstance(payload, list):
        return set()

    usernames = set()
    for item in payload:
        normalized = normalize_username(item)
        if normalized:
            usernames.add(normalized)
    return usernames


def save_ignored_usernames(path: Path, usernames: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(usernames), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_csv_bytes(entries: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["username", "profile_url"])
    for entry in entries:
        writer.writerow([entry["username"], entry["profile_url"]])
    return buffer.getvalue().encode("utf-8")


def build_txt_bytes(entries: list[dict[str, str]]) -> bytes:
    return "".join(f"{entry['username']}\n" for entry in entries).encode("utf-8")


def build_json_bytes(
    result: AnalysisResult,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    entries: list[dict[str, str]],
    ignored_usernames: set[str],
    created_at: str,
    scan_username: Optional[str],
    ignored_matches: int,
) -> bytes:
    payload = {
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "sort": sort_mode,
        "limit": limit,
        "created_at": created_at,
        "scan_username": scan_username,
        "stats": result.stats(),
        "time_ranges": result.relation_time_ranges(),
        "warnings": result.warnings(),
        "ignored_usernames": sorted(ignored_usernames),
        "ignored_matches": ignored_matches,
        "entries": entries,
        "used_files": {
            "followers": result.follower_files,
            "following": result.following_files,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def create_report_bundle(
    export_source: Path,
    source_label: str,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
) -> ReportBundle:
    result = analyze_export(export_source)
    return create_report_bundle_from_result(
        result=result,
        source_label=source_label,
        mode=mode,
        sort_mode=sort_mode,
        limit=limit,
        stats_only=stats_only,
    )


def create_report_bundle_from_result(
    *,
    result: AnalysisResult,
    source_label: str,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
    ignored_usernames: Optional[set[str]] = None,
    token: Optional[str] = None,
    created_at: Optional[str] = None,
    scan_username: Optional[str] = None,
) -> ReportBundle:
    ignored = set(ignored_usernames or set())
    selected_usernames = sort_usernames(result.usernames_for_mode(mode), sort_mode)
    visible_usernames = [username for username in selected_usernames if username not in ignored]
    displayed_usernames = apply_limit(visible_usernames, limit)
    entries = [
        {
            "username": username,
            "profile_url": profile_url_for(username),
        }
        for username in displayed_usernames
    ]
    created_value = created_at or utc_now_iso()
    ignored_matches = len(selected_usernames) - len(visible_usernames)
    csv_bytes = build_csv_bytes(entries)
    txt_bytes = build_txt_bytes(entries)
    json_bytes = build_json_bytes(
        result,
        mode,
        sort_mode,
        limit,
        entries,
        ignored,
        created_value,
        scan_username,
        ignored_matches,
    )

    return ReportBundle(
        token=token or uuid.uuid4().hex,
        source_label=source_label,
        mode=mode,
        sort_mode=sort_mode,
        limit=limit,
        stats_only=stats_only,
        result=result,
        stats=result.stats(),
        total_matches=len(visible_usernames),
        shown_matches=len(displayed_usernames),
        ignored_matches=ignored_matches,
        entries=entries,
        follower_files=result.follower_files,
        following_files=result.following_files,
        time_ranges=result.relation_time_ranges(),
        warnings=result.warnings(),
        csv_bytes=csv_bytes,
        txt_bytes=txt_bytes,
        json_bytes=json_bytes,
        created_at=created_value,
        scan_username=scan_username,
    )


def materialize_report_bundle(bundle: ReportBundle, ignored_usernames: set[str]) -> ReportBundle:
    return create_report_bundle_from_result(
        result=bundle.result,
        source_label=bundle.source_label,
        mode=bundle.mode,
        sort_mode=bundle.sort_mode,
        limit=bundle.limit,
        stats_only=bundle.stats_only,
        ignored_usernames=ignored_usernames,
        token=bundle.token,
        created_at=bundle.created_at,
        scan_username=bundle.scan_username,
    )


def live_session_summary(state: AppState) -> tuple[Optional[str], bool]:
    return load_session_username(state.live_session_dir), session_has_browser_state(state.live_session_dir)


def create_live_report_bundle(
    *,
    state: AppState,
    username: Optional[str],
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
    verbose: bool = False,
    progress_callback=None,
) -> tuple[str, ReportBundle]:
    resolved_username, result = analyze_live_session(
        username=username,
        session_dir=state.live_session_dir,
        headless=False,
        max_scrolls=250,
        scroll_pause_ms=1100,
        verbose=verbose,
        terminal_prompt=False,
        login_timeout_ms=DEFAULT_LOGIN_WAIT_MS,
        progress_callback=progress_callback,
    )
    save_session_username(state.live_session_dir, resolved_username)
    bundle = create_report_bundle_from_result(
        result=result,
        source_label=f"live Instagram session (@{resolved_username})",
        mode=mode,
        sort_mode=sort_mode,
        limit=limit,
        stats_only=stats_only,
        scan_username=resolved_username,
    )
    return resolved_username, bundle


def save_history_snapshot(state: AppState, bundle: ReportBundle) -> None:
    if not bundle.scan_username:
        return

    payload = {
        "snapshot_id": bundle.token,
        "username": bundle.scan_username,
        "created_at": bundle.created_at,
        "followers": sorted(bundle.result.followers),
        "following": sorted(bundle.result.following),
        "stats": bundle.result.stats(),
    }
    path = state.history_dir / f"{bundle.token}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history_entries(
    state: AppState,
    *,
    username: Optional[str] = None,
    limit: int = MAX_HISTORY_ITEMS,
    exclude_snapshot_id: Optional[str] = None,
) -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    if not state.history_dir.exists():
        return entries

    for path in state.history_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        raw_username = normalize_username(payload.get("username"))
        if not raw_username:
            continue
        if username and raw_username != username:
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
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            stats = {
                "followers": len(followers),
                "following": len(following),
                "nonfollowers": len(following - followers),
                "fans": len(followers - following),
                "mutuals": len(followers & following),
            }
        entries.append(
            HistoryEntry(
                snapshot_id=snapshot_id,
                username=raw_username,
                created_at=created_at,
                followers=followers,
                following=following,
                stats={key: int(value) for key, value in stats.items()},
            )
        )

    entries.sort(key=lambda item: item.created_at, reverse=True)
    return entries[:limit]


def format_history_timestamp(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


def build_history_changes(
    current_result: AnalysisResult,
    previous_entry: Optional[HistoryEntry],
) -> dict[str, list[str]]:
    if previous_entry is None:
        return {
            "new_nonfollowers": [],
            "returned_mutuals": [],
            "disappeared_fans": [],
        }

    previous_nonfollowers = previous_entry.following - previous_entry.followers
    previous_mutuals = previous_entry.following & previous_entry.followers
    previous_fans = previous_entry.followers - previous_entry.following

    return {
        "new_nonfollowers": sorted((current_result.following - current_result.followers) - previous_nonfollowers),
        "returned_mutuals": sorted((current_result.following & current_result.followers) - previous_mutuals),
        "disappeared_fans": sorted(previous_fans - (current_result.followers - current_result.following)),
    }


def live_form_values_from_bundle(
    bundle: Optional[ReportBundle],
    fallback_username: Optional[str],
) -> dict[str, str]:
    if bundle is None:
        return {
            "instagram_username": fallback_username or "",
            "mode": "nonfollowers",
            "sort": "alpha",
            "limit": "",
            "stats_only": "",
        }

    return {
        "instagram_username": bundle.scan_username or fallback_username or "",
        "mode": bundle.mode,
        "sort": bundle.sort_mode,
        "limit": "" if bundle.limit is None else str(bundle.limit),
        "stats_only": "on" if bundle.stats_only else "",
    }


def render_username_preview(usernames: list[str], empty_copy: str) -> str:
    if not usernames:
        return f'<p class="subtle">{html.escape(empty_copy)}</p>'

    items = "".join(
        f'<li><a href="{html.escape(profile_url_for(username))}" target="_blank" rel="noreferrer">{html.escape(username)}</a></li>'
        for username in usernames[:MAX_HISTORY_PREVIEW]
    )
    if len(usernames) > MAX_HISTORY_PREVIEW:
        items += f"<li>+{len(usernames) - MAX_HISTORY_PREVIEW} more</li>"
    return f'<ul class="clean compact-list">{items}</ul>'


def render_history_panel(
    history_entries: list[HistoryEntry],
    history_changes: dict[str, list[str]],
) -> str:
    if not history_entries and not any(history_changes.values()):
        return ""

    change_cards = ""
    if any(history_changes.values()):
        change_cards = f"""
        <div class="delta-grid">
          <div class="delta-card">
            <small>New Non-followers</small>
            <strong>{len(history_changes['new_nonfollowers'])}</strong>
            {render_username_preview(history_changes['new_nonfollowers'], 'No new non-followers compared with the previous saved scan.')}
          </div>
          <div class="delta-card">
            <small>Back To Mutuals</small>
            <strong>{len(history_changes['returned_mutuals'])}</strong>
            {render_username_preview(history_changes['returned_mutuals'], 'No accounts returned to mutuals in the latest comparison.')}
          </div>
          <div class="delta-card">
            <small>Removed From Fans</small>
            <strong>{len(history_changes['disappeared_fans'])}</strong>
            {render_username_preview(history_changes['disappeared_fans'], 'No accounts disappeared from fans in the latest comparison.')}
          </div>
        </div>
        """

    history_rows = "".join(
        f"""
        <div class="history-row">
          <div>
            <strong>@{html.escape(item.username)}</strong>
            <span>{html.escape(format_history_timestamp(item.created_at))}</span>
          </div>
          <div class="history-metrics">
            <span>NF {item.stats.get('nonfollowers', 0)}</span>
            <span>Fans {item.stats.get('fans', 0)}</span>
            <span>Mutuals {item.stats.get('mutuals', 0)}</span>
          </div>
        </div>
        """
        for item in history_entries
    )

    return f"""
    <section class="feature-card">
      <div class="card-heading">
        <div>
          <span class="section-title">History</span>
          <h3>Saved scan history</h3>
        </div>
      </div>
      {change_cards}
      <div class="history-list">
        {history_rows or '<p class="subtle">No previous scan history has been saved yet.</p>'}
      </div>
    </section>
    """


def render_inspect_panel(
    report_token: Optional[str],
    show_files: bool,
    inspect_username: str,
    inspect_result: Optional[dict[str, Any]],
) -> str:
    token_value = html.escape(report_token or "")
    inspected_html = ""
    if inspect_result:
        relationship = html.escape(str(inspect_result["relationship"]))
        inspected_html = f"""
        <div class="inspect-result">
          <span class="pill pill-secondary">{relationship.replace('_', ' ')}</span>
          <div class="inspect-grid">
            <span><strong>Username:</strong> @{html.escape(str(inspect_result['username']))}</span>
            <span><strong>Follows you:</strong> {"Yes" if inspect_result["in_followers"] else "No"}</span>
            <span><strong>You follow:</strong> {"Yes" if inspect_result["in_following"] else "No"}</span>
            <span><strong>Ignored:</strong> {"Yes" if inspect_result["ignored"] else "No"}</span>
          </div>
        </div>
        """

    result_hint = ""
    if inspect_result:
        result_hint = f'<p class="subtle inspect-caption">Last checked: @{html.escape(str(inspect_result["username"]))}</p>'

    return f"""
    <section class="feature-card inspect-card">
      <div class="card-heading">
        <div>
          <span class="section-title">Check Username</span>
          <h3>Inspect one account</h3>
        </div>
      </div>
      <p class="subtle">Type a username to see whether it is in followers, following, or both in the current live report.</p>
      <form class="inline-form" method="post" action="/inspect">
        <input type="hidden" name="report_token" value="{token_value}">
        <input type="hidden" name="show_files" value={"1" if show_files else "0"}>
        <input type="text" name="inspect_username" value="" placeholder="Enter a username" required>
        <button type="submit" class="compact-button">Check username</button>
      </form>
      {result_hint}
      {inspected_html}
    </section>
    """


def render_ignore_list(
    ignored_usernames: list[str],
    report_token: Optional[str],
    show_files: bool,
) -> str:
    token_html = html.escape(report_token or "")
    items_html = "".join(
        f"""
        <form method="post" action="/ignore/remove" class="tag-form">
          <input type="hidden" name="report_token" value="{token_html}">
          <input type="hidden" name="show_files" value={"1" if show_files else "0"}>
          <input type="hidden" name="username" value="{html.escape(username)}">
          <span class="tag">@{html.escape(username)}</span>
          <button type="submit" class="tag-button" aria-label="Remove @{html.escape(username)} from ignored usernames">Remove</button>
        </form>
        """
        for username in ignored_usernames
    )

    return f"""
    <section class="ignore-box">
      <div class="card-heading">
        <div>
          <span class="section-title">Ignore List</span>
          <h3>Hide low-value accounts</h3>
        </div>
      </div>
      <p class="subtle">Ignored usernames are removed from the visible match list and exports for the selected mode.</p>
      <form class="inline-form" method="post" action="/ignore/add">
        <input type="hidden" name="report_token" value="{token_html}">
        <input type="hidden" name="show_files" value={"1" if show_files else "0"}>
        <input type="text" name="username" placeholder="brand_account" required>
        <button type="submit" class="compact-button">Add ignore</button>
      </form>
      <div class="tag-list">
        {items_html or '<span class="subtle">No ignored usernames yet.</span>'}
      </div>
    </section>
    """


def render_advanced_tools(
    report_token: Optional[str],
    show_files: bool,
    inspect_username: str,
    inspect_result: Optional[dict[str, Any]],
    ignored_usernames: list[str],
    history_entries: list[HistoryEntry],
    history_changes: dict[str, list[str]],
) -> str:
    sections: list[str] = []
    history_panel = render_history_panel(history_entries, history_changes)

    if report_token:
        sections.append(render_inspect_panel(report_token, show_files, inspect_username, inspect_result))
        sections.append(render_ignore_list(ignored_usernames, report_token, show_files))

    if history_panel:
        sections.append(history_panel)

    if not sections:
        return ""

    open_attr = " open" if inspect_result or bool(ignored_usernames) else ""
    return f"""
    <details class="advanced-tools"{open_attr}>
      <summary>Advanced tools</summary>
      <div class="advanced-grid">
        {''.join(sections)}
      </div>
    </details>
    """


def render_results(
    bundle: ReportBundle,
    show_files: bool,
    report_token: Optional[str],
    inspect_username: str,
    inspect_result: Optional[dict[str, Any]],
    ignored_usernames: list[str],
    history_entries: list[HistoryEntry],
    history_changes: dict[str, list[str]],
) -> str:
    stats_html = "".join(
        f"""
        <div class="stat">
          <small>{html.escape(label)}</small>
          <strong>{count}</strong>
        </div>
        """
        for label, count in (
            ("Followers", bundle.stats["followers"]),
            ("Following", bundle.stats["following"]),
            ("Non-followers", bundle.stats["nonfollowers"]),
            ("Fans", bundle.stats["fans"]),
            ("Mutuals", bundle.stats["mutuals"]),
        )
    )

    warnings_html = "".join(
        f'<div class="notice notice-error">{html.escape(message)}</div>'
        for message in bundle.warnings
    )
    follower_range = bundle.time_ranges.get("followers")
    following_range = bundle.time_ranges.get("following")
    report_token_value = html.escape(report_token or "")
    advanced_html = render_advanced_tools(
        report_token,
        show_files,
        inspect_username,
        inspect_result,
        ignored_usernames,
        history_entries,
        history_changes,
    )

    meta_html = f"""
    <div class="meta">
      <span><strong>Mode:</strong> {html.escape(MODE_LABELS[bundle.mode])}</span>
      <span><strong>Source:</strong> {html.escape(bundle.source_label)}</span>
      <span><strong>Saved:</strong> {html.escape(format_history_timestamp(bundle.created_at))}</span>
      <span><strong>Matches:</strong> {bundle.total_matches}</span>
      {f"<span><strong>Showing:</strong> {bundle.shown_matches}</span>" if bundle.limit is not None else ""}
      {f"<span><strong>Ignored in this mode:</strong> {bundle.ignored_matches}</span>" if bundle.ignored_matches else ""}
      {f"<span><strong>Followers range:</strong> {html.escape(follower_range['start_date'])} to {html.escape(follower_range['end_date'])}</span>" if follower_range else ""}
      {f"<span><strong>Following range:</strong> {html.escape(following_range['start_date'])} to {html.escape(following_range['end_date'])}</span>" if following_range else ""}
    </div>
    """

    toolbar_html = f"""
    <div class="toolbar">
      <a href="/download/{report_token_value}/csv">Download CSV</a>
      <a href="/download/{report_token_value}/txt">Download TXT</a>
      <a href="/download/{report_token_value}/json">Download JSON</a>
    </div>
    """

    table_html = ""
    if not bundle.stats_only:
        rows = "".join(
            f"""
            <tr data-result-row data-username="{html.escape(entry['username'])}">
              <td>{index}</td>
              <td>{html.escape(entry['username'])}</td>
              <td><a href="{html.escape(entry['profile_url'])}" target="_blank" rel="noreferrer">{html.escape(entry['profile_url'])}</a></td>
              <td>
                <form method="post" action="/ignore/add">
                  <input type="hidden" name="report_token" value="{report_token_value}">
                  <input type="hidden" name="show_files" value={"1" if show_files else "0"}>
                  <input type="hidden" name="username" value="{html.escape(entry['username'])}">
                  <button type="submit" class="table-button">Ignore</button>
                </form>
              </td>
            </tr>
            """
            for index, entry in enumerate(bundle.entries, start=1)
        )
        table_html = f"""
        <div class="table-tools">
          <label class="search-box">
            <span>Search by username</span>
            <input type="text" id="result-search" data-result-search placeholder="Search by username">
          </label>
          <div class="search-status" id="search-status">Showing {bundle.shown_matches} usernames</div>
        </div>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Username</th>
              <th>Profile</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows if rows else '<tr><td colspan="4">No matching accounts found after the current ignore list was applied.</td></tr>'}
          </tbody>
        </table>
        """

    files_html = ""
    if show_files:
        follower_list = "".join(
            f"<li>{html.escape(name)}</li>" for name in bundle.follower_files
        ) or "<li>No follower files recorded.</li>"
        following_list = "".join(
            f"<li>{html.escape(name)}</li>" for name in bundle.following_files
        ) or "<li>No following files recorded.</li>"
        files_html = f"""
        <details open>
          <summary>Diagnostic source entries</summary>
          <div class="content">
            <strong>Follower entries</strong>
            <ul class="clean">{follower_list}</ul>
            <strong style="display:block; margin-top: 12px;">Following entries</strong>
            <ul class="clean">{following_list}</ul>
          </div>
        </details>
        """

    return f"""
    <div class="results-header">
      <div class="results-copy">
        <span class="pill">Analysis complete</span>
        <h2 class="results-title">{html.escape(MODE_LABELS[bundle.mode])}</h2>
        <p class="subtle">Current live snapshot from {html.escape(bundle.source_label)}.</p>
      </div>
      {toolbar_html if not bundle.stats_only else '<div class="toolbar"><a href="/download/' + report_token_value + '/json">Download JSON</a></div>'}
    </div>
    {warnings_html}
    <div class="stats">{stats_html}</div>
    {meta_html}
    {table_html}
    {advanced_html}
    {files_html}
    """


def render_empty_state(history_entries: list[HistoryEntry]) -> str:
    history_preview = ""
    if history_entries:
        cards = "".join(
            f"""
            <div class="empty-card">
              <small>{html.escape(format_history_timestamp(item.created_at))}</small>
              <strong>@{html.escape(item.username)}</strong>
              <p>Non-followers {item.stats.get('nonfollowers', 0)} • Fans {item.stats.get('fans', 0)} • Mutuals {item.stats.get('mutuals', 0)}</p>
            </div>
            """
            for item in history_entries
        )
        history_preview = f"""
        <details class="advanced-tools">
          <summary>Recent scans</summary>
          <div class="empty-history">
            <div class="empty-grid">{cards}</div>
          </div>
        </details>
        """

    return f"""
    <div class="empty">
      <span class="pill">Live workspace ready</span>
      <strong>Run one clean live scan.</strong>
      <p class="subtle">Connect Instagram, start the scan, then review the result table. Extra tools stay out of the way until you need them.</p>
      <div class="empty-grid">
        <div class="empty-card">
          <small>Connect</small>
          <strong>Open Instagram</strong>
          <p>Use the dedicated button to launch a visible browser window and finish login manually.</p>
        </div>
        <div class="empty-card">
          <small>Scan</small>
          <strong>Collect current lists</strong>
          <p>The app compares the live followers and following lists instead of reading an exported file.</p>
        </div>
        <div class="empty-card">
          <small>Review</small>
          <strong>Search when needed</strong>
          <p>Use the table search for quick filtering. Advanced tools stay collapsed until you open them.</p>
        </div>
      </div>
      {history_preview}
    </div>
    """


def render_page(
    form_values: Optional[dict[str, str]] = None,
    bundle: Optional[ReportBundle] = None,
    error: Optional[str] = None,
    notice: Optional[str] = None,
    show_files: bool = False,
    live_form_values: Optional[dict[str, str]] = None,
    live_session_username: Optional[str] = None,
    live_session_ready: bool = False,
    report_token: Optional[str] = None,
    ignored_usernames: Optional[list[str]] = None,
    inspect_username: str = "",
    inspect_result: Optional[dict[str, Any]] = None,
    history_entries: Optional[list[HistoryEntry]] = None,
    history_changes: Optional[dict[str, list[str]]] = None,
) -> str:
    _ = form_values
    ignored = ignored_usernames or []
    history_items = history_entries or []
    changes = history_changes or {
        "new_nonfollowers": [],
        "returned_mutuals": [],
        "disappeared_fans": [],
    }
    live_values = {
        "instagram_username": "",
        "mode": "nonfollowers",
        "sort": "alpha",
        "limit": "",
        "stats_only": "",
    }
    if live_form_values:
        live_values.update(live_form_values)

    live_options_html = "".join(
        f'<option value="{mode}" {"selected" if live_values["mode"] == mode else ""}>{html.escape(CONTROL_MODE_LABELS[mode])}</option>'
        for mode in ("nonfollowers", "fans", "mutuals")
    )
    live_sort_options_html = "".join(
        f'<option value="{option}" {"selected" if live_values["sort"] == option else ""}>{option.title()}</option>'
        for option in ("alpha", "length")
    )

    error_html = f'<div class="notice notice-error">{html.escape(error)}</div>' if error else ""
    notice_html = f'<div class="notice notice-success">{html.escape(notice)}</div>' if notice else ""
    if live_session_ready and live_session_username:
        live_status_class = "session-strip connected"
        live_status_state = "Connected"
        live_status_title = f"@{html.escape(live_session_username)}"
        live_status_copy = "Saved Instagram session is ready on this Mac."
        status_indicator_html = """
        <span class="session-status-icon success" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none">
            <path d="M5 10.4 8.3 13.7 15 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
        """
    elif live_session_ready:
        live_status_class = "session-strip warm"
        live_status_state = "Session saved"
        live_status_title = "Browser session found"
        live_status_copy = "Connect once more or enter your username before scanning."
        status_indicator_html = '<span class="session-status-icon warm" aria-hidden="true"></span>'
    else:
        live_status_class = "session-strip idle"
        live_status_state = "Not connected"
        live_status_title = "No Instagram session yet"
        live_status_copy = "Connect Instagram to create a saved local session."
        status_indicator_html = '<span class="session-status-icon idle" aria-hidden="true"></span>'

    if live_session_ready:
        connect_button_label = "Connected"
        connect_button_attrs = 'disabled aria-disabled="true"'
        disconnect_button_html = (
            '<button type="submit" formaction="/live-disconnect" '
            'class="button-secondary button-danger">Disconnect</button>'
        )
    else:
        connect_button_label = "Connect"
        connect_button_attrs = ""
        disconnect_button_html = ""

    live_status_html = f"""
    <div class="{live_status_class}">
      <span class="session-platform-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <rect x="3.5" y="3.5" width="17" height="17" rx="5.5" stroke="currentColor" stroke-width="1.8"/>
          <circle cx="12" cy="12" r="4.2" stroke="currentColor" stroke-width="1.8"/>
          <circle cx="17.2" cy="6.8" r="1.2" fill="currentColor"/>
        </svg>
      </span>
      <div class="session-copy">
        <strong class="session-handle">{live_status_title}</strong>
        <span class="session-state-badge">
          <span>{live_status_state}</span>
          {status_indicator_html}
        </span>
        <p>{live_status_copy}</p>
      </div>
    </div>
    """
    results_html = (
        render_results(
            bundle,
            show_files,
            report_token,
            inspect_username,
            inspect_result,
            ignored,
            history_items,
            changes,
        )
        if bundle
        else render_empty_state(history_items)
    )
    show_files_checked = "checked" if show_files else ""
    stats_only_checked = "checked" if live_values.get("stats_only") == "on" else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Instagram Live Followback</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;700&display=swap");

    :root {{
      --bg: #08111f;
      --panel: rgba(10, 18, 33, 0.82);
      --ink: #ecf3ff;
      --muted: #94a8c8;
      --line: rgba(148, 168, 200, 0.18);
      --accent: #64f0c8;
      --accent-2: #7cb8ff;
      --accent-3: #ffd166;
      --danger: #ff8b9e;
      --shadow: 0 28px 80px rgba(0, 0, 0, 0.38);
      --content-width: 980px;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 18%, rgba(100, 240, 200, 0.16), transparent 24%),
        radial-gradient(circle at 88% 12%, rgba(124, 184, 255, 0.20), transparent 28%),
        radial-gradient(circle at 80% 88%, rgba(255, 209, 102, 0.10), transparent 22%),
        linear-gradient(180deg, #07101c 0%, var(--bg) 52%, #091422 100%);
      font-family: "Manrope", "Segoe UI", sans-serif;
    }}

    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px 20px 48px;
    }}

    .hero {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      width: 100%;
      max-width: var(--content-width);
      margin: 0 auto 16px;
      animation: rise 420ms ease-out;
    }}

    .panel {{
      position: relative;
      overflow: hidden;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}

    .panel::before {{
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(135deg, rgba(255, 255, 255, 0.06), transparent 42%);
      pointer-events: none;
    }}

    .hero-copy {{
      padding: 24px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
      align-items: start;
    }}

    .hero-main {{
      display: grid;
      gap: 14px;
    }}

    .eyebrow {{
      display: inline-flex;
      width: fit-content;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.06);
      color: var(--accent);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: 12px;
      font-weight: 700;
    }}

    h1 {{
      margin: 10px 0 8px;
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
      font-size: clamp(30px, 4vw, 44px);
      line-height: 0.98;
      letter-spacing: -0.04em;
    }}

    h3 {{
      margin: 0;
      font-size: 20px;
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
      line-height: 1.1;
    }}

    .hero p,
    .section-head p,
    .subtle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}

    .hero-actions,
    .hero-stats,
    .toolbar,
    .tag-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .hero-actions,
    .hero-stats {{
      margin-top: 0;
    }}

    .hero-chip,
    .tag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.05);
      color: var(--ink);
      font-size: 13px;
      font-weight: 700;
    }}

    .hero-stat,
    .stat,
    .feature-card,
    .delta-card,
    .empty-card,
    .session-card,
    .ignore-box,
    .job-card {{
      border-radius: 22px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
    }}

    .hero-stat {{
      flex: 1 1 120px;
      min-width: 120px;
      padding: 16px;
    }}

    .hero-stat small,
    .stat small,
    .delta-card small,
    .empty-card small {{
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .hero-stat strong,
    .stat strong,
    .delta-card strong {{
      font-size: 28px;
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
    }}

    .section-title {{
      color: var(--accent-2);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 12px;
      font-weight: 800;
    }}

    .section-head h2,
    .empty strong,
    .session-card strong {{
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
    }}

    .controls {{
      width: 100%;
      max-width: var(--content-width);
      margin: 0 auto;
      padding: 20px;
      animation: rise 520ms ease-out;
    }}

    .results {{
      width: 100%;
      max-width: var(--content-width);
      margin: 16px auto 0;
      padding: 20px;
      min-height: 320px;
      animation: rise 620ms ease-out;
      background: linear-gradient(180deg, rgba(12, 22, 40, 0.94), rgba(9, 17, 32, 0.9));
    }}

    .shell > .notice {{
      width: 100%;
      max-width: var(--content-width);
      margin: 0 auto 16px;
    }}

    .control-surface {{
      display: grid;
      gap: 16px;
      padding: 18px;
      border-radius: 24px;
      border: 1px solid rgba(148, 168, 200, 0.14);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.025));
    }}

    .control-top {{
      display: grid;
      grid-template-columns: minmax(240px, 2fr) repeat(3, minmax(0, 1fr));
      gap: 12px;
      align-items: end;
    }}

    .control-bottom {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 320px);
      gap: 14px;
      align-items: start;
    }}

    .section-head {{
      display: grid;
      gap: 8px;
      margin-bottom: 0;
    }}

    .section-head h2 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.04;
    }}

    .stack,
    .checks,
    .history-list,
    .meta,
    .feature-grid {{
      display: grid;
      gap: 14px;
    }}

    .stack {{
      gap: 14px;
    }}

    .field {{
      display: grid;
      gap: 8px;
      align-content: start;
    }}

    .toggle-group,
    .action-group {{
      display: grid;
      gap: 12px;
      padding: 14px;
      border-radius: 20px;
      border: 1px solid rgba(148, 168, 200, 0.14);
      background: rgba(255, 255, 255, 0.03);
    }}

    .micro-label {{
      color: var(--accent-2);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .feature-grid {{
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      align-items: start;
      margin: 22px 0 4px;
    }}

    label {{
      display: grid;
      gap: 7px;
      font-size: 14px;
      font-weight: 700;
      color: var(--ink);
    }}

    input[type="text"],
    input[type="number"],
    select {{
      width: 100%;
      border: 1px solid rgba(148, 168, 200, 0.18);
      border-radius: 14px;
      padding: 12px 14px;
      background: rgba(255, 255, 255, 0.05);
      color: var(--ink);
      font: inherit;
    }}

    input::placeholder {{
      color: rgba(148, 168, 200, 0.58);
    }}

    .checks {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}

    .checks label {{
      grid-template-columns: 20px minmax(0, 1fr);
      align-items: start;
      gap: 14px;
      padding: 12px 14px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.025));
      color: var(--ink);
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }}

    .checks label:hover {{
      transform: translateY(-1px);
      border-color: rgba(124, 184, 255, 0.34);
      background: linear-gradient(180deg, rgba(124, 184, 255, 0.08), rgba(255, 255, 255, 0.03));
    }}

    .checks input[type="checkbox"] {{
      appearance: none;
      -webkit-appearance: none;
      width: 20px;
      height: 20px;
      margin: 0;
      border-radius: 7px;
      border: 1px solid rgba(148, 168, 200, 0.4);
      background: rgba(255, 255, 255, 0.05);
      display: grid;
      place-items: center;
      transition: background 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
    }}

    .checks input[type="checkbox"]::after {{
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 4px;
      transform: scale(0);
      transition: transform 160ms ease;
      background: #04141c;
      box-shadow: 0 0 0 1px rgba(4, 20, 28, 0.06);
    }}

    .checks input[type="checkbox"]:checked {{
      background: linear-gradient(135deg, var(--accent) 0%, #43c8ff 100%);
      border-color: transparent;
      box-shadow: 0 10px 20px rgba(100, 240, 200, 0.18);
    }}

    .checks input[type="checkbox"]:checked::after {{
      transform: scale(1);
    }}

    .checks input[type="checkbox"]:focus-visible {{
      outline: 2px solid rgba(124, 184, 255, 0.7);
      outline-offset: 2px;
    }}

    .check-copy {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .check-title {{
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      line-height: 1.2;
    }}

    .check-description {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    button,
    .compact-button,
    .table-button,
    .tag-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      border: 0;
      border-radius: 16px;
      padding: 14px 16px;
      background: linear-gradient(135deg, var(--accent) 0%, #43c8ff 100%);
      color: #04141c;
      font: inherit;
      font-weight: 800;
      letter-spacing: 0.01em;
      line-height: 1.15;
      text-align: center;
      box-shadow: 0 18px 34px rgba(100, 240, 200, 0.22);
      transition: transform 160ms ease, box-shadow 160ms ease;
      text-decoration: none;
    }}

    button:hover,
    .compact-button:hover,
    .table-button:hover,
    .tag-button:hover {{
      transform: translateY(-1px);
      box-shadow: 0 22px 40px rgba(100, 240, 200, 0.28);
    }}

    button:disabled,
    .compact-button:disabled,
    .table-button:disabled,
    .tag-button:disabled {{
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
      opacity: 0.52;
    }}

    .compact-button,
    .table-button,
    .tag-button {{
      padding: 10px 12px;
      border-radius: 12px;
      box-shadow: none;
      font-size: 13px;
    }}

    .table-button,
    .tag-button {{
      background: rgba(255, 255, 255, 0.06);
      color: var(--ink);
      border: 1px solid var(--line);
    }}

    .button-secondary {{
      background: rgba(255, 255, 255, 0.04);
      color: var(--ink);
      border: 1px solid var(--line);
      box-shadow: none;
    }}

    .button-danger {{
      background: rgba(255, 139, 158, 0.10);
      color: #ffd6df;
      border: 1px solid rgba(255, 139, 158, 0.24);
      box-shadow: none;
    }}

    .button-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 12px;
      align-items: stretch;
    }}

    .button-row > button {{
      width: 100%;
      min-width: 0;
      min-height: 60px;
      padding-inline: 12px;
    }}

    .checks label {{
      min-height: 0;
      align-content: start;
    }}

    .notice {{
      margin-bottom: 0;
      padding: 14px 16px;
      border-radius: 16px;
      font-size: 14px;
      font-weight: 700;
      border: 1px solid var(--line);
    }}

    .notice-error {{
      background: rgba(255, 139, 158, 0.10);
      color: #ffd6df;
    }}

    .notice-success {{
      background: rgba(100, 240, 200, 0.12);
      color: var(--accent);
    }}

    .notice-info {{
      background: rgba(255, 255, 255, 0.05);
      color: var(--muted);
    }}

    .session-card,
    .ignore-box,
    .job-card,
    .feature-card {{
      display: grid;
      gap: 12px;
      padding: 16px;
      margin-bottom: 0;
      align-content: start;
    }}

    .ignore-box {{
      margin-top: 4px;
      align-content: start;
    }}

    .inspect-card {{
      min-height: 0;
    }}

    .session-card strong,
    .session-strip strong {{
      font-size: 18px;
    }}

    .session-card p,
    .session-strip p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
      font-size: 14px;
    }}

    .session-badge,
    .pill {{
      display: inline-flex;
      width: fit-content;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .session-card.connected .session-badge,
    .pill {{
      background: rgba(100, 240, 200, 0.14);
      color: var(--accent);
    }}

    .session-card.warm .session-badge {{
      background: rgba(255, 209, 102, 0.14);
      color: var(--accent-3);
    }}

    .session-card.idle .session-badge,
    .pill-secondary {{
      background: rgba(124, 184, 255, 0.12);
      color: var(--accent-2);
    }}

    .session-strip {{
      display: inline-flex;
      align-items: center;
      gap: 14px;
      padding: 14px 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.02));
      width: fit-content;
      max-width: 100%;
    }}

    .session-platform-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      border-radius: 14px;
      background: linear-gradient(135deg, rgba(255, 84, 112, 0.20), rgba(124, 184, 255, 0.18));
      color: #f5f7ff;
      flex: 0 0 auto;
    }}

    .session-platform-icon svg {{
      width: 22px;
      height: 22px;
    }}

    .session-copy {{
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      flex: 0 1 auto;
    }}

    .session-state-badge {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      min-height: 34px;
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .session-state-badge {{
      gap: 8px;
      padding-right: 10px;
    }}

    .session-strip.connected .session-state-badge {{
      background: rgba(100, 240, 200, 0.14);
      color: var(--accent);
    }}

    .session-strip.warm .session-state-badge {{
      background: rgba(255, 209, 102, 0.14);
      color: var(--accent-3);
    }}

    .session-strip.idle .session-state-badge {{
      background: rgba(124, 184, 255, 0.12);
      color: var(--accent-2);
    }}

    .session-handle {{
      flex: 0 0 auto;
    }}

    .session-strip strong {{
      font-size: 18px;
      line-height: 1.1;
    }}

    .session-copy p {{
      max-width: 56ch;
    }}

    .session-status-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      flex: 0 0 auto;
    }}

    .session-status-icon.success {{
      background: rgba(100, 240, 200, 0.18);
      color: #64f0c8;
      box-shadow: 0 0 0 1px rgba(100, 240, 200, 0.18);
    }}

    .session-status-icon.success svg {{
      width: 12px;
      height: 12px;
    }}

    .session-status-icon.warm {{
      background: rgba(255, 209, 102, 0.18);
      box-shadow: inset 0 0 0 6px rgba(255, 209, 102, 0.92);
    }}

    .session-status-icon.idle {{
      background: rgba(124, 184, 255, 0.18);
      box-shadow: inset 0 0 0 6px rgba(124, 184, 255, 0.92);
    }}

    .job-card.hidden {{
      display: none;
    }}

    .progress-meta {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }}

    .progress-track {{
      height: 10px;
      width: 100%;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.08);
    }}

    .progress-bar {{
      height: 100%;
      width: 0%;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--accent) 0%, #43c8ff 100%);
      transition: width 180ms ease;
    }}

    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}

    .results-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}

    .results-copy {{
      display: grid;
      gap: 10px;
    }}

    .results-title {{
      margin: 0;
      font-size: 26px;
      line-height: 1.04;
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
    }}

    .stat {{
      padding: 18px;
    }}

    .toolbar {{
      margin: 0;
    }}

    .toolbar a {{
      text-decoration: none;
      color: var(--accent);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 10px 14px;
      background: rgba(255, 255, 255, 0.04);
      font-size: 14px;
      font-weight: 700;
    }}

    .meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
    }}

    .card-heading,
    .history-row,
    .inspect-grid {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      flex-wrap: wrap;
    }}

    .table-tools {{
      display: grid;
      grid-template-columns: minmax(240px, 360px) minmax(0, 1fr);
      gap: 16px;
      align-items: end;
      margin: 18px 0 16px;
    }}

    .history-row {{
      padding: 14px 0;
      border-bottom: 1px solid rgba(148, 168, 200, 0.08);
    }}

    .history-row:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}

    .history-row span,
    .history-metrics span,
    .search-status {{
      color: var(--muted);
      font-size: 13px;
    }}

    .search-status {{
      justify-self: end;
      align-self: center;
      text-align: right;
    }}

    .history-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .delta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}

    .delta-card {{
      padding: 16px;
    }}

    .compact-list {{
      margin-top: 10px;
    }}

    .inline-form,
    .tag-form {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: end;
    }}

    .search-box {{
      max-width: 360px;
    }}

    .search-box span {{
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}

    .tag-form {{
      margin: 0;
      align-items: center;
    }}

    .inline-form input,
    .tag-form input {{
      min-width: 0;
    }}

    .tag-list {{
      display: grid;
      gap: 10px;
    }}

    .tag {{
      width: 100%;
      min-width: 0;
      justify-content: flex-start;
      overflow-wrap: anywhere;
    }}

    .compact-button,
    .tag-button {{
      white-space: nowrap;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
    }}

    th, td {{
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid rgba(148, 168, 200, 0.08);
      font-size: 14px;
      vertical-align: top;
    }}

    th {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    tr:last-child td {{
      border-bottom: 0;
    }}

    details {{
      margin-top: 18px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
      overflow: hidden;
    }}

    summary {{
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 800;
    }}

    details .content {{
      padding: 0 16px 16px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}

    ul.clean {{
      margin: 10px 0 0;
      padding-left: 18px;
    }}

    .empty {{
      display: grid;
      gap: 14px;
      align-content: center;
      justify-items: start;
      min-height: 320px;
      color: var(--muted);
    }}

    .empty strong {{
      color: var(--ink);
      font-size: 24px;
      font-family: "Space Grotesk", "Avenir Next", sans-serif;
    }}

    .empty-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      width: 100%;
    }}

    .empty-card {{
      padding: 16px;
    }}

    .empty-card p {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
    }}

    .empty-history {{
      display: grid;
      gap: 12px;
      width: 100%;
    }}

    .inspect-result {{
      display: grid;
      gap: 12px;
    }}

    .inspect-caption {{
      margin: -2px 0 0;
    }}

    .advanced-tools {{
      margin-top: 18px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
      overflow: hidden;
    }}

    .advanced-tools summary {{
      padding: 16px 18px;
      font-size: 14px;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}

    .advanced-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
      padding: 0 18px 18px;
    }}

    a {{
      color: var(--accent-2);
    }}

    @keyframes rise {{
      from {{
        opacity: 0;
        transform: translateY(10px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    @media (max-width: 1080px) {{
      .hero-copy,
      .control-top,
      .control-bottom {{
        grid-template-columns: 1fr;
      }}
    }}

    @media (max-width: 900px) {{
      .hero-copy,
      .control-top,
      .control-bottom,
      .checks,
      .empty-grid {{
        grid-template-columns: 1fr;
      }}

      .table-tools {{
        grid-template-columns: 1fr;
      }}

      .search-status {{
        justify-self: start;
        text-align: left;
      }}
    }}

    @media (max-width: 640px) {{
      .delta-grid,
      .advanced-grid,
      .feature-grid {{
        grid-template-columns: 1fr;
      }}

      .button-row,
      .inline-form,
      .tag-form {{
        grid-template-columns: 1fr;
      }}

      .shell {{
        padding-inline: 14px;
      }}

      .hero-copy,
      .controls,
      .results {{
        padding: 18px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-copy panel">
        <div class="hero-main">
          <span class="eyebrow">Live-only local app • v{html.escape(VERSION)}</span>
          <h1>Instagram Followback</h1>
          <p>Connect your Instagram session, run one live scan, and review who does not follow you back without digging through a crowded interface.</p>
          {live_status_html}
        </div>
      </div>
    </section>

    {error_html}
    {notice_html}

    <section class="panel controls">
      <div class="section-head">
        <span class="section-title">Live scan</span>
        <h2>Simple control panel</h2>
        <p>Pick the mode, then run the scan.</p>
      </div>
      <form id="live-scan-form" class="stack" method="post" action="/live-analyze">
        <div class="control-surface">
          <div class="control-top">
            <label class="field">
              <span class="micro-label">Account</span>
              <span>Instagram username</span>
              <input type="text" name="instagram_username" value="{html.escape(live_values['instagram_username'])}" placeholder="Leave empty if the saved session is already yours">
            </label>
            <label class="field">
              <span class="micro-label">Mode</span>
              <select name="mode">{live_options_html}</select>
            </label>
            <label class="field">
              <span class="micro-label">Sort</span>
              <select name="sort">{live_sort_options_html}</select>
            </label>
            <label class="field">
              <span class="micro-label">Limit</span>
              <input type="number" min="0" step="1" name="limit" value="{html.escape(live_values['limit'])}" placeholder="All">
            </label>
          </div>
          <div class="control-bottom">
            <div class="toggle-group">
              <span class="micro-label">Display</span>
              <div class="checks">
                <label>
                  <input type="checkbox" name="stats_only" {stats_only_checked}>
                  <span class="check-copy">
                    <span class="check-title">Summary only</span>
                    <span class="check-description">Hide the full table.</span>
                  </span>
                </label>
                <label>
                  <input type="checkbox" name="show_files" {show_files_checked}>
                  <span class="check-copy">
                    <span class="check-title">Diagnostics</span>
                    <span class="check-description">Show scan inputs.</span>
                  </span>
                </label>
              </div>
            </div>
            <div class="action-group">
              <span class="micro-label">Actions</span>
              <div class="button-row">
                <button type="submit" formaction="/live-login" class="button-secondary" {connect_button_attrs}>{connect_button_label}</button>
                {disconnect_button_html}
                <button id="scan-submit" type="submit">Run scan</button>
              </div>
            </div>
          </div>
        </div>
          <div id="job-card" class="job-card hidden" aria-live="polite">
            <div class="card-heading">
              <div>
                <span class="section-title">Progress</span>
                <h3 id="job-phase">Preparing live scan</h3>
              </div>
              <span id="job-percent">0%</span>
            </div>
            <div class="progress-track"><div id="job-bar" class="progress-bar"></div></div>
            <p id="job-message" class="subtle">The live scan status will appear here.</p>
          </div>
      </form>
    </section>

    <main class="panel results">
      {results_html}
    </main>
  </div>
  <script>
    (() => {{
      const searchInput = document.querySelector("[data-result-search]");
      const searchStatus = document.getElementById("search-status");
      const rows = Array.from(document.querySelectorAll("[data-result-row]"));

      const updateSearch = () => {{
        if (!searchInput || !rows.length || !searchStatus) {{
          return;
        }}
        const query = searchInput.value.trim().toLowerCase();
        let visible = 0;
        rows.forEach((row) => {{
          const username = (row.dataset.username || "").toLowerCase();
          const matches = !query || username.includes(query);
          row.style.display = matches ? "" : "none";
          if (matches) visible += 1;
        }});
        searchStatus.textContent = query
          ? `Showing ${{visible}} username(s) matching "${{searchInput.value.trim()}}"`
          : `Showing ${{visible}} username(s)`;
      }};

      if (searchInput) {{
        searchInput.addEventListener("input", updateSearch);
        updateSearch();
      }}

      const liveForm = document.getElementById("live-scan-form");
      const jobCard = document.getElementById("job-card");
      const jobPhase = document.getElementById("job-phase");
      const jobPercent = document.getElementById("job-percent");
      const jobMessage = document.getElementById("job-message");
      const jobBar = document.getElementById("job-bar");
      const scanSubmit = document.getElementById("scan-submit");

      const setJobState = (payload, isError = false) => {{
        if (!jobCard || !jobPhase || !jobPercent || !jobMessage || !jobBar) {{
          return;
        }}
        jobCard.classList.remove("hidden");
        jobPhase.textContent = payload.phase || "Running";
        const progress = Math.max(0, Math.min(100, Number(payload.progress || 0)));
        jobPercent.textContent = `${{progress}}%`;
        jobMessage.textContent = payload.error || payload.message || "Working...";
        jobBar.style.width = `${{progress}}%`;
        jobCard.style.borderColor = isError ? "rgba(255, 139, 158, 0.4)" : "";
      }};

      const pollJob = async (jobId, showFiles) => {{
        const response = await fetch(`/jobs/${{encodeURIComponent(jobId)}}`, {{
          headers: {{
            Accept: "application/json",
          }},
        }});
        const payload = await response.json();
        setJobState(payload, payload.status === "error");

        if (payload.status === "completed" && payload.report_token) {{
          const params = new URLSearchParams();
          params.set("report", payload.report_token);
          if (showFiles) {{
            params.set("show_files", "1");
          }}
          if (payload.notice) {{
            params.set("notice", payload.notice);
          }}
          window.location.href = `/?${{params.toString()}}`;
          return;
        }}

        if (payload.status === "error") {{
          if (scanSubmit) {{
            scanSubmit.disabled = false;
            scanSubmit.textContent = "Run scan";
          }}
          return;
        }}

        window.setTimeout(() => {{
          pollJob(jobId, showFiles).catch((error) => {{
            setJobState({{
              phase: "Progress",
              progress: 0,
              error: error.message || "Could not fetch live scan status.",
            }}, true);
          }});
        }}, 900);
      }};

      if (liveForm) {{
        liveForm.addEventListener("submit", async (event) => {{
          const submitter = event.submitter;
          if (
            !submitter ||
            submitter.formAction.endsWith("/live-login") ||
            submitter.formAction.endsWith("/live-disconnect")
          ) {{
            return;
          }}

          event.preventDefault();
          const formData = new FormData(liveForm);
          if (scanSubmit) {{
            scanSubmit.disabled = true;
            scanSubmit.textContent = "Scanning...";
          }}
          setJobState({{
            phase: "Queued",
            progress: 2,
            message: "The live scan job was queued.",
          }});

          try {{
            const response = await fetch("/live-analyze", {{
              method: "POST",
              body: formData,
              headers: {{
                Accept: "application/json",
              }},
            }});
            const payload = await response.json();
            if (!response.ok) {{
              throw new Error(payload.error || "Could not start the live scan.");
            }}
            pollJob(payload.job_id, formData.get("show_files") === "on");
          }} catch (error) {{
            setJobState({{
              phase: "Error",
              progress: 0,
              error: error.message || "Could not start the live scan.",
            }}, true);
            if (scanSubmit) {{
              scanSubmit.disabled = false;
              scanSubmit.textContent = "Run scan";
            }}
          }}
        }});
      }}
    }})();
  </script>
</body>
</html>"""


def update_job(
    state: AppState,
    job_id: str,
    *,
    status: Optional[str] = None,
    phase: Optional[str] = None,
    message: Optional[str] = None,
    progress: Optional[int] = None,
    report_token: Optional[str] = None,
    error: Optional[str] = None,
    notice: Optional[str] = None,
) -> None:
    with state.lock:
        job = state.jobs.get(job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if phase is not None:
            job.phase = phase
        if message is not None:
            job.message = message
        if progress is not None:
            job.progress = max(0, min(100, progress))
        if report_token is not None:
            job.report_token = report_token
        if error is not None:
            job.error = error
        if notice is not None:
            job.notice = notice


def start_live_scan_job(
    state: AppState,
    *,
    raw_username: Optional[str],
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    stats_only: bool,
) -> str:
    job_id = uuid.uuid4().hex
    with state.lock:
        state.jobs[job_id] = LiveJob(job_id=job_id)

    def progress_callback(phase: str, message: str, progress: Optional[int]) -> None:
        update_job(
            state,
            job_id,
            status="running",
            phase=phase.replace("_", " ").title(),
            message=message,
            progress=progress if progress is not None else 0,
        )

    def worker() -> None:
        try:
            update_job(
                state,
                job_id,
                status="running",
                phase="Queued",
                message="Starting the live scan job.",
                progress=4,
            )
            requested_username = resolve_requested_username(
                raw_username,
                state.live_session_dir,
                allow_prompt=False,
            )
            resolved_username, bundle = create_live_report_bundle(
                state=state,
                username=requested_username,
                mode=mode,
                sort_mode=sort_mode,
                limit=limit,
                stats_only=stats_only,
                verbose=False,
                progress_callback=progress_callback,
            )
            save_history_snapshot(state, bundle)
            with state.lock:
                state.reports[bundle.token] = bundle
                state.last_report_token = bundle.token
            update_job(
                state,
                job_id,
                status="completed",
                phase="Completed",
                message=f"Live scan completed for @{resolved_username}.",
                progress=100,
                report_token=bundle.token,
                notice=f"Live scan completed for @{resolved_username}.",
            )
        except (LiveModeError, OSError) as exc:
            update_job(
                state,
                job_id,
                status="error",
                phase="Error",
                message=str(exc),
                progress=0,
                error=str(exc),
            )

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        @staticmethod
        def parse_limit(raw: str) -> Optional[int]:
            if not raw:
                return None
            limit = int(raw)
            if limit < 0:
                raise ValueError
            return limit

        def parse_form(self) -> cgi.FieldStorage:
            return cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                },
            )

        def parse_live_form(self, form: cgi.FieldStorage) -> tuple[dict[str, str], bool, Optional[int]]:
            live_form_values = {
                "instagram_username": (form.getfirst("instagram_username", "") or "").strip(),
                "mode": form.getfirst("mode", "nonfollowers"),
                "sort": form.getfirst("sort", "alpha"),
                "limit": (form.getfirst("limit", "") or "").strip(),
                "stats_only": "on" if form.getfirst("stats_only") == "on" else "",
            }
            show_files = form.getfirst("show_files") in {"on", "1", "true"}
            limit = self.parse_limit(live_form_values["limit"])
            return live_form_values, show_files, limit

        def send_html(self, document: str) -> None:
            data = document.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.end_headers()

        def render_document(
            self,
            *,
            report_token: Optional[str] = None,
            live_form_values: Optional[dict[str, str]] = None,
            error: Optional[str] = None,
            notice: Optional[str] = None,
            show_files: bool = False,
            inspect_username: str = "",
            inspect_result: Optional[dict[str, Any]] = None,
        ) -> None:
            live_username, live_ready = live_session_summary(state)
            with state.lock:
                token = report_token or state.last_report_token
                base_bundle = state.reports.get(token) if token else None
                ignored_snapshot = sorted(state.ignored_usernames)
            bundle = materialize_report_bundle(base_bundle, set(ignored_snapshot)) if base_bundle else None
            if live_form_values is None:
                live_form_values = live_form_values_from_bundle(base_bundle, live_username)
            history_username = bundle.scan_username if bundle else (live_username or None)
            history_entries = load_history_entries(
                state,
                username=history_username,
                limit=MAX_HISTORY_ITEMS,
                exclude_snapshot_id=bundle.token if bundle else None,
            )
            history_changes = build_history_changes(bundle.result, history_entries[0] if history_entries else None) if bundle else {
                "new_nonfollowers": [],
                "returned_mutuals": [],
                "disappeared_fans": [],
            }
            self.send_html(
                render_page(
                    bundle=bundle,
                    error=error,
                    notice=notice,
                    show_files=show_files,
                    live_form_values=live_form_values,
                    live_session_username=live_username,
                    live_session_ready=live_ready,
                    report_token=token if bundle else None,
                    ignored_usernames=ignored_snapshot,
                    inspect_username=inspect_username,
                    inspect_result=inspect_result,
                    history_entries=history_entries,
                    history_changes=history_changes,
                )
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                query = urllib.parse.parse_qs(parsed.query)
                self.render_document(
                    report_token=(query.get("report", [""])[0] or None),
                    notice=(query.get("notice", [""])[0] or None),
                    show_files=query.get("show_files", ["0"])[0] == "1",
                )
                return

            if parsed.path.startswith("/download/"):
                self.handle_download(parsed.path)
                return

            if parsed.path.startswith("/jobs/"):
                self.handle_job_status(parsed.path)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path not in {
                "/live-login",
                "/live-disconnect",
                "/live-analyze",
                "/inspect",
                "/ignore/add",
                "/ignore/remove",
            }:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            form = self.parse_form()
            if self.path == "/live-login":
                self.handle_live_login(form)
                return
            if self.path == "/live-disconnect":
                self.handle_live_disconnect(form)
                return
            if self.path == "/live-analyze":
                self.handle_live_analysis(form)
                return
            if self.path == "/inspect":
                self.handle_inspect(form)
                return
            if self.path == "/ignore/add":
                self.handle_ignore_mutation(form, add=True)
                return
            self.handle_ignore_mutation(form, add=False)

        def handle_live_login(self, form: cgi.FieldStorage) -> None:
            try:
                live_form_values, show_files, _ = self.parse_live_form(form)
            except ValueError:
                self.render_document(
                    live_form_values={
                        "instagram_username": (form.getfirst("instagram_username", "") or "").strip(),
                        "mode": form.getfirst("mode", "nonfollowers"),
                        "sort": form.getfirst("sort", "alpha"),
                        "limit": (form.getfirst("limit", "") or "").strip(),
                        "stats_only": "on" if form.getfirst("stats_only") == "on" else "",
                    },
                    error="Limit must be a whole number greater than or equal to zero.",
                    show_files=form.getfirst("show_files") == "on",
                )
                return

            try:
                requested_username = resolve_requested_username(
                    live_form_values["instagram_username"] or None,
                    state.live_session_dir,
                    allow_prompt=False,
                )
                resolved_username = login_only(
                    username=requested_username,
                    session_dir=state.live_session_dir,
                    headless=False,
                    terminal_prompt=False,
                    login_timeout_ms=DEFAULT_LOGIN_WAIT_MS,
                    verbose=False,
                )
                if resolved_username:
                    save_session_username(state.live_session_dir, resolved_username)
                    notice = f"Live Instagram session connected for @{resolved_username}."
                else:
                    notice = "Live Instagram session connected."
            except (LiveModeError, OSError) as exc:
                self.render_document(
                    live_form_values=live_form_values,
                    error=str(exc),
                    show_files=show_files,
                )
                return

            self.render_document(
                live_form_values=live_form_values,
                notice=notice,
                show_files=show_files,
            )

        def handle_live_disconnect(self, form: cgi.FieldStorage) -> None:
            try:
                live_form_values, show_files, _ = self.parse_live_form(form)
            except ValueError:
                self.render_document(
                    live_form_values={
                        "instagram_username": (form.getfirst("instagram_username", "") or "").strip(),
                        "mode": form.getfirst("mode", "nonfollowers"),
                        "sort": form.getfirst("sort", "alpha"),
                        "limit": (form.getfirst("limit", "") or "").strip(),
                        "stats_only": "on" if form.getfirst("stats_only") == "on" else "",
                    },
                    error="Limit must be a whole number greater than or equal to zero.",
                    show_files=form.getfirst("show_files") == "on",
                )
                return

            try:
                had_session = session_has_browser_state(state.live_session_dir) or (
                    load_session_username(state.live_session_dir) is not None
                )
                clear_live_session(state.live_session_dir)
            except OSError as exc:
                self.render_document(
                    live_form_values=live_form_values,
                    error=f"Could not disconnect the saved Instagram session: {exc}",
                    show_files=show_files,
                )
                return

            self.render_document(
                live_form_values=live_form_values,
                notice=(
                    "Saved Instagram session disconnected."
                    if had_session
                    else "No saved Instagram session was found."
                ),
                show_files=show_files,
            )

        def handle_live_analysis(self, form: cgi.FieldStorage) -> None:
            try:
                live_form_values, show_files, limit = self.parse_live_form(form)
            except ValueError:
                payload = {
                    "error": "Limit must be a whole number greater than or equal to zero.",
                }
                if "application/json" in self.headers.get("Accept", ""):
                    self.send_json(payload, status=HTTPStatus.BAD_REQUEST)
                else:
                    self.render_document(
                        live_form_values={
                            "instagram_username": (form.getfirst("instagram_username", "") or "").strip(),
                            "mode": form.getfirst("mode", "nonfollowers"),
                            "sort": form.getfirst("sort", "alpha"),
                            "limit": (form.getfirst("limit", "") or "").strip(),
                            "stats_only": "on" if form.getfirst("stats_only") == "on" else "",
                        },
                        error=payload["error"],
                        show_files=form.getfirst("show_files") == "on",
                    )
                return

            accepts_json = "application/json" in self.headers.get("Accept", "")
            if accepts_json:
                job_id = start_live_scan_job(
                    state,
                    raw_username=live_form_values["instagram_username"] or None,
                    mode=live_form_values["mode"],
                    sort_mode=live_form_values["sort"],
                    limit=limit,
                    stats_only=live_form_values["stats_only"] == "on",
                )
                self.send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                return

            try:
                requested_username = resolve_requested_username(
                    live_form_values["instagram_username"] or None,
                    state.live_session_dir,
                    allow_prompt=False,
                )
                resolved_username, bundle = create_live_report_bundle(
                    state=state,
                    username=requested_username,
                    mode=live_form_values["mode"],
                    sort_mode=live_form_values["sort"],
                    limit=limit,
                    stats_only=live_form_values["stats_only"] == "on",
                    verbose=False,
                )
            except (LiveModeError, OSError) as exc:
                self.render_document(
                    live_form_values=live_form_values,
                    error=str(exc),
                    show_files=show_files,
                )
                return

            save_history_snapshot(state, bundle)
            with state.lock:
                state.reports[bundle.token] = bundle
                state.last_report_token = bundle.token
            self.render_document(
                report_token=bundle.token,
                live_form_values=live_form_values,
                notice=f"Live scan completed for @{resolved_username}.",
                show_files=show_files,
            )

        def handle_inspect(self, form: cgi.FieldStorage) -> None:
            report_token = (form.getfirst("report_token", "") or "").strip() or None
            raw_username = (form.getfirst("inspect_username", "") or "").strip()
            show_files = form.getfirst("show_files") in {"1", "on", "true"}
            if not report_token:
                self.render_document(
                    error="Run a live scan first before inspecting a username.",
                    show_files=show_files,
                )
                return

            with state.lock:
                base_bundle = state.reports.get(report_token)
                ignored_snapshot = set(state.ignored_usernames)
            if base_bundle is None:
                self.render_document(
                    error="The selected report is no longer available. Run the live scan again.",
                    show_files=show_files,
                )
                return

            normalized = normalize_username(raw_username)
            if normalized is None:
                self.render_document(
                    report_token=report_token,
                    error=f"Invalid Instagram username: {raw_username}",
                    show_files=show_files,
                )
                return

            inspected = base_bundle.result.inspect_username(normalized)
            inspected["ignored"] = normalized in ignored_snapshot
            self.render_document(
                report_token=report_token,
                inspect_username=normalized,
                inspect_result=inspected,
                show_files=show_files,
            )

        def handle_ignore_mutation(self, form: cgi.FieldStorage, *, add: bool) -> None:
            report_token = (form.getfirst("report_token", "") or "").strip() or None
            raw_username = (form.getfirst("username", "") or "").strip()
            show_files = form.getfirst("show_files") in {"1", "on", "true"}
            normalized = normalize_username(raw_username)
            if normalized is None:
                self.render_document(
                    report_token=report_token,
                    error=f"Invalid Instagram username: {raw_username}",
                    show_files=show_files,
                )
                return

            with state.lock:
                if add:
                    state.ignored_usernames.add(normalized)
                else:
                    state.ignored_usernames.discard(normalized)
                save_ignored_usernames(state.ignore_list_path, state.ignored_usernames)

            params = urllib.parse.urlencode(
                {
                    "report": report_token or "",
                    "show_files": "1" if show_files else "0",
                    "notice": (
                        f"Added @{normalized} to the ignore list."
                        if add
                        else f"Removed @{normalized} from the ignore list."
                    ),
                }
            )
            self.redirect(f"/?{params}")

        def handle_job_status(self, path: str) -> None:
            parts = path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "jobs":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            with state.lock:
                job = state.jobs.get(parts[1])
            if job is None:
                self.send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
                return

            self.send_json(
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "phase": job.phase,
                    "message": job.message,
                    "progress": job.progress,
                    "report_token": job.report_token,
                    "error": job.error,
                    "notice": job.notice,
                }
            )

        def handle_download(self, path: str) -> None:
            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "download":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            _, token, fmt = parts
            with state.lock:
                base_bundle = state.reports.get(token)
                ignored_snapshot = set(state.ignored_usernames)
            if base_bundle is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            bundle = materialize_report_bundle(base_bundle, ignored_snapshot)
            payloads = {
                "csv": ("text/csv; charset=utf-8", bundle.csv_bytes, f"{bundle.mode}.csv"),
                "txt": ("text/plain; charset=utf-8", bundle.txt_bytes, f"{bundle.mode}.txt"),
                "json": ("application/json; charset=utf-8", bundle.json_bytes, f"{bundle.mode}.json"),
            }
            if fmt not in payloads:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            content_type, data, filename = payloads[fmt]
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local live-mode web UI for instagram-followback-checker."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", default=8000, type=int, help="Port to listen on.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    state = AppState()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    url = f"http://{args.host}:{server.server_port}"
    print(f"Instagram Live Followback UI running at {url}")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server...")
    finally:
        server.server_close()
        state.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
