#!/usr/bin/env python3
"""Install or restore a synthetic desktop app state for screenshots."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


DEFAULT_STATE_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "com.mishabelyakov.instagramfollowback"
)
SESSION_INFO_FILENAME = "ig_followback_live_session.json"


def avatar_data_url(initials: str, *, start: str, end: str) -> str:
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">
      <defs>
        <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="{start}"/>
          <stop offset="100%" stop-color="{end}"/>
        </linearGradient>
      </defs>
      <rect width="160" height="160" rx="80" fill="url(#g)"/>
      <circle cx="80" cy="80" r="76" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="2"/>
      <text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle"
            font-family="-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif"
            font-size="58" font-weight="700" fill="white">{initials}</text>
    </svg>
    """.strip()
    return f"data:image/svg+xml;utf8,{quote(svg)}"


def synthetic_usernames(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{index:03d}" for index in range(1, count + 1)]


def build_exact_set(featured: list[str], prefix: str, count: int) -> set[str]:
    featured_items = featured[:count]
    remaining = max(0, count - len(featured_items))
    return set(featured_items) | set(synthetic_usernames(prefix, remaining))


def build_snapshot_payload(
    *,
    snapshot_id: str,
    username: str,
    created_at: str,
    mutual_count: int,
    fans_count: int,
    nonfollowers_count: int,
    following_stat: int,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    mutuals = build_exact_set(["northlight", "open.room"], "mutual_", mutual_count)
    fans = build_exact_set(["calm.signal", "field.note"], "fan_", fans_count)
    nonfollowers = build_exact_set(
        ["atelierframe", "nova.collective", "studioharbor", "fwdvision", "quietarchive"],
        "nonfollow_",
        nonfollowers_count,
    )

    followers = sorted(mutuals | fans)
    following = sorted(mutuals | nonfollowers)
    return {
        "snapshot_id": snapshot_id,
        "username": username,
        "created_at": created_at,
        "followers": followers,
        "following": following,
        "stats": {
            "followers": mutual_count + fans_count,
            "following": following_stat,
            "nonfollowers": nonfollowers_count,
            "fans": fans_count,
            "mutuals": mutual_count,
        },
        "warnings": warnings or [],
    }


def backup_state(state_dir: Path) -> Path | None:
    live_session = state_dir / "live-session"
    history = state_dir / "history"
    if not live_session.exists() and not history.exists():
        return None

    backups_root = state_dir.parent / f"{state_dir.name}.backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backups_root / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    if live_session.exists():
        shutil.copytree(live_session, backup_dir / "live-session")
    if history.exists():
        shutil.copytree(history, backup_dir / "history")
    return backup_dir


def create_cookie_db(session_dir: Path) -> None:
    cookie_db = session_dir / "Default" / "Cookies"
    cookie_db.parent.mkdir(parents=True, exist_ok=True)
    if cookie_db.exists():
        cookie_db.unlink()

    connection = sqlite3.connect(cookie_db)
    try:
        connection.execute(
            """
            create table cookies(
                creation_utc integer not null,
                host_key text not null,
                top_frame_site_key text not null,
                name text not null,
                value text not null,
                encrypted_value blob not null,
                path text not null,
                expires_utc integer not null,
                is_secure integer not null,
                is_httponly integer not null,
                last_access_utc integer not null,
                has_expires integer not null,
                is_persistent integer not null,
                priority integer not null,
                samesite integer not null,
                source_scheme integer not null,
                source_port integer not null,
                last_update_utc integer not null,
                source_type integer not null,
                has_cross_site_ancestor integer not null
            )
            """
        )
        connection.execute(
            """
            insert into cookies values
            (0, '.instagram.com', '', 'ds_user_id', 'demo-user-id', x'', '/', 0, 1, 0, 0, 1, 1, 1, 0, 0, 443, 0, 0, 0),
            (0, '.instagram.com', '', 'sessionid', 'demo-session-id', x'', '/', 0, 1, 0, 0, 1, 1, 1, 0, 0, 443, 0, 0, 0)
            """
        )
        connection.commit()
    finally:
        connection.close()


def install_demo_state(state_dir: Path) -> Path | None:
    backup_dir = backup_state(state_dir)
    live_session = state_dir / "live-session"
    history = state_dir / "history"

    if live_session.exists():
        shutil.rmtree(live_session)
    if history.exists():
        shutil.rmtree(history)

    live_session.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    session_info = {
        "username": "studio.demo",
        "avatar_data_url": avatar_data_url("SD", start="#6AA8FF", end="#FF8B71"),
    }
    (live_session / SESSION_INFO_FILENAME).write_text(
        json.dumps(session_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    create_cookie_db(live_session)
    (live_session / "Local State").write_text("{}", encoding="utf-8")

    snapshots = [
        build_snapshot_payload(
            snapshot_id="snap-20260313-124800",
            username="studio.demo",
            created_at="2026-03-13T12:48:00Z",
            mutual_count=363,
            fans_count=49,
            nonfollowers_count=10,
            following_stat=373,
            warnings=[
                "Instagram hid part of one dialog while the scanner was collecting results.",
                "One profile was skipped because the relation link changed during the scan.",
            ],
        ),
        build_snapshot_payload(
            snapshot_id="snap-20260312-184500",
            username="studio.demo",
            created_at="2026-03-12T18:45:00Z",
            mutual_count=358,
            fans_count=50,
            nonfollowers_count=8,
            following_stat=366,
        ),
        build_snapshot_payload(
            snapshot_id="snap-20260310-173000",
            username="studio.demo",
            created_at="2026-03-10T17:30:00Z",
            mutual_count=348,
            fans_count=53,
            nonfollowers_count=6,
            following_stat=354,
        ),
    ]

    for payload in snapshots:
        (history / f"{payload['snapshot_id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return backup_dir


def restore_backup(state_dir: Path, backup_dir: Path) -> None:
    live_session = state_dir / "live-session"
    history = state_dir / "history"
    if live_session.exists():
        shutil.rmtree(live_session)
    if history.exists():
        shutil.rmtree(history)

    source_live = backup_dir / "live-session"
    source_history = backup_dir / "history"
    if source_live.exists():
        shutil.copytree(source_live, live_session)
    if source_history.exists():
        shutil.copytree(source_history, history)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install a synthetic studio.demo desktop state for screenshots."
    )
    parser.add_argument(
        "--state-dir",
        default=str(DEFAULT_STATE_DIR),
        help="Desktop app state directory to modify.",
    )
    parser.add_argument(
        "--restore",
        help="Optional backup directory to restore instead of installing demo data.",
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)

    if args.restore:
        backup_dir = Path(args.restore).expanduser()
        restore_backup(state_dir, backup_dir)
        print(f"Restored desktop app state from: {backup_dir}")
        return

    backup_dir = install_demo_state(state_dir)
    print(f"Installed synthetic desktop state into: {state_dir}")
    if backup_dir:
        print(f"Backup saved at: {backup_dir}")
    else:
        print("No existing desktop state was found, so no backup was created.")


if __name__ == "__main__":
    main()
