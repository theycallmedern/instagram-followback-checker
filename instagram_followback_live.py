#!/usr/bin/env python3
"""Experimental live-mode scanner that reads follow lists from Instagram Web."""

from __future__ import annotations

import argparse
import collections
import base64
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Set
from urllib import error as urllib_error
from urllib import request as urllib_request

from instagram_followback_checker import (
    AnalysisResult,
    MODE_LABELS,
    RESERVED_PATH_SEGMENTS,
    VERSION,
    apply_limit,
    normalize_username,
    parse_limit,
    print_summary,
    resolve_mode,
    sort_usernames,
    write_csv,
    write_json_report,
    write_txt,
)

DEFAULT_SESSION_DIR = Path.home() / ".instagram-followback-checker" / "live-session"
DEFAULT_LOGIN_WAIT_MS = 240000
SESSION_INFO_FILENAME = "ig_followback_live_session.json"
RELATION_PATHS = {"followers", "following"}
STALL_TOLERANCE_ROUNDS = 8
PLACEHOLDER_USERNAMES = {
    "your_username",
    "<your_username>",
    "your_instagram_username",
    "<your_instagram_username>",
    "username",
}
LOGIN_TEXT_MARKERS = (
    "log in",
    "login",
    "sign up",
    "see instagram photos and videos",
    "continue as",
)
DISMISS_BUTTON_MARKERS = (
    "not now",
    "не сейчас",
    "cancel",
    "отмена",
    "close",
    "закрыть",
    "skip",
    "пропустить",
)
SCROLL_TO_END_SCRIPT = r"""
() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) {
    return { hrefs: [], moved: false, atEnd: true, scrollTop: 0, maxScrollTop: 0 };
  }

  const profileHrefPattern = /^\/[A-Za-z0-9._]+\/$/;

  let target = dialog;
  let bestScrollable = Math.max(0, dialog.scrollHeight - dialog.clientHeight);

  for (const node of dialog.querySelectorAll('*')) {
    const scrollable = Math.max(0, node.scrollHeight - node.clientHeight);
    if (scrollable > bestScrollable + 8) {
      bestScrollable = scrollable;
      target = node;
    }
  }

  const hrefs = Array.from(dialog.querySelectorAll('a[href]'))
    .map((anchor) => anchor.getAttribute('href') || anchor.href || '')
    .filter((href) => profileHrefPattern.test(href));

  const maxScrollTop = Math.max(0, target.scrollHeight - target.clientHeight);
  const before = target.scrollTop;
  target.scrollTop = maxScrollTop;

  return {
    hrefs,
    moved: target.scrollTop > before + 1,
    atEnd: target.scrollTop >= maxScrollTop - 2,
    scrollTop: target.scrollTop,
    maxScrollTop,
  };
}
"""


class LiveModeError(RuntimeError):
    """Raised when the live Instagram session cannot be used."""


ProgressCallback = Callable[[str, str, Optional[int]], None]


def live_relation_url(username: str, relation: str) -> str:
    if relation not in RELATION_PATHS:
        raise ValueError(f"Unsupported relation: {relation}")
    return f"https://www.instagram.com/{username}/{relation}/"


def extract_live_usernames(hrefs: Iterable[str]) -> Set[str]:
    usernames: Set[str] = set()
    for href in hrefs:
        username = normalize_username(href)
        if username is not None:
            usernames.add(username)
    return usernames


def build_live_result(username: str, followers: Set[str], following: Set[str]) -> AnalysisResult:
    return AnalysisResult(
        followers=set(followers),
        following=set(following),
        follower_files=[f"live-instagram://{username}/followers"],
        following_files=[f"live-instagram://{username}/following"],
        follower_timestamps=[],
        following_timestamps=[],
    )


def emit_progress(
    progress_callback: Optional[ProgressCallback],
    phase: str,
    message: str,
    progress: Optional[int] = None,
) -> None:
    if progress_callback is None:
        return
    progress_callback(phase, message, progress)


