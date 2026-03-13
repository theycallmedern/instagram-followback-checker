import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from instagram_followback_checker import AnalysisResult
from instagram_followback_desktop_bridge import build_report_payload, session_status_payload
from instagram_followback_live import save_session_profile


class InstagramFollowbackDesktopBridgeTests(unittest.TestCase):
    def test_build_report_payload_respects_mode_sort_and_limit(self):
        result = AnalysisResult(
            followers={"amy", "zeta", "mila"},
            following={"amy", "zoe", "li"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        payload = build_report_payload(
            scan_username="demo_user",
            result=result,
            mode="nonfollowers",
            sort_mode="length",
            limit=1,
            stats_only=False,
        )

        self.assertEqual(payload["scan_username"], "demo_user")
        self.assertEqual(payload["mode"], "nonfollowers")
        self.assertEqual(payload["total_matches"], 2)
        self.assertEqual(payload["shown_matches"], 1)
        self.assertEqual(payload["entries"][0]["username"], "li")
        self.assertIn("amy", payload["followers_usernames"])
        self.assertIn("zoe", payload["following_usernames"])

    def test_session_status_payload_without_session(self):
        with TemporaryDirectory() as temp_dir:
            payload = session_status_payload(Path(temp_dir) / "live-session")
            self.assertFalse(payload["connected"])
            self.assertIsNone(payload["username"])

    def test_session_status_payload_treats_saved_browser_state_as_connected(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            (session_dir / "Default").mkdir(parents=True)

            payload = session_status_payload(session_dir)

            self.assertTrue(payload["connected"])
            self.assertTrue(payload["browser_state_present"])
            self.assertIsNone(payload["username"])

    def test_session_status_payload_includes_saved_avatar(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            (session_dir / "Default").mkdir(parents=True)
            save_session_profile(
                session_dir,
                username="demo_user",
                avatar_data_url="data:image/png;base64,abc123",
            )

            payload = session_status_payload(session_dir)

            self.assertEqual(payload["username"], "demo_user")
            self.assertEqual(payload["avatar_data_url"], "data:image/png;base64,abc123")


if __name__ == "__main__":
    unittest.main()
