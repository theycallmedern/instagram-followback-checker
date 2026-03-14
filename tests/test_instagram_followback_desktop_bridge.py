import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from unittest import mock

from instagram_followback_checker import AnalysisResult
from instagram_followback_desktop_bridge import (
    clear_history_entries,
    build_history_detail_payload,
    build_history_payload,
    build_report_payload,
    build_report_payload_from_entry,
    export_history_entries,
    load_history_entries,
    run_login,
    save_history_snapshot,
    session_status_payload,
)
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
        self.assertEqual(payload["report_source"], "live")
        self.assertTrue(payload["snapshot_id"])
        self.assertEqual(payload["total_matches"], 2)
        self.assertEqual(payload["shown_matches"], 1)
        self.assertEqual(payload["entries"][0]["username"], "li")
        self.assertIn("amy", payload["followers_usernames"])
        self.assertIn("zoe", payload["following_usernames"])

    def test_build_history_payload_includes_latest_snapshot_and_changes(self):
        first_result = AnalysisResult(
            followers={"amy", "mila"},
            following={"amy", "zoe"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )
        second_result = AnalysisResult(
            followers={"amy", "li"},
            following={"amy", "zoe", "nova"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=first_result,
            )
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-two",
                scan_username="demo_user",
                created_at="2026-03-14T10:00:00Z",
                result=second_result,
            )

            payload = build_history_payload(session_dir, username="demo_user")

        self.assertEqual(payload["username"], "demo_user")
        self.assertEqual(len(payload["entries"]), 2)
        self.assertEqual(payload["entries"][0]["snapshot_id"], "snapshot-two")
        self.assertEqual(payload["entries"][1]["snapshot_id"], "snapshot-one")
        self.assertEqual(payload["changes"]["new_nonfollowers"], ["nova"])
        self.assertEqual(payload["changes"]["returned_mutuals"], [])
        self.assertEqual(payload["changes"]["disappeared_fans"], ["mila"])

    def test_build_history_detail_payload_returns_snapshot_lists_and_previous_compare(self):
        first_result = AnalysisResult(
            followers={"amy", "mila"},
            following={"amy", "zoe"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )
        second_result = AnalysisResult(
            followers={"amy", "li"},
            following={"amy", "zoe", "nova"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=first_result,
            )
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-two",
                scan_username="demo_user",
                created_at="2026-03-14T10:00:00Z",
                result=second_result,
            )

            payload = build_history_detail_payload(
                session_dir,
                username="demo_user",
                snapshot_id="snapshot-two",
            )

        self.assertEqual(payload["snapshot"]["snapshot_id"], "snapshot-two")
        self.assertEqual(payload["previous_snapshot"]["snapshot_id"], "snapshot-one")
        self.assertEqual(payload["snapshot"]["mode_lists"]["nonfollowers"], ["nova", "zoe"])
        self.assertEqual(payload["snapshot"]["mode_lists"]["fans"], ["li"])
        self.assertEqual(payload["snapshot"]["mode_lists"]["mutuals"], ["amy"])
        self.assertEqual(payload["changes"]["new_nonfollowers"], ["nova"])
        self.assertEqual(payload["changes"]["disappeared_fans"], ["mila"])

    def test_build_history_detail_payload_supports_custom_comparison_snapshot(self):
        first_result = AnalysisResult(
            followers={"amy", "mila"},
            following={"amy", "zoe"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )
        second_result = AnalysisResult(
            followers={"amy", "mila", "li"},
            following={"amy", "zoe", "nova"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )
        third_result = AnalysisResult(
            followers={"amy", "li"},
            following={"amy", "li", "zoe", "nova", "ivy"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-12T10:00:00Z",
                result=first_result,
            )
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-two",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=second_result,
            )
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-three",
                scan_username="demo_user",
                created_at="2026-03-14T10:00:00Z",
                result=third_result,
            )

            payload = build_history_detail_payload(
                session_dir,
                username="demo_user",
                snapshot_id="snapshot-three",
                compare_snapshot_id="snapshot-one",
            )

        self.assertEqual(payload["comparison_mode"], "custom")
        self.assertEqual(payload["snapshot"]["snapshot_id"], "snapshot-three")
        self.assertEqual(payload["comparison_snapshot"]["snapshot_id"], "snapshot-one")
        self.assertEqual(payload["changes"]["new_nonfollowers"], ["ivy", "nova"])
        self.assertEqual(payload["changes"]["returned_mutuals"], ["li"])
        self.assertEqual(payload["changes"]["disappeared_fans"], ["mila"])
        self.assertEqual(len(payload["available_comparisons"]), 2)

    def test_clear_history_entries_only_removes_selected_account(self):
        result = AnalysisResult(
            followers={"amy"},
            following={"amy", "zoe"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=result,
            )
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-two",
                scan_username="other_user",
                created_at="2026-03-14T10:00:00Z",
                result=result,
            )

            removed = clear_history_entries(session_dir, username="demo_user")
            remaining_demo = build_history_payload(session_dir, username="demo_user")
            remaining_other = build_history_payload(session_dir, username="other_user")

        self.assertEqual(removed, 1)
        self.assertEqual(remaining_demo["entries"], [])
        self.assertEqual(len(remaining_other["entries"]), 1)
        self.assertEqual(remaining_other["entries"][0]["snapshot_id"], "snapshot-two")

    def test_export_history_entries_writes_json_and_csv(self):
        result = AnalysisResult(
            followers={"amy"},
            following={"amy", "zoe"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=result,
            )

            json_path = Path(temp_dir) / "history.json"
            csv_path = Path(temp_dir) / "history.csv"
            json_payload = export_history_entries(
                session_dir,
                username="demo_user",
                export_format="json",
                output_path=json_path,
            )
            csv_payload = export_history_entries(
                session_dir,
                username="demo_user",
                export_format="csv",
                output_path=csv_path,
            )

            json_text = json_path.read_text(encoding="utf-8")
            csv_text = csv_path.read_text(encoding="utf-8")

        self.assertEqual(json_payload["exported_entries"], 1)
        self.assertIn('"snapshot_id": "snapshot-one"', json_text)
        self.assertIn('"nonfollowers": [', json_text)
        self.assertEqual(csv_payload["exported_entries"], 1)
        self.assertIn("snapshot_id,created_at,followers,following,nonfollowers,fans,mutuals,warning_count", csv_text)
        self.assertIn("snapshot-one,2026-03-13T10:00:00Z,1,2,1,0,1,0", csv_text)

    def test_build_report_payload_from_entry_restores_saved_snapshot_stats(self):
        result = AnalysisResult(
            followers={"amy", "mila"},
            following={"amy", "zoe", "li"},
            follower_files=["live-instagram://demo/followers"],
            following_files=["live-instagram://demo/following"],
            follower_timestamps=[],
            following_timestamps=[],
        )

        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / "live-session"
            save_history_snapshot(
                session_dir,
                snapshot_id="snapshot-one",
                scan_username="demo_user",
                created_at="2026-03-13T10:00:00Z",
                result=result,
            )
            entry = load_history_entries(session_dir, username="demo_user", limit=1)[0]
            payload = build_report_payload_from_entry(
                entry=entry,
                mode="nonfollowers",
                sort_mode="alpha",
                limit=None,
                stats_only=False,
            )

        self.assertEqual(payload["snapshot_id"], "snapshot-one")
        self.assertEqual(payload["scan_username"], "demo_user")
        self.assertEqual(payload["report_source"], "history")
        self.assertEqual(payload["stats"]["followers"], 2)
        self.assertEqual(payload["stats"]["following"], 3)
        self.assertEqual(payload["stats"]["nonfollowers"], 2)
        self.assertEqual(payload["entries"][0]["username"], "li")
        self.assertEqual(payload["entries"][1]["username"], "zoe")

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