def require_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LiveModeError(
            "Playwright is not installed. Run "
            "\"python3 -m pip install '.[live]'\" and then "
            "\"python3 -m playwright install chromium\"."
        ) from exc

    return sync_playwright, PlaywrightTimeoutError


def normalize_profile_username(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    normalized_raw = raw.strip().lower()
    if normalized_raw in PLACEHOLDER_USERNAMES:
        raise LiveModeError(
            "Replace the example value in --username with your real Instagram username, "
            "or omit --username and let the script detect it from the logged-in session."
        )
    username = normalize_username(raw)
    if username is None:
        raise LiveModeError(f"Invalid Instagram username: {raw}")
    return username


def prompt_for_profile_username() -> Optional[str]:
    print("Enter your Instagram username and press Enter.")
    print("Leave it empty if you want the script to try auto-detecting it after login.")
    try:
        raw = input("Instagram username: ").strip()
    except EOFError:
        return None

    if not raw:
        return None
    return normalize_profile_username(raw)


def session_info_path(session_dir: Path) -> Path:
    return session_dir / SESSION_INFO_FILENAME


def load_session_info(session_dir: Path) -> dict[str, str]:
    info_path = session_info_path(session_dir)
    if not info_path.exists():
        return {}

    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def save_session_profile(
    session_dir: Path,
    *,
    username: Optional[str] = None,
    avatar_data_url: Optional[str] = None,
) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = load_session_info(session_dir)

    if username is not None:
        payload["username"] = username
    if avatar_data_url is not None:
        payload["avatar_data_url"] = avatar_data_url

    session_info_path(session_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_session_username(session_dir: Path, username: str) -> None:
    save_session_profile(session_dir, username=username)


def load_session_username(session_dir: Path) -> Optional[str]:
    payload = load_session_info(session_dir)
    return normalize_profile_username(payload.get("username"))


def load_session_avatar_data_url(session_dir: Path) -> Optional[str]:
    payload = load_session_info(session_dir)
    avatar_data_url = payload.get("avatar_data_url")
    if not isinstance(avatar_data_url, str):
        return None
    if not avatar_data_url.startswith("data:image/"):
        return None
    return avatar_data_url


def session_has_browser_state(session_dir: Path) -> bool:
    if not session_dir.exists() or not session_dir.is_dir():
        return False

    for child in session_dir.iterdir():
        if child.name == SESSION_INFO_FILENAME:
            continue
        return True
    return False


def clear_live_session(session_dir: Path) -> None:
    if not session_dir.exists():
        return
    if session_dir.is_dir():
        shutil.rmtree(session_dir)
        return
    session_dir.unlink()


def resolve_requested_username(
    raw_username: Optional[str],
    session_dir: Path,
    *,
    allow_prompt: bool = True,
) -> Optional[str]:
    requested_username = normalize_profile_username(raw_username)
    if requested_username is not None:
        return requested_username

    saved_username = load_session_username(session_dir)
    if saved_username is not None:
        return saved_username

    if allow_prompt and sys.stdin.isatty():
        return prompt_for_profile_username()

    return None


def text_suggests_login_required(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = " ".join(text.lower().split())
    return any(marker in normalized for marker in LOGIN_TEXT_MARKERS)


def looks_logged_out(page) -> bool:
    if "/accounts/login" in page.url:
        return True

    state = page.evaluate(
        """
        () => {
          const bodyText = (document.body?.innerText || '').toLowerCase();
          return {
            usernameInput: Boolean(document.querySelector("input[name='username']")),
            passwordInput: Boolean(document.querySelector("input[name='password']")),
            loginButton: Array.from(document.querySelectorAll('button, a'))
              .some((node) => /log in|login|continue as/i.test(node.textContent || '')),
            bodyText,
          };
        }
        """
    )

    return bool(
        state.get("usernameInput")
        or state.get("passwordInput")
        or state.get("loginButton")
        or text_suggests_login_required(state.get("bodyText"))
    )


def dismiss_known_dialogs(page, *, verbose: bool = False) -> bool:
    clicked_label = page.evaluate(
        """
        (markers) => {
          const candidates = Array.from(document.querySelectorAll("button, [role='button']"));
          for (const node of candidates) {
            const text = (node.innerText || node.textContent || '').trim().toLowerCase();
            if (!text) continue;
            if (markers.some((marker) => text === marker || text.startsWith(marker + ' '))) {
              node.click();
              return text;
            }
          }
          return '';
        }
        """,
        list(DISMISS_BUTTON_MARKERS),
    )
    if verbose and clicked_label:
        print(f"Dismissed Instagram dialog button: {clicked_label}")
    return bool(clicked_label)


def wait_for_login_in_browser(
    page,
    *,
    login_timeout_ms: int,
    verbose: bool = False,
) -> None:
    deadline = time.monotonic() + login_timeout_ms / 1000
    logged_in_rounds = 0
    while time.monotonic() < deadline:
        dismiss_known_dialogs(page, verbose=verbose)
        if not looks_logged_out(page):
            logged_in_rounds += 1
            if logged_in_rounds >= 4:
                return
        else:
            logged_in_rounds = 0
        page.wait_for_timeout(1000)

    raise LiveModeError(
        "Instagram session is still not authenticated. Finish the login in the opened browser and try again."
    )


def wait_for_confirmed_login(
    page,
    requested_username: Optional[str],
    timeout_error,
    *,
    login_timeout_ms: int,
    verbose: bool = False,
) -> str:
    deadline = time.monotonic() + login_timeout_ms / 1000
    last_error = "Instagram login could not be confirmed yet."

    while time.monotonic() < deadline:
        dismiss_known_dialogs(page, verbose=verbose)
        if looks_logged_out(page):
            page.wait_for_timeout(1000)
            continue

        if requested_username:
            return requested_username

        resolved_username = infer_username_from_current_page(page)
        if resolved_username:
            return resolved_username

        try:
            page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
        except Exception:
            page.wait_for_timeout(1000)
            continue

        dismiss_known_dialogs(page, verbose=verbose)
        if looks_logged_out(page):
            last_error = "Instagram returned to the login wall while confirming the session."
            page.wait_for_timeout(1000)
            continue

        resolved_username = detect_logged_in_username(
            page,
            timeout_error,
            verbose=verbose,
        )
        if resolved_username:
            return resolved_username

        last_error = "Instagram login succeeded, but the account username could not be confirmed yet."
        page.wait_for_timeout(1000)

    raise LiveModeError(last_error)


def complete_manual_login(
    page,
    target_url: str,
    *,
    terminal_prompt: bool,
    login_timeout_ms: int,
    verbose: bool = False,
) -> None:
    print("Instagram browser opened in live mode.")
    if terminal_prompt:
        print("Log in manually in the browser window, complete any verification, then press Enter here.")
        try:
            input()
        except EOFError as exc:
            raise LiveModeError(
                "Interactive login requires a terminal. Re-run the command from Terminal."
            ) from exc

        page.goto(target_url, wait_until="domcontentloaded")
        return

    print("Log in manually in the browser window. This page will continue automatically after Instagram accepts the session.")
    wait_for_login_in_browser(
        page,
        login_timeout_ms=login_timeout_ms,
        verbose=verbose,
    )


def ensure_logged_in(
    page,
    target_url: str,
    *,
    terminal_prompt: bool,
    login_timeout_ms: int,
    verbose: bool,
) -> None:
    page.goto(target_url, wait_until="domcontentloaded")
    if looks_logged_out(page):
        complete_manual_login(
            page,
            target_url,
            terminal_prompt=terminal_prompt,
            login_timeout_ms=login_timeout_ms,
            verbose=verbose,
        )
        if looks_logged_out(page):
            raise LiveModeError(
                "Instagram session is still not authenticated. Complete the login in the opened browser and try again."
            )


def detect_logged_in_username(page, timeout_error, *, verbose: bool = False) -> Optional[str]:
    page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
    dismiss_known_dialogs(page, verbose=verbose)
    if looks_logged_out(page):
        return None

    return infer_username_from_current_page(page, timeout_error=timeout_error)


def infer_username_from_current_page(page, timeout_error=None) -> Optional[str]:
    # Instagram changes the edit-profile form markup frequently, so we try
    # several sources before giving up on auto-detection.
    direct_selectors = (
        "input[name='username']",
        "input[autocomplete='username']",
        "input[aria-label='Username']",
        "input[aria-label='Имя пользователя']",
    )
    for selector in direct_selectors:
        locator = page.locator(selector).first
        try:
            if timeout_error is not None:
                locator.wait_for(state="visible", timeout=2500)
        except timeout_error:
            continue

        resolved = normalize_username(locator.input_value())
        if resolved:
            return resolved

    dom_candidates = page.evaluate(
        """
        () => {
          const values = [];
          const push = (value) => {
            if (typeof value === "string" && value.trim()) {
              values.push(value.trim());
            }
          };

          [
            "input[name='username']",
            "input[autocomplete='username']",
            "input[aria-label='Username']",
            "input[aria-label='Имя пользователя']",
          ].forEach((selector) => {
            document.querySelectorAll(selector).forEach((node) => {
              push(node.value || node.getAttribute("value") || "");
            });
          });

          document.querySelectorAll("a[href]").forEach((anchor) => {
            push(anchor.getAttribute("href") || anchor.href || "");
          });

          document.querySelectorAll("meta[content]").forEach((meta) => {
            push(meta.getAttribute("content") || "");
          });

          return values;
        }
        """
    )
    resolved = infer_username_from_candidates(dom_candidates)
    if resolved:
        return resolved

    return infer_username_from_html(page.content())


def infer_username_from_candidates(raw_candidates: Iterable[str]) -> Optional[str]:
    counts: collections.Counter[str] = collections.Counter()
    for candidate in raw_candidates:
        normalized = normalize_username(candidate)
        if normalized and normalized not in RESERVED_PATH_SEGMENTS:
            counts[normalized] += 1

    if not counts:
        return None

    if len(counts) == 1:
        return next(iter(counts))

    winner, winner_count = counts.most_common(1)[0]
    runner_up_count = counts.most_common(2)[1][1]
    if winner_count >= 2 and winner_count > runner_up_count:
        return winner

    return None


def infer_username_from_html(html: str) -> Optional[str]:
    if not html:
        return None

    preferred_patterns = (
        r'"viewer"\s*:\s*\{[^{}]{0,400}?"username"\s*:\s*"([A-Za-z0-9._]{1,30})"',
        r'"current_user"\s*:\s*\{[^{}]{0,400}?"username"\s*:\s*"([A-Za-z0-9._]{1,30})"',
        r'"username"\s*:\s*"([A-Za-z0-9._]{1,30})"\s*,\s*"is_private"',
    )
    for pattern in preferred_patterns:
        preferred_matches = re.findall(pattern, html, flags=re.DOTALL)
        resolved = infer_username_from_candidates(preferred_matches)
        if resolved:
            return resolved

    candidates: list[str] = []
    fallback_patterns = (
        r'"username"\s*:\s*"([A-Za-z0-9._]{1,30})"',
        r'https://www\.instagram\.com/([A-Za-z0-9._]{1,30})/',
        r'content="/([A-Za-z0-9._]{1,30})/"',
    )
    for pattern in fallback_patterns:
        candidates.extend(re.findall(pattern, html))

    return infer_username_from_candidates(candidates)


def extract_profile_avatar_url(page) -> Optional[str]:
    candidate = page.evaluate(
        """
        () => {
          const selectors = [
            "meta[property='og:image']",
            "meta[name='twitter:image']",
            "header img",
            "img[alt*='profile picture']",
          ];

          for (const selector of selectors) {
            for (const node of document.querySelectorAll(selector)) {
              const value =
                node.getAttribute?.("content") ||
                node.getAttribute?.("src") ||
                node.src ||
                "";
              if (typeof value === "string" && value.startsWith("http")) {
                return value;
              }
            }
          }

          return "";
        }
        """
    )
    return candidate if isinstance(candidate, str) and candidate else None


def download_image_as_data_url(url: str) -> Optional[str]:
    if not url.startswith("http"):
        return None

    request = urllib_request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0 Safari/537.36"
            )
        },
    )
    try:
        with urllib_request.urlopen(request, timeout=20) as response:
            image_bytes = response.read()
            content_type = response.headers.get_content_type() or "image/jpeg"
    except (OSError, urllib_error.URLError, urllib_error.HTTPError):
        return None

    if not image_bytes:
        return None

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def capture_profile_avatar_data_url(page, username: str, *, verbose: bool = False) -> Optional[str]:
    try:
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
    except Exception:
        return None

    dismiss_known_dialogs(page, verbose=verbose)
    avatar_url = extract_profile_avatar_url(page)
    if not avatar_url:
        return None
    return download_image_as_data_url(avatar_url)


