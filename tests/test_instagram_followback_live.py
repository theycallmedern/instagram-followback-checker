import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sqlite3

from instagram_followback_live import (
    append_login_debug,
    build_live_result,
    clear_login_state,
    clear_live_session,
    confirm_authenticated_session_in_fresh_page,
    current_page_confirms_authenticated_shell,
    extract_username_from_instagram_api_payload,
    extract_live_usernames,
    extract_profile_avatar_url,
    fetch_logged_in_username_from_instagram_api,
    has_authenticated_instagram_cookies,
    has_authenticated_instagram_shell,
    infer_authenticated_username,
    infer_username_from_profile_navigation,
    infer_username_from_candidates,
    infer_username_from_current_page,
    infer_username_from_html,
    live_relation_url,
    load_login_state,
    load_session_avatar_data_url,
    load_session_username,
    looks_logged_out,
    normalize_profile_username,
    prompt_for_profile_username,
    save_session_profile,
    save_login_state,
    resolve_requested_username,
    save_session_username,
    session_has_authenticated_instagram_cookies,
    session_has_browser_state,
    text_suggests_login_required,
    url_suggests_login_flow,
    wait_for_confirmed_login,
    wait_for_login_in_browser,
)


class InstagramFollowbackLiveTests(unittest.TestCase):
    def test_extract_live_usernames_filters_non_profile_paths(self):
        usernames = extract_live_usernames(
            [
                "https://www.instagram.com/alice/",
                "https://www.instagram.com/_u/bob/",
                "https://www.instagram.com/p/abc123/",
                "https://www.instagram.com/explore/people/",
                "/carol/",
                "not an instagram profile",
            ]
        )

        self.assertEqual(usernames, {"alice", "bob", "carol"})

    def test_build_live_result_reuses_analysis_result_shape(self):
        result = build_live_result("demo_user", {"alice", "bob"}, {"alice", "carol"})

        self.assertEqual(result.stats()["followers"], 2)
        self.assertEqual(result.stats()["following"], 2)
        self.assertEqual(result.not_following_back(), ["carol"])
        self.assertEqual(result.fans(), ["bob"])
        self.assertEqual(result.follower_files, ["live-instagram://demo_user/followers"])
        self.assertEqual(result.following_files, ["live-instagram://demo_user/following"])

    def test_live_relation_url_uses_expected_profile_path(self):
        self.assertEqual(
            live_relation_url("demo_user", "followers"),
            "https://www.instagram.com/demo_user/followers/",
        )
        self.assertEqual(
            live_relation_url("demo_user", "following"),
            "https://www.instagram.com/demo_user/following/",
        )

    def test_normalize_profile_username_rejects_invalid_values(self):
        self.assertEqual(normalize_profile_username("@Demo.User"), "demo.user")
        with self.assertRaisesRegex(RuntimeError, "Invalid Instagram username"):
            normalize_profile_username("https://example.com/not-instagram")
        with self.assertRaisesRegex(RuntimeError, "Replace the example value"):
            normalize_profile_username("your_username")

    def test_prompt_for_profile_username_normalizes_input(self):
        with mock.patch("builtins.input", return_value="  Demo.User  "):
            self.assertEqual(prompt_for_profile_username(), "demo.user")

    def test_prompt_for_profile_username_allows_empty_input(self):
        with mock.patch("builtins.input", return_value="   "):
            self.assertIsNone(prompt_for_profile_username())

    def test_text_suggests_login_required_matches_auth_wall_copy(self):
        self.assertTrue(text_suggests_login_required("Log in to continue"))
        self.assertTrue(text_suggests_login_required("Log into Instagram"))
        self.assertTrue(text_suggests_login_required("See Instagram photos and videos from your friends."))
        self.assertFalse(text_suggests_login_required("followers following posts"))

    def test_url_suggests_login_flow_matches_instagram_auth_routes(self):
        self.assertTrue(url_suggests_login_flow("https://www.instagram.com/accounts/login/"))
        self.assertTrue(url_suggests_login_flow("https://www.instagram.com/challenge/abc"))
        self.assertFalse(url_suggests_login_flow("https://www.instagram.com/accounts/onetap/"))
        self.assertFalse(url_suggests_login_flow("https://www.instagram.com/demo.user/"))

    def test_has_authenticated_instagram_shell_detects_app_navigation(self):
        page = mock.Mock()
        page.evaluate.return_value = {
            "hasKnownAppHref": True,
            "hasProfileNav": False,
            "visibleLabelCount": 3,
            "bodyText": "home messages profile",
        }
        self.assertTrue(has_authenticated_instagram_shell(page))

    def test_confirm_authenticated_session_in_fresh_page_checks_probe_page(self):
        context = mock.Mock()
        probe_page = mock.Mock()
        context.new_page.return_value = probe_page

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.settle_instagram_account_page"), \
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.infer_authenticated_username", return_value="demo.user"):
            self.assertTrue(confirm_authenticated_session_in_fresh_page(context))

        probe_page.goto.assert_called()
        probe_page.close.assert_called_once()

    def test_confirm_authenticated_session_in_fresh_page_rejects_unconfirmed_account(self):
        context = mock.Mock()
        probe_page = mock.Mock()
        context.new_page.return_value = probe_page

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.settle_instagram_account_page"), \
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.infer_authenticated_username", return_value=None):
            self.assertFalse(confirm_authenticated_session_in_fresh_page(context))

    def test_infer_authenticated_username_prefers_explicit_profile_inputs(self):
        page = mock.Mock()
        page.locator.return_value.first.input_value.return_value = "Demo.User"

        with mock.patch("instagram_followback_live.fetch_logged_in_username_from_instagram_api", return_value=None):
            self.assertEqual(infer_authenticated_username(page), "demo.user")

    def test_infer_authenticated_username_uses_profile_navigation_only_with_shell(self):
        page = mock.Mock()
        page.locator.return_value.first.input_value.return_value = ""

        with mock.patch("instagram_followback_live.fetch_logged_in_username_from_instagram_api", return_value=None), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_shell", return_value=True), \
             mock.patch("instagram_followback_live.infer_username_from_profile_navigation", return_value="demo.user"):
            self.assertEqual(infer_authenticated_username(page), "demo.user")

    def test_current_page_confirms_authenticated_shell_rejects_login_routes(self):
        page = mock.Mock()
        page.url = "https://www.instagram.com/accounts/login/"

        with mock.patch("instagram_followback_live.has_authenticated_instagram_shell", return_value=True):
            self.assertFalse(current_page_confirms_authenticated_shell(page))

    def test_current_page_confirms_authenticated_shell_accepts_visible_shell(self):
        page = mock.Mock()
        page.url = "https://www.instagram.com/"

        with mock.patch("instagram_followback_live.has_authenticated_instagram_shell", return_value=True):
            self.assertTrue(current_page_confirms_authenticated_shell(page))

    def test_wait_for_login_in_browser_retries_transient_navigation_errors(self):
        page = mock.Mock()
        page.url = "https://www.instagram.com/accounts/login/"
        page.wait_for_timeout.return_value = None

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.looks_logged_out", side_effect=[RuntimeError("navigating"), False, False, False, False, False]), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.current_page_confirms_authenticated_shell", return_value=True), \
             mock.patch("instagram_followback_live.confirm_authenticated_session_in_fresh_page", return_value=True), \
             mock.patch("instagram_followback_live.time.monotonic", side_effect=[0, 0, 1, 2, 3, 4, 5]):
            wait_for_login_in_browser(page, login_timeout_ms=10000)

    def test_infer_username_from_candidates_returns_single_normalized_match(self):
        self.assertEqual(
            infer_username_from_candidates([" @Demo.User ", "https://www.instagram.com/demo.user/"]),
            "demo.user",
        )

    def test_infer_username_from_candidates_rejects_ambiguous_singletons(self):
        self.assertIsNone(infer_username_from_candidates(["alice", "bob"]))

    def test_infer_username_from_candidates_ignores_reserved_instagram_paths(self):
        self.assertIsNone(
            infer_username_from_candidates(["accounts", "explore", "reel", "tv"])
        )

    def test_infer_username_from_html_extracts_username_from_embedded_page_data(self):
        html = """
        <script type="application/json">
          {"viewer":{"username":"demo.user"},"user":{"username":"another_person"}}
        </script>
        """
        self.assertEqual(infer_username_from_html(html), "demo.user")

    def test_infer_username_from_html_ignores_accounts_path_from_edit_profile_links(self):
        html = """
        <a href="https://www.instagram.com/accounts/edit/">Edit profile</a>
        <a href="https://www.instagram.com/accounts/privacy_and_security/">Privacy</a>
        """
        self.assertIsNone(infer_username_from_html(html))

    def test_extract_profile_avatar_url_prefers_og_image(self):
        page = mock.Mock()
        page.evaluate.return_value = "https://cdn.example.com/avatar.jpg"
        self.assertEqual(extract_profile_avatar_url(page), "https://cdn.example.com/avatar.jpg")

    def test_extract_username_from_instagram_api_payload_reads_nested_form_data(self):
        payload = {"form_data": {"username": "Demo.User"}}
        self.assertEqual(extract_username_from_instagram_api_payload(payload), "demo.user")

    def test_fetch_logged_in_username_from_instagram_api_reads_page_payload(self):
        page = mock.Mock()
        page.evaluate.return_value = {"user": {"username": "demo.user"}}
        self.assertEqual(fetch_logged_in_username_from_instagram_api(page), "demo.user")

    def test_infer_username_from_profile_navigation_prefers_profile_link(self):
        page = mock.Mock()
        page.evaluate.return_value = [
            {"href": "/reels/", "priority": 0, "inNav": 1},
            {"href": "/demo.user/", "priority": 2, "inNav": 1},
            {"href": "/another.user/", "priority": 0, "inNav": 0},
        ]
        self.assertEqual(infer_username_from_profile_navigation(page), "demo.user")

    def test_has_authenticated_instagram_cookies_requires_sessionid_cookie(self):
        context = mock.Mock()
        context.cookies.return_value = [
            {"name": "sessionid"},
            {"name": "ds_user_id"},
        ]
        self.assertTrue(has_authenticated_instagram_cookies(context))

    def test_has_authenticated_instagram_cookies_returns_false_without_auth_cookie(self):
        context = mock.Mock()
        context.cookies.return_value = [{"name": "mid"}]
        self.assertFalse(has_authenticated_instagram_cookies(context))

    def test_session_has_authenticated_instagram_cookies_reads_chromium_cookie_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            cookie_db = session_dir / "Default" / "Cookies"
            cookie_db.parent.mkdir(parents=True)

            connection = sqlite3.connect(cookie_db)
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
                (0, '.instagram.com', '', 'ds_user_id', '', x'', '/', 0, 1, 0, 0, 1, 1, 1, 0, 0, 443, 0, 0, 0),
                (0, '.instagram.com', '', 'sessionid', '', x'', '/', 0, 1, 0, 0, 1, 1, 1, 0, 0, 443, 0, 0, 0)
                """
            )
            connection.commit()
            connection.close()

            self.assertTrue(session_has_authenticated_instagram_cookies(session_dir))

    def test_infer_username_from_current_page_reads_dom_without_waits(self):
        page = mock.Mock()
        page.locator.return_value.first.input_value.return_value = "Demo.User"

        self.assertEqual(infer_username_from_current_page(page), "demo.user")

    def test_looks_logged_out_handles_instagram_email_and_pass_fields(self):
        page = mock.Mock()
        page.url = "https://www.instagram.com/accounts/login/"
        self.assertTrue(looks_logged_out(page))

    def test_wait_for_confirmed_login_skips_edit_profile_when_username_is_known(self):
        page = mock.Mock()
        page.wait_for_timeout.return_value = None

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.fetch_logged_in_username_from_instagram_api", return_value=None), \
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.infer_username_from_current_page", return_value=None), \
             mock.patch("instagram_followback_live.time.monotonic", side_effect=[0, 0, 0]):
            resolved = wait_for_confirmed_login(
                page,
                "known_user",
                TimeoutError,
                login_timeout_ms=1000,
            )

        self.assertEqual(resolved, "known_user")
        page.goto.assert_not_called()

    def test_wait_for_confirmed_login_uses_current_page_before_edit_profile(self):
        page = mock.Mock()
        page.wait_for_timeout.return_value = None

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.infer_authenticated_username", return_value="page_user"), \
             mock.patch("instagram_followback_live.time.monotonic", side_effect=[0, 0]):
            resolved = wait_for_confirmed_login(
                page,
                None,
                TimeoutError,
                login_timeout_ms=1000,
            )

        self.assertEqual(resolved, "page_user")
        page.goto.assert_not_called()

    def test_wait_for_confirmed_login_allows_cookie_backed_login_without_username(self):
        page = mock.Mock()
        page.wait_for_timeout.return_value = None

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
             mock.patch("instagram_followback_live.has_authenticated_instagram_cookies", return_value=True), \
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.infer_authenticated_username", return_value=None), \
             mock.patch("instagram_followback_live.settle_instagram_account_page"), \
             mock.patch("instagram_followback_live.detect_logged_in_username", return_value=None), \
             mock.patch("instagram_followback_live.time.monotonic", side_effect=[0, 0, 0.4, 0.8, 1.2]):
            resolved = wait_for_confirmed_login(
                page,
                None,
                TimeoutError,
                login_timeout_ms=1000,
            )

        self.assertIsNone(resolved)

    def test_append_login_debug_writes_debug_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "login.log"
            append_login_debug(log_path, "hello")
            self.assertIn("hello", log_path.read_text(encoding="utf-8"))

    def test_save_and_clear_login_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "live-session"
            save_login_state(session_dir, "identity")
            self.assertEqual(load_login_state(session_dir).get("phase"), "identity")
            clear_login_state(session_dir)
            self.assertEqual(load_login_state(session_dir), {})

    def test_save_and_load_session_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            save_session_username(session_dir, "demo.user")

            self.assertEqual(load_session_username(session_dir), "demo.user")

    def test_save_session_profile_preserves_avatar_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            save_session_profile(
                session_dir,
                username="demo.user",
                avatar_data_url="data:image/png;base64,abc123",
            )
            save_session_username(session_dir, "demo.user")

            self.assertEqual(load_session_username(session_dir), "demo.user")
            self.assertEqual(load_session_avatar_data_url(session_dir), "data:image/png;base64,abc123")

    def test_resolve_requested_username_prefers_saved_session_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            save_session_username(session_dir, "saved_user")

            with mock.patch("instagram_followback_live.prompt_for_profile_username") as prompt:
                self.assertEqual(resolve_requested_username(None, session_dir), "saved_user")
                prompt.assert_not_called()

    def test_resolve_requested_username_prompts_when_browser_state_exists_but_username_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "Default").mkdir()

            with mock.patch("instagram_followback_live.sys.stdin.isatty", return_value=True):
                with mock.patch(
                    "instagram_followback_live.prompt_for_profile_username",
                    return_value="asked_user",
                ) as prompt:
                    self.assertEqual(resolve_requested_username(None, session_dir), "asked_user")
                    prompt.assert_called_once()

            self.assertTrue(session_has_browser_state(session_dir))

    def test_clear_live_session_removes_saved_browser_state_and_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "live-session"
            save_session_username(session_dir, "saved_user")
            (session_dir / "Default").mkdir(parents=True)
            (session_dir / "Default" / "cookies").write_text("demo", encoding="utf-8")

            clear_live_session(session_dir)

            self.assertFalse(session_dir.exists())
            self.assertIsNone(load_session_username(session_dir))
            self.assertFalse(session_has_browser_state(session_dir))


if __name__ == "__main__":
    unittest.main()
