import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from instagram_followback_checker import HtmlExportError, analyze_export
from instagram_followback_live import build_live_result
from instagram_followback_web import (
    HistoryEntry,
    build_history_changes,
    create_report_bundle,
    create_report_bundle_from_result,
    render_page,
)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def create_basic_export(root: Path) -> Path:
    export_root = root / "export"
    write_json(
        export_root / "followers_and_following" / "followers_1.json",
        [
            {"string_list_data": [{"value": "alice"}]},
            {"string_list_data": [{"value": "bob"}]},
            {"string_list_data": [{"value": "dave"}]},
        ],
    )
    write_json(
        export_root / "followers_and_following" / "following.json",
        {
            "relationships_following": [
                {"string_list_data": [{"value": "alice"}]},
                {"string_list_data": [{"value": "carol"}]},
                {"string_list_data": [{"value": "dave"}]},
                {"string_list_data": [{"value": "eve"}]},
            ]
        },
    )
    return export_root


def create_limited_range_export(root: Path) -> Path:
    export_root = root / "export"
    write_json(
        export_root / "followers_and_following" / "followers_1.json",
        [
            {"string_list_data": [{"value": "recent_follower", "timestamp": 1743260024}]},
        ],
    )
    write_json(
        export_root / "followers_and_following" / "following.json",
        {
            "relationships_following": [
                {"string_list_data": [{"value": "recent_follower", "timestamp": 1743260024}]},
                {"string_list_data": [{"value": "old_account", "timestamp": 1557080370}]},
            ]
        },
    )
    return export_root