def open_relation_dialog(
    page,
    username: str,
    relation: str,
    timeout_error,
    *,
    verbose: bool,
    terminal_prompt: bool,
    login_timeout_ms: int,
) -> None:
    dialog = page.locator("[role='dialog']").first
    target_url = live_relation_url(username, relation)
    page.goto(target_url, wait_until="domcontentloaded")
    dismiss_known_dialogs(page, verbose=verbose)

    if looks_logged_out(page):
        complete_manual_login(
            page,
            target_url,
            terminal_prompt=terminal_prompt,
            login_timeout_ms=login_timeout_ms,
            verbose=verbose,
        )
        dismiss_known_dialogs(page, verbose=verbose)
        if looks_logged_out(page):
            raise LiveModeError(
                f"Instagram still requires login before opening {relation} for @{username}."
            )

    try:
        dialog.wait_for(state="visible", timeout=12000)
        return
    except timeout_error:
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")

    trigger = page.locator(f"a[href='/{username}/{relation}/']").first
    try:
        trigger.wait_for(state="visible", timeout=12000)
        trigger.click()
    except timeout_error as exc:
        raise LiveModeError(
            f"Could not open the {relation} list for @{username}. Instagram may have changed the page layout."
        ) from exc

    try:
        dialog.wait_for(state="visible", timeout=12000)
    except timeout_error as exc:
        raise LiveModeError(
            f"Instagram opened the profile for @{username}, but the {relation} list never appeared."
        ) from exc


