import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from instagram_followback_checker import HtmlExportError, analyze_export


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


if __name__ == "__main__":
    unittest.main()