class InstagramFollowbackCheckerTests(unittest.TestCase):
    def test_directory_export_with_standard_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_basic_export(Path(tmp))
            result = analyze_export(root)

            self.assertEqual(result.not_following_back(), ["carol", "eve"])
            self.assertEqual(result.fans(), ["bob"])
            self.assertEqual(result.mutuals(), ["alice", "dave"])

    def test_directory_export_with_content_based_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "custom" / "part_a.json",
                {
                    "relationships_followers": [
                        {"string_list_data": [{"value": "alice"}]},
                        {"string_list_data": [{"value": "bob"}]},
                    ]
                },
            )
            write_json(
                root / "custom" / "part_b.json",
                {
                    "relationships_following": [
                        {"string_list_data": [{"value": "alice"}]},
                        {"string_list_data": [{"value": "eve"}]},
                    ]
                },
            )

            result = analyze_export(root)

            self.assertEqual(result.not_following_back(), ["eve"])
            self.assertEqual(sorted(result.follower_files), ["custom/part_a.json"])
            self.assertEqual(sorted(result.following_files), ["custom/part_b.json"])

    def test_html_export_error_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_export = Path(tmp) / "instagram-export.html"
            html_export.write_text("<html></html>", encoding="utf-8")

            with self.assertRaises(HtmlExportError):
                analyze_export(html_export)

    def test_invalid_zip_error_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_archive = Path(tmp) / "broken.zip"
            bad_archive.write_text("not a zip", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Invalid ZIP archive"):
                analyze_export(bad_archive)

    def test_cli_modes_sort_limit_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_basic_export(Path(tmp))
            csv_path = Path(tmp) / "fans.csv"
            txt_path = Path(tmp) / "fans.txt"
            json_path = Path(tmp) / "fans.json"
            script_path = Path(__file__).resolve().parents[1] / "instagram_followback_checker.py"

            completed = subprocess.run(
                [
                    "python3",
                    str(script_path),
                    str(root),
                    "--fans",
                    "--sort",
                    "length",
                    "--limit",
                    "1",
                    "--csv",
                    str(csv_path),
                    "--txt",
                    str(txt_path),
                    "--json",
                    str(json_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Fans: 1", completed.stdout)
            self.assertIn("Mode: Accounts that follow you, but you do not follow back", completed.stdout)
            self.assertIn("bob", completed.stdout)
            self.assertTrue(csv_path.exists())
            self.assertTrue(txt_path.exists())
            self.assertTrue(json_path.exists())

            csv_contents = csv_path.read_text(encoding="utf-8")
            self.assertIn("username,profile_url", csv_contents)
            self.assertIn("bob,https://www.instagram.com/bob/", csv_contents)

            txt_contents = txt_path.read_text(encoding="utf-8")
            self.assertEqual(txt_contents.strip(), "bob")

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "fans")
            self.assertEqual(payload["entries"][0]["username"], "bob")

    def test_cli_stats_only_and_legacy_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_basic_export(Path(tmp))
            wrapper_path = Path(__file__).resolve().parents[1] / "instagram_nonfollowers.py"

            completed = subprocess.run(
                [
                    "python3",
                    str(wrapper_path),
                    str(root),
                    "--stats-only",
                    "--mutuals",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Mutuals: 2", completed.stdout)
            self.assertNotIn("Usernames:", completed.stdout)

    def test_zip_export_supports_split_relation_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "instagram-export.zip"

            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr(
                    "connections/followers_and_following/followers_1.json",
                    json.dumps(
                        [
                            {"string_list_data": [{"value": "@Alice"}]},
                            {
                                "string_list_data": [
                                    {"href": "https://www.instagram.com/bob/"}
                                ]
                            },
                        ]
                    ),
                )
                handle.writestr(
                    "connections/followers_and_following/followers_2.json",
                    json.dumps([{"string_list_data": [{"value": "dave"}]}]),
                )
                handle.writestr(
                    "connections/followers_and_following/following_1.json",
                    json.dumps(
                        {
                            "relationships_following": [
                                {"string_list_data": [{"value": "alice"}]},
                                {"string_list_data": [{"value": "carol"}]},
                                {"string_list_data": [{"value": "dave"}]},
                            ]
                        }
                    ),
                )

            result = analyze_export(archive)

            self.assertEqual(result.not_following_back(), ["carol"])

    def test_ignores_service_u_paths_and_extracts_wrapped_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "followers_and_following" / "followers_1.json",
                [
                    {"string_list_data": [{"value": "alice"}]},
                ],
            )
            write_json(
                root / "followers_and_following" / "following.json",
                {
                    "relationships_following": [
                        {"string_list_data": [{"href": "https://www.instagram.com/_u/"}]},
                        {"string_list_data": [{"href": "https://www.instagram.com/_u/carol/"}]},
                        {"string_list_data": [{"value": "alice"}]},
                    ]
                },
            )

            result = analyze_export(root)

            self.assertEqual(result.not_following_back(), ["carol"])

    def test_does_not_mix_threads_followers_and_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "connections" / "followers_and_following" / "followers_1.json",
                [
                    {"string_list_data": [{"value": "alice"}]},
                ],
            )
            write_json(
                root / "connections" / "followers_and_following" / "following.json",
                {
                    "relationships_following": [
                        {"string_list_data": [{"value": "alice"}]},
                        {"string_list_data": [{"value": "carol"}]},
                    ]
                },
            )
            write_json(
                root / "your_instagram_activity" / "threads" / "followers.json",
                {
                    "text_post_app_text_post_app_followers": [
                        {"string_list_data": [{"value": "threads_only_1"}]},
                        {"string_list_data": [{"value": "threads_only_2"}]},
                    ]
                },
            )
            write_json(
                root / "your_instagram_activity" / "threads" / "following.json",
                {
                    "text_post_app_text_post_app_following": [
                        {"string_list_data": [{"value": "threads_only_3"}]},
                    ]
                },
            )

            result = analyze_export(root)

            self.assertEqual(result.stats()["followers"], 1)
            self.assertEqual(result.stats()["following"], 2)
            self.assertEqual(result.not_following_back(), ["carol"])

    def test_warns_when_relation_time_ranges_do_not_line_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_limited_range_export(Path(tmp))
            result = analyze_export(root)

            warnings = result.warnings()

            self.assertEqual(result.relation_time_ranges()["followers"]["start_date"], "2025-03-29")
            self.assertEqual(result.relation_time_ranges()["following"]["start_date"], "2019-05-05")
            self.assertEqual(len(warnings), 1)
            self.assertIn("limited date range", warnings[0])

    def test_web_bundle_contains_entries_and_download_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_basic_export(Path(tmp))
            bundle = create_report_bundle(
                export_source=root,
                source_label=str(root),
                mode="mutuals",
                sort_mode="alpha",
                limit=1,
                stats_only=False,
            )

            self.assertEqual(bundle.mode, "mutuals")
            self.assertEqual(bundle.total_matches, 2)
            self.assertEqual(bundle.shown_matches, 1)
            self.assertEqual(bundle.entries[0]["username"], "alice")
            self.assertIn(b"username,profile_url", bundle.csv_bytes)
            self.assertIn(b"https://www.instagram.com/alice/", bundle.csv_bytes)

    def test_web_bundle_can_wrap_live_results(self):
        result = build_live_result("demo_user", {"alice", "bob"}, {"alice", "carol"})
        bundle = create_report_bundle_from_result(
            result=result,
            source_label="live Instagram session (@demo_user)",
            mode="nonfollowers",
            sort_mode="alpha",
            limit=None,
            stats_only=False,
        )

        self.assertEqual(bundle.source_label, "live Instagram session (@demo_user)")
        self.assertEqual(bundle.entries[0]["username"], "carol")
        self.assertIn("live-instagram://demo_user/followers", bundle.json_bytes.decode("utf-8"))

    def test_web_bundle_can_hide_ignored_usernames(self):
        result = build_live_result("demo_user", {"alice", "bob"}, {"alice", "carol", "dave"})
        bundle = create_report_bundle_from_result(
            result=result,
            source_label="live Instagram session (@demo_user)",
            mode="nonfollowers",
            sort_mode="alpha",
            limit=None,
            stats_only=False,
            ignored_usernames={"carol"},
        )

        self.assertEqual(bundle.total_matches, 1)
        self.assertEqual(bundle.ignored_matches, 1)
        self.assertEqual(bundle.entries[0]["username"], "dave")
        self.assertIn('"ignored_usernames": [\n    "carol"\n  ]', bundle.json_bytes.decode("utf-8"))

    def test_history_changes_detect_expected_deltas(self):
        current = build_live_result("demo_user", {"alice", "erin"}, {"alice", "carol", "dave", "erin"})
        previous = HistoryEntry(
            snapshot_id="snapshot-1",
            username="demo_user",
            created_at="2026-03-12T12:00:00Z",
            followers={"alice", "bob", "erin"},
            following={"alice", "carol"},
            stats={
                "followers": 3,
                "following": 2,
                "nonfollowers": 1,
                "fans": 2,
                "mutuals": 1,
            },
        )

        changes = build_history_changes(current, previous)

        self.assertEqual(changes["new_nonfollowers"], ["dave"])
        self.assertEqual(changes["returned_mutuals"], ["erin"])
        self.assertEqual(changes["disappeared_fans"], ["bob", "erin"])

    def test_web_page_renders_results_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_basic_export(Path(tmp))
            bundle = create_report_bundle(
                export_source=root,
                source_label="demo-export.zip",
                mode="fans",
                sort_mode="alpha",
                limit=None,
                stats_only=False,
            )

            page = render_page(
                form_values={
                    "local_path": "",
                    "mode": "fans",
                    "sort": "alpha",
                    "limit": "",
                },
                bundle=bundle,
                report_token=bundle.token,
                ignored_usernames=["brand_noise"],
                inspect_username="alice",
                inspect_result={
                    "username": "alice",
                    "in_followers": True,
                    "in_following": True,
                    "relationship": "mutual",
                    "ignored": False,
                },
                history_entries=[
                    HistoryEntry(
                        snapshot_id="older-scan",
                        username="demo_user",
                        created_at="2026-03-12T12:00:00Z",
                        followers={"alice"},
                        following={"alice", "carol"},
                        stats={
                            "followers": 1,
                            "following": 2,
                            "nonfollowers": 1,
                            "fans": 0,
                            "mutuals": 1,
                        },
                    )
                ],
                history_changes={
                    "new_nonfollowers": ["carol"],
                    "returned_mutuals": ["alice"],
                    "disappeared_fans": [],
                },
                show_files=True,
            )

            self.assertIn("Instagram Live Followback", page)
            self.assertIn("Analysis complete", page)
            self.assertIn("demo-export.zip", page)
            self.assertIn("/download/", page)
            self.assertIn("Search by username", page)
            self.assertIn("Check username", page)
            self.assertIn('name="inspect_username" value=""', page)
            self.assertIn("Last checked: @alice", page)
            self.assertIn("Advanced tools", page)
            self.assertIn("Saved scan history", page)
            self.assertIn("brand_noise", page)

    def test_web_page_renders_live_controls(self):
        page = render_page(
            live_form_values={
                "instagram_username": "demo_user",
                "mode": "fans",
                "sort": "length",
                "limit": "25",
                "stats_only": "on",
            },
            live_session_username="demo_user",
            live_session_ready=True,
        )

        self.assertIn("Instagram Live Followback", page)
        self.assertIn("/live-login", page)
        self.assertIn("/live-disconnect", page)
        self.assertIn("/live-analyze", page)
        self.assertIn("Connected", page)
        self.assertIn("Disconnect", page)
        self.assertIn('disabled aria-disabled="true"', page)
        self.assertIn("Run scan", page)
        self.assertIn("@demo_user", page)
        self.assertIn("Saved Instagram session is ready on this Mac.", page)
        self.assertIn("Simple control panel", page)
        self.assertIn("Progress", page)
        self.assertNotIn("Upload export ZIP", page)
        self.assertNotIn("Analyze Export", page)
        self.assertIn('placeholder="Leave empty if the saved session is already yours"', page)
        self.assertNotIn("Hide low-value accounts", page)

    def test_web_page_renders_date_range_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = create_limited_range_export(Path(tmp))
            bundle = create_report_bundle(
                export_source=root,
                source_label="limited-export.zip",
                mode="nonfollowers",
                sort_mode="alpha",
                limit=None,
                stats_only=False,
            )

            page = render_page(bundle=bundle)

            self.assertIn("limited date range", page)
            self.assertIn("2019-05-05", page)
            self.assertIn("2025-03-29", page)


if __name__ == "__main__":
    unittest.main()
