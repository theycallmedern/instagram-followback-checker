#!/usr/bin/env python3
"""Capture product-style desktop screenshots using a mocked Tauri runtime."""

from __future__ import annotations

import contextlib
import json
import os
import socketserver
import threading
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "screenshots"
HOST = "127.0.0.1"
PORT = 41731
DEFAULT_BROWSERS_PATH = ROOT / ".desktop-runtime" / "playwright-browsers"


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


def build_report() -> dict[str, object]:
    entries = [
        {"username": "northlight", "profile_url": "https://www.instagram.com/northlight/"},
        {"username": "atelierframe", "profile_url": "https://www.instagram.com/atelierframe/"},
        {"username": "nova.collective", "profile_url": "https://www.instagram.com/nova.collective/"},
        {"username": "studioharbor", "profile_url": "https://www.instagram.com/studioharbor/"},
        {"username": "fwdvision", "profile_url": "https://www.instagram.com/fwdvision/"},
        {"username": "quietarchive", "profile_url": "https://www.instagram.com/quietarchive/"},
    ]
    return {
        "scan_username": "studio.demo",
        "created_at": "2026-03-13T12:48:00Z",
        "report_source": "live",
        "mode": "nonfollowers",
        "mode_label": "Accounts you follow that do not follow you back",
        "sort": "alpha",
        "limit": None,
        "stats_only": False,
        "stats": {
            "followers": 412,
            "following": 538,
            "nonfollowers": 126,
            "fans": 49,
            "mutuals": 363,
        },
        "warnings": [
            "Instagram hid part of one dialog while the scanner was collecting results.",
            "One profile was skipped because the relation link changed during the scan.",
        ],
        "time_ranges": {
            "followers": {"start_date": "2026-03-12", "end_date": "2026-03-13"},
            "following": {"start_date": "2026-03-12", "end_date": "2026-03-13"},
        },
        "followers_usernames": [
            "calm.signal",
            "field.note",
            "northlight",
            "open.room",
            "studio.demo",
        ],
        "following_usernames": [
            "atelierframe",
            "northlight",
            "nova.collective",
            "quietarchive",
            "studio.demo",
            "studioharbor",
            "fwdvision",
        ],
        "entries": entries,
        "all_entries": entries,
        "shown_matches": len(entries),
        "total_matches": 126,
        "used_files": {
            "followers": ["live-instagram://studio.demo/followers"],
            "following": ["live-instagram://studio.demo/following"],
        },
    }


def build_history() -> dict[str, object]:
    return {
        "username": "studio.demo",
        "latest_snapshot_id": "snap-20260313-124800",
        "previous_snapshot_id": "snap-20260312-184500",
        "changes": {
            "new_nonfollowers": ["atelierframe", "quietarchive", "studioharbor"],
            "returned_mutuals": ["northlight", "open.room"],
            "disappeared_fans": ["calm.signal"],
        },
        "entries": [
            {
                "snapshot_id": "snap-20260313-124800",
                "username": "studio.demo",
                "created_at": "2026-03-13T12:48:00Z",
                "stats": {
                    "followers": 412,
                    "following": 538,
                    "nonfollowers": 126,
                    "fans": 49,
                    "mutuals": 363,
                },
                "has_warnings": True,
                "warning_count": 2,
            },
            {
                "snapshot_id": "snap-20260312-184500",
                "username": "studio.demo",
                "created_at": "2026-03-12T18:45:00Z",
                "stats": {
                    "followers": 408,
                    "following": 531,
                    "nonfollowers": 123,
                    "fans": 50,
                    "mutuals": 358,
                },
                "has_warnings": False,
                "warning_count": 0,
            },
            {
                "snapshot_id": "snap-20260310-093000",
                "username": "studio.demo",
                "created_at": "2026-03-10T09:30:00Z",
                "stats": {
                    "followers": 401,
                    "following": 520,
                    "nonfollowers": 119,
                    "fans": 53,
                    "mutuals": 348,
                },
                "has_warnings": False,
                "warning_count": 0,
            },
        ],
    }