def wait_for_relation_entries(
    page,
    relation: str,
    timeout_error,
    *,
    timeout_ms: int = 12000,
    settle_rounds: int = 3,
    settle_pause_ms: int = 450,
    verbose: bool = False,
) -> None:
    try:
        page.wait_for_function(
            """
            () => {
              const dialog = document.querySelector('[role="dialog"]');
              if (!dialog) return false;
              return Array.from(dialog.querySelectorAll('a[href]'))
                .some((anchor) => /^\\/[A-Za-z0-9._]+\\/$/.test(anchor.getAttribute('href') || ''));
            }
            """,
            timeout=timeout_ms,
        )
        last_count = -1
        stable_rounds = 0
        best_count = 0
        while stable_rounds < settle_rounds:
            count = int(
                page.evaluate(
                    """
                    () => {
                      const dialog = document.querySelector('[role="dialog"]');
                      if (!dialog) return 0;
                      return Array.from(dialog.querySelectorAll('a[href]'))
                        .filter((anchor) => /^\\/[A-Za-z0-9._]+\\/$/.test(anchor.getAttribute('href') || ''))
                        .length;
                    }
                    """
                )
                or 0
            )
            best_count = max(best_count, count)
            if count > 0 and count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_count = count
            page.wait_for_timeout(settle_pause_ms)
        if verbose:
            print(f"[{relation}] dialog stabilized with {best_count} visible profile links before scrolling.")
    except timeout_error:
        if verbose:
            print(f"[{relation}] dialog opened, but Instagram did not render profile links within {timeout_ms}ms.")


