import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from instagram_nonfollowers import analyze_export


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class InstagramNonFollowersTests(unittest.TestCase):
    def test_directory_export_with_standard_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "followers_and_following" / "followers_1.json",
                [
                    {"string_list_data": [{"value": "alice"}]},
                    {"string_list_data": [{"value": "bob"}]},
                ],
            )
            write_json(
                root / "followers_and_following" / "following.json",
                {
                    "relationships_following": [
                        {"string_list_data": [{"value": "alice"}]},
                        {"string_list_data": [{"value": "carol"}]},
                    ]
                },
            )

            result = analyze_export(root)

            self.assertEqual(result.not_following_back(), ["carol"])
            self.assertEqual(sorted(result.follower_files), ["followers_and_following/followers_1.json"])
            self.assertEqual(sorted(result.following_files), ["followers_and_following/following.json"])

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

    def test_zip_export_and_cli_outputs(self):
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
                    "connections/followers_and_following/following.json",
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

            csv_path = root / "result.csv"
            json_path = root / "result.json"
            script_path = Path(__file__).resolve().parents[1] / "instagram_nonfollowers.py"

            completed = subprocess.run(
                [
                    "python3",
                    str(script_path),
                    str(archive),
                    "--csv",
                    str(csv_path),
                    "--json",
                    str(json_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Не подписаны в ответ: 1", completed.stdout)
            self.assertIn("carol", completed.stdout)
            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())

            csv_contents = csv_path.read_text(encoding="utf-8")
            self.assertIn("username", csv_contents)
            self.assertIn("carol", csv_contents)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["not_following_back"], ["carol"])


if __name__ == "__main__":
    unittest.main()