def build_history_detail() -> dict[str, object]:
    return {
        "username": "studio.demo",
        "comparison_mode": "custom",
        "changes": {
            "new_nonfollowers": ["atelierframe", "quietarchive", "studioharbor"],
            "returned_mutuals": ["northlight", "open.room"],
            "disappeared_fans": ["calm.signal"],
        },
        "available_comparisons": [
            {
                "snapshot_id": "snap-20260312-184500",
                "created_at": "2026-03-12T18:45:00Z",
                "stats": {"nonfollowers": 123, "fans": 50, "mutuals": 358},
            },
            {
                "snapshot_id": "snap-20260310-093000",
                "created_at": "2026-03-10T09:30:00Z",
                "stats": {"nonfollowers": 119, "fans": 53, "mutuals": 348},
            },
        ],
        "comparison_snapshot": {
            "snapshot_id": "snap-20260310-093000",
            "username": "studio.demo",
            "created_at": "2026-03-10T09:30:00Z",
            "stats": {
                "followers": 401,
                "following": 520,
                "nonfollowers": 119,
                "fans": 53,
                "mutuals": 348,
            },
        },
        "snapshot": {
            "snapshot_id": "snap-20260313-124800",
            "username": "studio.demo",
            "created_at": "2026-03-13T12:48:00Z",
            "stats": {
                "followers": 412,
                "following": 538,
                "nonfollowers": 126,
                "fans": 49,
                "mutuals": 363,
            },
            "has_warnings": True,
            "warning_count": 2,
            "warnings": [
                "Instagram hid part of one dialog while the scanner was collecting results.",
                "One profile was skipped because the relation link changed during the scan.",
            ],
            "mode_lists": {
                "nonfollowers": [
                    "atelierframe",
                    "fwdvision",
                    "nova.collective",
                    "quietarchive",
                    "studioharbor",
                    "northlight",
                ],
                "fans": ["calm.signal", "field.note", "open.room"],
                "mutuals": ["northlight", "open.room", "studio.demo", "field.note"],
            },
        },
    }


def build_mock_script() -> str:
    session = {
        "connected": True,
        "username": "studio.demo",
        "avatar_data_url": avatar_data_url("SD", start="#6AA8FF", end="#FF8B71"),
        "browser_state_present": True,
        "session_dir": "/Users/demo/Library/Application Support/com.mishabelyakov.instagramfollowback/live-session",
    }
    history = build_history()
    detail = build_history_detail()
    report = {
        **build_report(),
        "history": history,
    }
    progress_steps = [
        {"phase": "boot", "message": "Launching the local analysis runtime.", "progress": 6},
        {"phase": "auth", "message": "Reusing the saved Instagram session.", "progress": 18},
        {"phase": "followers", "message": "Reading followers.", "progress": 44},
        {"phase": "following", "message": "Reading following.", "progress": 71},
        {"phase": "finalize", "message": "Preparing the report.", "progress": 94},
    ]
    payload = json.dumps(
        {
            "session": session,
            "report": report,
            "progress_steps": progress_steps,
            "history_detail": detail,
        }
    )
    return f"""
      (() => {{
        const payload = {payload};
        const listeners = new Map();
        window.__DOCS_MOCK__ = payload;

        window.__TAURI__ = {{
          core: {{
            invoke: async (command, args) => {{
              if (command === "get_session_status") {{
                return payload.session;
              }}
              if (command === "disconnect_instagram") {{
                return {{
                  ...payload.session,
                  connected: false,
                  username: null,
                  avatar_data_url: null,
                  browser_state_present: false,
                }};
              }}
              if (command === "connect_instagram") {{
                return {{ started: true, message: "Instagram login browser launched." }};
              }}
              if (command === "run_live_scan") {{
                const handler = listeners.get("scan-progress");
                if (handler) {{
                  for (const step of payload.progress_steps) {{
                    handler({{ payload: step }});
                  }}
                }}
                await new Promise((resolve) => setTimeout(resolve, 120));
                return payload.report;
              }}
              if (command === "get_scan_history") {{
                return payload.report.history;
              }}
              if (command === "get_scan_history_detail") {{
                return payload.history_detail;
              }}
              if (command === "get_latest_saved_report") {{
                return {{
                  ...payload.report,
                  report_source: "history",
                }};
              }}
              if (command === "clear_scan_history") {{
                return {{
                  removed: payload.report.history.entries.length,
                  history: {{
                    username: payload.report.history.username,
                    latest_snapshot_id: null,
                    previous_snapshot_id: null,
                    entries: [],
                    changes: {{
                      new_nonfollowers: [],
                      returned_mutuals: [],
                      disappeared_fans: [],
                    }},
                  }},
                }};
              }}
              if (command === "export_scan_history") {{
                return {{
                  path: "/Users/demo/Downloads/studio-demo-history.json",
                }};
              }}
              throw new Error(`Unsupported mocked command: ${{command}}`);
            }},
          }},
          event: {{
            listen: async (eventName, callback) => {{
              listeners.set(eventName, callback);
              return async () => listeners.delete(eventName);
            }},
          }},
        }};
      }})();
    """


