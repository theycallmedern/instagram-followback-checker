import tempfile
import unittest
from pathlib import Path
from unittest import mock

from instagram_followback_live import (
    build_live_result,
    clear_live_session,
    extract_live_usernames,
    extract_profile_avatar_url,
    infer_username_from_candidates,
    infer_username_from_current_page,
    infer_username_from_html,
    live_relation_url,
    load_session_avatar_data_url,
    load_session_username,
    normalize_profile_username,
    prompt_for_profile_username,
    save_session_profile,
    resolve_requested_username,
    save_session_username,
    session_has_browser_state,
    text_suggests_login_required,
    wait_for_confirmed_login,
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
        self.assertTrue(text_suggests_login_required("See Instagram photos and videos from your friends."))
        self.assertFalse(text_suggests_login_required("followers following posts"))

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

    def test_infer_username_from_current_page_reads_dom_without_waits(self):
        page = mock.Mock()
        page.locator.return_value.first.input_value.return_value = "Demo.User"

        self.assertEqual(infer_username_from_current_page(page), "demo.user")

    def test_wait_for_confirmed_login_skips_edit_profile_when_username_is_known(self):
        page = mock.Mock()
        page.wait_for_timeout.return_value = None

        with mock.patch("instagram_followback_live.dismiss_known_dialogs"), \
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
             mock.patch("instagram_followback_live.looks_logged_out", return_value=False), \
             mock.patch("instagram_followback_live.infer_username_from_current_page", return_value="page_user"), \
             mock.patch("instagram_followback_live.time.monotonic", side_effect=[0, 0]):
            resolved = wait_for_confirmed_login(
                page,
                None,
                TimeoutError,
                login_timeout_ms=1000,
            )

        self.assertEqual(resolved, "page_user")
        page.goto.assert_not_called()

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
