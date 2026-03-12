import tempfile
import unittest
from pathlib import Path
from unittest import mock

from instagram_followback_live import (
    build_live_result,
    clear_live_session,
    extract_live_usernames,
    live_relation_url,
    load_session_username,
    normalize_profile_username,
    prompt_for_profile_username,
    resolve_requested_username,
    save_session_username,
    session_has_browser_state,
    text_suggests_login_required,
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

    def test_save_and_load_session_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            save_session_username(session_dir, "demo.user")

            self.assertEqual(load_session_username(session_dir), "demo.user")

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