@contextlib.contextmanager
def local_server():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, fmt, *args):
            return

    httpd = socketserver.TCPServer((HOST, PORT), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{PORT}/desktop-shell/index.html"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1)


def capture_overview(page, output_path: Path) -> None:
    page.goto(page.url, wait_until="networkidle")
    page.locator(".desktop-window").screenshot(path=str(output_path))


def populate_results_state(page) -> None:
    page.evaluate(
        """
        () => {
          renderReport(window.__DOCS_MOCK__.report);
          document.getElementById('diagnosticsToggle').checked = true;
          renderWarnings(window.__DOCS_MOCK__.report);
          document.getElementById('inspectInput').value = 'northlight';
          renderInspect();
          setStatus('Live scan completed for @studio.demo.');
        }
        """
    )


def populate_history_state(page) -> None:
    page.evaluate(
        """
        () => {
          renderReport(window.__DOCS_MOCK__.report);
          state.selectedHistorySnapshotId = window.__DOCS_MOCK__.history_detail.snapshot.snapshot_id;
          state.compareHistorySnapshotId = window.__DOCS_MOCK__.history_detail.comparison_snapshot.snapshot_id;
          state.historyDetailExpanded = true;
          renderHistory(window.__DOCS_MOCK__.report.history);
          renderHistoryDetail(window.__DOCS_MOCK__.history_detail);
          setStatus('Showing saved snapshot history for @studio.demo.');
        }
        """
    )


def capture_results(page, output_path: Path) -> None:
    populate_results_state(page)
    page.locator(".desktop-window").screenshot(path=str(output_path))


def capture_region(page, selectors: list[str], output_path: Path, *, padding: int = 28) -> None:
    clip = page.evaluate(
        """
        ({ selectors, padding }) => {
          const boxes = selectors.flatMap((selector) =>
            Array.from(document.querySelectorAll(selector)).map((element) => {
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
              };
            })
          ).filter((box) => box.right > box.left && box.bottom > box.top);

          if (!boxes.length) {
            return null;
          }

          const left = Math.max(0, Math.min(...boxes.map((box) => box.left)) - padding);
          const top = Math.max(0, Math.min(...boxes.map((box) => box.top)) - padding);
          const right = Math.min(window.innerWidth, Math.max(...boxes.map((box) => box.right)) + padding);
          const bottom = Math.min(document.documentElement.scrollHeight, Math.max(...boxes.map((box) => box.bottom)) + padding);

          return {
            x: left,
            y: top,
            width: Math.max(1, right - left),
            height: Math.max(1, bottom - top),
          };
        }
        """,
        {"selectors": selectors, "padding": padding},
    )
    if not clip:
        raise RuntimeError(f"Could not capture region for selectors: {selectors}")
    page.screenshot(path=str(output_path), clip=clip)


def capture_feature_gallery(page) -> None:
    populate_results_state(page)

    capture_region(
        page,
        [".window-bar", ".sidebar", ".workspace-card", ".metrics-grid"],
        OUTPUT_DIR / "hero-workspace.png",
        padding=14,
    )
    capture_region(
        page,
        [".nav-card"],
        OUTPUT_DIR / "session-panel.png",
        padding=8,
    )
    capture_region(
        page,
        [".nav-card", ".field-stack", ".button-stack", ".action-feedback"],
        OUTPUT_DIR / "sidebar-flow.png",
        padding=12,
    )
    capture_region(
        page,
        [".field-stack", ".toggle-list", ".button-stack", ".action-feedback"],
        OUTPUT_DIR / "scan-controls.png",
        padding=18,
    )
    capture_region(
        page,
        [".table-card"],
        OUTPUT_DIR / "results-table.png",
        padding=18,
    )
    capture_region(
        page,
        [".table-card", ".inspector-card"],
        OUTPUT_DIR / "analysis-workspace.png",
        padding=10,
    )
    capture_region(
        page,
        [".inspector-card"],
        OUTPUT_DIR / "inspector-diagnostics.png",
        padding=18,
    )


def capture_history_showcase(page) -> None:
    populate_history_state(page)
    capture_region(
        page,
        [".history-card"],
        OUTPUT_DIR / "history-timeline.png",
        padding=16,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if DEFAULT_BROWSERS_PATH.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(DEFAULT_BROWSERS_PATH))
    with local_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": 1540, "height": 1100},
                device_scale_factor=2,
                color_scheme="dark",
            )
            page = context.new_page()
            page.add_init_script(build_mock_script())
            page.goto(url, wait_until="networkidle")
            time.sleep(0.25)
            capture_overview(page, OUTPUT_DIR / "overview.png")

            page = context.new_page()
            page.add_init_script(build_mock_script())
            page.goto(url, wait_until="networkidle")
            time.sleep(0.25)
            capture_results(page, OUTPUT_DIR / "results.png")

            page = context.new_page()
            page.add_init_script(build_mock_script())
            page.goto(url, wait_until="networkidle")
            time.sleep(0.25)
            capture_feature_gallery(page)

            page = context.new_page()
            page.add_init_script(build_mock_script())
            page.goto(url, wait_until="networkidle")
            time.sleep(0.25)
            capture_history_showcase(page)
            context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