def collect_live_relation_usernames(
    page,
    username: str,
    relation: str,
    *,
    max_scrolls: int,
    scroll_pause_ms: int,
    verbose: bool,
    terminal_prompt: bool,
    login_timeout_ms: int,
    timeout_error,
    progress_callback: Optional[ProgressCallback] = None,
) -> Set[str]:
    range_start, range_end = (24, 56) if relation == "followers" else (58, 90)
    emit_progress(
        progress_callback,
        relation,
        f"Opening the {relation} list for @{username}.",
        range_start,
    )
    open_relation_dialog(
        page,
        username,
        relation,
        timeout_error,
        verbose=verbose,
        terminal_prompt=terminal_prompt,
        login_timeout_ms=login_timeout_ms,
    )
    wait_for_relation_entries(
        page,
        relation,
        timeout_error,
        verbose=verbose,
    )
    for attempt in range(3):
        usernames: Set[str] = set()
        stalled_rounds = 0
        previous_max_scroll_top = -1

        for step in range(1, max_scrolls + 1):
            if looks_logged_out(page):
                break
            snapshot = page.evaluate(SCROLL_TO_END_SCRIPT)
            batch = extract_live_usernames(snapshot.get("hrefs", []))
            before = len(usernames)
            usernames.update(batch)
            added = len(usernames) - before
            current_max_scroll_top = int(snapshot.get("maxScrollTop") or 0)
            moved = bool(snapshot.get("moved"))
            grew_scroll_range = current_max_scroll_top > previous_max_scroll_top + 1
            previous_max_scroll_top = max(previous_max_scroll_top, current_max_scroll_top)
            progress = range_start + int(((range_end - range_start) * step) / max_scrolls)
            emit_progress(
                progress_callback,
                relation,
                f"Reading {relation}: {len(usernames)} usernames collected.",
                progress,
            )
            if verbose:
                print(
                    f"[{relation}] scroll {step}: +{added} new usernames "
                    f"(total {len(usernames)})"
                )

            if added == 0 and not moved and not grew_scroll_range:
                stalled_rounds += 1
            else:
                stalled_rounds = 0

            page.wait_for_timeout(scroll_pause_ms)

            # Instagram dialogs often pause for several rounds before loading the next chunk.
            if stalled_rounds >= STALL_TOLERANCE_ROUNDS:
                break

        if usernames:
            emit_progress(
                progress_callback,
                relation,
                f"Collected {len(usernames)} {relation} usernames.",
                range_end,
            )
            return usernames

        if looks_logged_out(page):
            print(f"The {relation} list is blocked by Instagram login.")
            emit_progress(
                progress_callback,
                relation,
                f"Instagram asked for login again while reading {relation}. Finish login in the browser to continue.",
                range_start,
            )
            ensure_logged_in(
                page,
                live_relation_url(username, relation),
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
                verbose=verbose,
            )
            open_relation_dialog(
                page,
                username,
                relation,
                timeout_error,
                verbose=verbose,
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
            )
            wait_for_relation_entries(
                page,
                relation,
                timeout_error,
                verbose=verbose,
            )
            continue

    raise LiveModeError(
        f"The live {relation} list for @{username} did not return any usernames. If Instagram kept asking for login, reconnect the session and try again."
    )


