import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from unittest import mock

from instagram_followback_checker import AnalysisResult
from instagram_followback_desktop_bridge import build_report_payload, run_login, session_status_payload
from instagram_followback_live import save_login_state, save_session_profile, save_session_username


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

            self.assertFalse(payload["connected"])
            self.assertTrue(payload["browser_state_present"])
            self.assertFalse(payload["authenticated_cookie_present"])
            self.assertIsNone(payload["username"])

    def test_session_status_payload_includes_saved_avatar(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
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
            save_session_profile(
                session_dir,
                username="demo_user",
                avatar_data_url="data:image/png;base64,abc123",
            )

            payload = session_status_payload(session_dir)

            self.assertEqual(payload["username"], "demo_user")
            self.assertEqual(payload["avatar_data_url"], "data:image/png;base64,abc123")
            self.assertTrue(payload["connected"])
            self.assertTrue(payload["authenticated_cookie_present"])

    def test_session_status_payload_treats_authenticated_cookies_as_connected(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
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

            payload = session_status_payload(session_dir)

            self.assertTrue(payload["connected"])
            self.assertTrue(payload["authenticated_cookie_present"])
            self.assertIsNone(payload["username"])

    def test_session_status_payload_reports_login_in_progress(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_login_state(session_dir, "identity")

            payload = session_status_payload(session_dir)

            self.assertTrue(payload["login_in_progress"])
            self.assertEqual(payload["login_phase"], "identity")

    def test_run_login_clears_saved_session_before_launching_new_login(self):
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_session_username(session_dir, "saved_user")
            (session_dir / "Default").mkdir(parents=True)

            args = Namespace(
                session_dir=str(session_dir),
                username=None,
                login_timeout_ms=1234,
                verbose=False,
            )

            with mock.patch("instagram_followback_desktop_bridge.login_only", return_value=None):
                exit_code = run_login(args)

            self.assertEqual(exit_code, 0)
            self.assertFalse((session_dir / "ig_followback_live_session.json").exists())


if __name__ == "__main__":
    unittest.main()