def analyze_live_session(
    *,
    username: Optional[str],
    session_dir: Path,
    headless: bool,
    max_scrolls: int,
    scroll_pause_ms: int,
    verbose: bool,
    terminal_prompt: bool = True,
    login_timeout_ms: int = DEFAULT_LOGIN_WAIT_MS,
    progress_callback: Optional[ProgressCallback] = None,
) -> tuple[str, AnalysisResult]:
    sync_playwright, timeout_error = require_playwright()
    session_dir.mkdir(parents=True, exist_ok=True)
    emit_progress(progress_callback, "boot", "Launching the live Instagram browser.", 4)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(session_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1100},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            landing_url = f"https://www.instagram.com/{username}/" if username else "https://www.instagram.com/"
            emit_progress(progress_callback, "auth", "Checking the saved Instagram session.", 12)
            ensure_logged_in(
                page,
                landing_url,
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
                verbose=verbose,
            )
            emit_progress(progress_callback, "auth", "Instagram session is ready.", 20)

            resolved_username = username or detect_logged_in_username(
                page,
                timeout_error,
                verbose=verbose,
            )
            if not resolved_username:
                raise LiveModeError(
                    "Could not determine the logged-in Instagram username automatically. Pass --username explicitly."
                )

            avatar_data_url = capture_profile_avatar_data_url(
                page,
                resolved_username,
                verbose=verbose,
            )
            save_session_profile(
                session_dir,
                username=resolved_username,
                avatar_data_url=avatar_data_url,
            )

            emit_progress(progress_callback, "followers", "Starting the followers scan.", 24)
            followers = collect_live_relation_usernames(
                page,
                resolved_username,
                "followers",
                max_scrolls=max_scrolls,
                scroll_pause_ms=scroll_pause_ms,
                verbose=verbose,
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
                timeout_error=timeout_error,
                progress_callback=progress_callback,
            )
            emit_progress(progress_callback, "following", "Starting the following scan.", 58)
            following = collect_live_relation_usernames(
                page,
                resolved_username,
                "following",
                max_scrolls=max_scrolls,
                scroll_pause_ms=scroll_pause_ms,
                verbose=verbose,
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
                timeout_error=timeout_error,
                progress_callback=progress_callback,
            )
        finally:
            context.close()

    emit_progress(
        progress_callback,
        "finalize",
        "Comparing followers and following and preparing the report.",
        96,
    )
    return resolved_username, build_live_result(resolved_username, followers, following)


def login_only(
    *,
    username: Optional[str],
    session_dir: Path,
    headless: bool,
    terminal_prompt: bool = True,
    login_timeout_ms: int = DEFAULT_LOGIN_WAIT_MS,
    verbose: bool = False,
) -> Optional[str]:
    sync_playwright, timeout_error = require_playwright()
    session_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(session_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1100},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_logged_in(
                page,
                "https://www.instagram.com/",
                terminal_prompt=terminal_prompt,
                login_timeout_ms=login_timeout_ms,
                verbose=verbose,
            )
            resolved_username = wait_for_confirmed_login(
                page,
                username,
                timeout_error,
                login_timeout_ms=login_timeout_ms,
                verbose=verbose,
            )
            avatar_data_url = None
            if resolved_username:
                avatar_data_url = capture_profile_avatar_data_url(
                    page,
                    resolved_username,
                    verbose=verbose,
                )
                save_session_profile(
                    session_dir,
                    username=resolved_username,
                    avatar_data_url=avatar_data_url,
                )
        finally:
            context.close()

    return resolved_username


def print_live_metadata(session_dir: Path, username: str) -> None:
    print(f"Source: live Instagram session")
    print(f"Username: {username}")
    print(f"Session directory: {session_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Experimental live-mode followback checker using a visible Playwright browser."
    )
    parser.add_argument(
        "--username",
        help="Your Instagram username. If omitted, the script tries to detect it from the logged-in session.",
    )
    parser.add_argument(
        "--session-dir",
        default=str(DEFAULT_SESSION_DIR),
        help="Directory where the persistent Instagram browser session is stored.",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Open Instagram, let you log in manually, save the session, and exit without scanning.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headlessly. Not recommended for the first login.",
    )
    parser.add_argument(
        "--max-scrolls",
        type=parse_limit,
        default=250,
        help="Maximum number of scroll rounds per relation list.",
    )
    parser.add_argument(
        "--scroll-pause-ms",
        type=parse_limit,
        default=700,
        help="Pause between scroll rounds in milliseconds.",
    )
    parser.add_argument(
        "--fans",
        action="store_true",
        help="Show accounts that follow you, but you do not follow back.",
    )
    parser.add_argument(
        "--mutuals",
        action="store_true",
        help="Show mutual follow relationships instead of non-followers.",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print summary counts without listing usernames.",
    )
    parser.add_argument("--csv", dest="csv_path", help="Write the selected results to CSV.")
    parser.add_argument("--txt", dest="txt_path", help="Write the selected results to TXT.")
    parser.add_argument("--json", dest="json_path", help="Write a JSON report.")
    parser.add_argument(
        "--limit",
        type=parse_limit,
        help="Limit the number of displayed and exported accounts.",
    )
    parser.add_argument(
        "--sort",
        choices=["alpha", "length"],
        default="alpha",
        help="Sort usernames alphabetically or by length.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print live scrolling progress while collecting usernames.",
    )
    parser.add_argument(
        "--inspect",
        metavar="USERNAME",
        help="Inspect one specific username after the live scan completes.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"instagram-followback-live {VERSION}",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        session_dir = Path(args.session_dir).expanduser()
        requested_username = resolve_requested_username(args.username, session_dir, allow_prompt=True)

        if args.login_only:
            resolved_username = login_only(
                username=requested_username,
                session_dir=session_dir,
                headless=args.headless,
            )
            print(f"Live session saved: {session_dir}")
            if resolved_username:
                save_session_username(session_dir, resolved_username)
                print(f"Logged in as: {resolved_username}")
            return 0

        resolved_username, result = analyze_live_session(
            username=requested_username,
            session_dir=session_dir,
            headless=args.headless,
            max_scrolls=args.max_scrolls,
            scroll_pause_ms=args.scroll_pause_ms,
            verbose=args.verbose,
        )
        save_session_username(session_dir, resolved_username)
    except LiveModeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    mode = resolve_mode(args)
    selected_usernames = sort_usernames(result.usernames_for_mode(mode), args.sort)
    displayed_usernames = apply_limit(selected_usernames, args.limit)

    print_live_metadata(session_dir, resolved_username)
    print_summary(result)

    if args.inspect:
        inspected = result.inspect_username(args.inspect)
        print("\nInspect:")
        print(f"Username: {inspected['username']}")
        print(f"In followers: {inspected['in_followers']}")
        print(f"In following: {inspected['in_following']}")
        print(f"Relationship: {inspected['relationship']}")

    if not args.stats_only:
        print(f"\nMode: {MODE_LABELS[mode]}")
        print(f"Matching accounts: {len(selected_usernames)}")
        if args.limit is not None and len(displayed_usernames) != len(selected_usernames):
            print(f"Showing first {len(displayed_usernames)} account(s) due to --limit.")

        if displayed_usernames:
            print("\nUsernames:")
            for username in displayed_usernames:
                print(username)
        else:
            print("\nNo matching accounts found.")

    if args.csv_path:
        csv_path = Path(args.csv_path).expanduser()
        write_csv(csv_path, displayed_usernames)
        print(f"\nCSV saved: {csv_path}")

    if args.txt_path:
        txt_path = Path(args.txt_path).expanduser()
        write_txt(txt_path, displayed_usernames)
        print(f"TXT saved: {txt_path}")

    if args.json_path:
        json_path = Path(args.json_path).expanduser()
        write_json_report(
            json_path,
            result,
            mode,
            args.sort,
            args.limit,
            displayed_usernames,
        )
        print(f"JSON saved: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
