#!/usr/bin/env python3
"""Analyze followback relationships from an official Instagram JSON export."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, List, Optional, Sequence, Set
from urllib.parse import urlparse

VERSION = "0.2.0"
USERNAME_RE = re.compile(r"[a-z0-9._]{1,30}")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
RESERVED_PATH_SEGMENTS = {"p", "reel", "stories", "tv", "explore", "accounts"}
RELATION_KEYS = {
    "followers": "relationships_followers",
    "following": "relationships_following",
}
MODE_LABELS = {
    "nonfollowers": "Accounts you follow that do not follow you back",
    "fans": "Accounts that follow you, but you do not follow back",
    "mutuals": "Mutual follow relationships",
}
DATE_RANGE_WARNING_DAYS = 180


class ExportError(RuntimeError):
    """Raised when the export cannot be parsed."""


class HtmlExportError(ExportError):
    """Raised when the user provides an HTML export instead of JSON."""


@dataclass
class AnalysisResult:
    followers: Set[str]
    following: Set[str]
    follower_files: List[str]
    following_files: List[str]
    follower_timestamps: List[int]
    following_timestamps: List[int]

    def not_following_back(self) -> List[str]:
        return sorted(self.following - self.followers)

    def fans(self) -> List[str]:
        return sorted(self.followers - self.following)

    def mutuals(self) -> List[str]:
        return sorted(self.followers & self.following)

    def usernames_for_mode(self, mode: str) -> List[str]:
        if mode == "nonfollowers":
            return self.not_following_back()
        if mode == "fans":
            return self.fans()
        if mode == "mutuals":
            return self.mutuals()
        raise ValueError(f"Unsupported mode: {mode}")

    def stats(self) -> dict[str, int]:
        return {
            "followers": len(self.followers),
            "following": len(self.following),
            "nonfollowers": len(self.following - self.followers),
            "fans": len(self.followers - self.following),
            "mutuals": len(self.followers & self.following),
        }

    def relation_time_ranges(self) -> dict[str, dict[str, str] | None]:
        return {
            "followers": summarize_timestamps(self.follower_timestamps),
            "following": summarize_timestamps(self.following_timestamps),
        }

    def warnings(self) -> List[str]:
        warnings: List[str] = []
        time_ranges = self.relation_time_ranges()
        follower_range = time_ranges["followers"]
        following_range = time_ranges["following"]

        if follower_range and following_range:
            start_gap_days = (
                int(follower_range["start_timestamp"]) - int(following_range["start_timestamp"])
            ) / 86400
            if start_gap_days >= DATE_RANGE_WARNING_DAYS:
                warnings.append(
                    "Followers data starts much later than following data "
                    f"({follower_range['start_date']} vs {following_range['start_date']}). "
                    "This export may have been requested with a limited date range, so older "
                    "followers can be missing from the report."
                )

        return warnings

    def inspect_username(self, username: str) -> dict[str, bool | str]:
        normalized = normalize_username(username) or username.strip().lower()
        in_followers = normalized in self.followers
        in_following = normalized in self.following

        if in_followers and in_following:
            relationship = "mutual"
        elif in_following:
            relationship = "not_following_back"
        elif in_followers:
            relationship = "fan"
        else:
            relationship = "not_found"

        return {
            "username": normalized,
            "in_followers": in_followers,
            "in_following": in_following,
            "relationship": relationship,
        }


class JsonSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.is_zip = path.is_file() and path.suffix.lower() == ".zip"
        self._zip_file: Optional[zipfile.ZipFile] = None

    def __enter__(self) -> "JsonSource":
        if self.is_zip:
            try:
                self._zip_file = zipfile.ZipFile(self.path)
            except zipfile.BadZipFile as exc:
                raise ExportError(
                    f"Invalid ZIP archive: {self.path}. Download the export again and try once more."
                ) from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._zip_file is not None:
            self._zip_file.close()
            self._zip_file = None

    def iter_json_names(self) -> List[str]:
        if self.is_zip:
            assert self._zip_file is not None
            return sorted(
                name
                for name in self._zip_file.namelist()
                if name.lower().endswith(".json") and not name.endswith("/")
            )

        return sorted(
            file.relative_to(self.path).as_posix()
            for file in self.path.rglob("*.json")
            if file.is_file()
        )

    def iter_html_names(self) -> List[str]:
        if self.is_zip:
            assert self._zip_file is not None
            return sorted(
                name
                for name in self._zip_file.namelist()
                if name.lower().endswith((".html", ".htm")) and not name.endswith("/")
            )

        return sorted(
            file.relative_to(self.path).as_posix()
            for file in self.path.rglob("*")
            if file.is_file() and file.suffix.lower() in {".html", ".htm"}
        )

    def read_json(self, name: str) -> Any:
        try:
            if self.is_zip:
                assert self._zip_file is not None
                raw = self._zip_file.read(name)
                return json.loads(raw.decode("utf-8"))

            file_path = self.path / Path(name)
            return json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExportError(f"Invalid JSON file in export: {name}") from exc


def profile_url_for(username: str) -> str:
    return f"https://www.instagram.com/{username}/"


def summarize_timestamps(timestamps: Sequence[int]) -> Optional[dict[str, str]]:
    if not timestamps:
        return None

    start_timestamp = min(timestamps)
    end_timestamp = max(timestamps)
    return {
        "start_timestamp": str(start_timestamp),
        "end_timestamp": str(end_timestamp),
        "start_date": datetime.fromtimestamp(start_timestamp, tz=timezone.utc).date().isoformat(),
        "end_date": datetime.fromtimestamp(end_timestamp, tz=timezone.utc).date().isoformat(),
    }


def normalize_username(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None

    value = raw.strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.lower()
        if host not in INSTAGRAM_HOSTS:
            return None
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            return None
        # Instagram exports can contain service links such as "/_u/<username>/".
        # "_u" is not a real username and should be unwrapped or ignored.
        if segments[0] == "_u":
            if len(segments) < 2:
                return None
            value = segments[1]
        elif segments[0] in RESERVED_PATH_SEGMENTS:
            return None
        else:
            value = segments[0]

    value = value.lstrip("@").strip().strip("/").lower()
    if USERNAME_RE.fullmatch(value):
        return value
    return None


def collect_relation_data(node: Any) -> tuple[Set[str], List[int]]:
    usernames: Set[str] = set()
    timestamps: List[int] = []

    if isinstance(node, list):
        for item in node:
            child_usernames, child_timestamps = collect_relation_data(item)
            usernames.update(child_usernames)
            timestamps.extend(child_timestamps)
        return usernames, timestamps

    if not isinstance(node, dict):
        return usernames, timestamps

    for key in ("value", "href", "username"):
        username = normalize_username(node.get(key))
        if username is not None:
            usernames.add(username)

    timestamp = node.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        timestamps.append(int(timestamp))

    for value in node.values():
        if isinstance(value, (dict, list)):
            child_usernames, child_timestamps = collect_relation_data(value)
            usernames.update(child_usernames)
            timestamps.extend(child_timestamps)

    return usernames, timestamps


def relation_payload(payload: Any, relation: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(RELATION_KEYS[relation], payload)
    return payload


def path_relation_hint(name: str) -> Optional[str]:
    pure_path = PurePosixPath(name)
    basename = pure_path.name.lower()
    path_parts = {part.lower() for part in pure_path.parts}

    # Only accept relationship files from the Instagram export section that
    # stores followers/following data. This avoids mixing in Threads or
    # unrelated activity files that happen to reuse generic file names.
    if "followers_and_following" not in path_parts or "threads" in path_parts:
        return None

    if re.fullmatch(r"(?:followers|relationships_followers)(?:_\d+)?\.json", basename):
        return "followers"
    if re.fullmatch(r"(?:following|followings|relationships_following)(?:_\d+)?\.json", basename):
        return "following"
    return None


def payload_relation_hints(payload: Any) -> List[str]:
    if not isinstance(payload, dict):
        return []

    hints: List[str] = []
    for relation, key in RELATION_KEYS.items():
        if key in payload:
            hints.append(relation)
    return hints


def load_relation_documents(
    source: JsonSource, names: Iterable[str], relation: str
) -> tuple[Set[str], List[str], List[int]]:
    usernames: Set[str] = set()
    used_files: List[str] = []
    timestamps: List[int] = []

    for name in names:
        payload = source.read_json(name)
        extracted, extracted_timestamps = collect_relation_data(relation_payload(payload, relation))
        if extracted:
            usernames.update(extracted)
            used_files.append(name)
            timestamps.extend(extracted_timestamps)

    return usernames, used_files, timestamps


def analyze_export(path: Path) -> AnalysisResult:
    if not path.exists():
        raise ExportError(f"Path not found: {path}")

    if path.is_file() and path.suffix.lower() in {".html", ".htm"}:
        raise HtmlExportError(
            "This looks like an HTML export. Request the Instagram export again in JSON format."
        )

    if not path.is_dir() and not (path.is_file() and path.suffix.lower() == ".zip"):
        raise ExportError("Provide either an extracted export folder or a .zip archive.")

    with JsonSource(path) as source:
        json_names = source.iter_json_names()
        html_names = source.iter_html_names()

        if not json_names:
            if html_names:
                raise HtmlExportError(
                    "No JSON files were found. This export looks like HTML. Request the export in JSON format."
                )
            raise ExportError("No JSON files were found in the export.")

        follower_candidates = [name for name in json_names if path_relation_hint(name) == "followers"]
        following_candidates = [name for name in json_names if path_relation_hint(name) == "following"]

        followers, follower_files, follower_timestamps = load_relation_documents(
            source, follower_candidates, "followers"
        )
        following, following_files, following_timestamps = load_relation_documents(
            source, following_candidates, "following"
        )

        inspected = set(follower_candidates + following_candidates)
        need_deep_scan = not followers or not following

        if need_deep_scan:
            for name in json_names:
                if name in inspected:
                    continue
                payload = source.read_json(name)
                for relation in payload_relation_hints(payload):
                    extracted, extracted_timestamps = collect_relation_data(
                        relation_payload(payload, relation)
                    )
                    if not extracted:
                        continue
                    if relation == "followers":
                        followers.update(extracted)
                        follower_files.append(name)
                        follower_timestamps.extend(extracted_timestamps)
                    else:
                        following.update(extracted)
                        following_files.append(name)
                        following_timestamps.extend(extracted_timestamps)

    if not followers:
        raise ExportError(
            "Could not find followers data in the export. Make sure you downloaded the JSON version."
        )
    if not following:
        raise ExportError(
            "Could not find following data in the export. Make sure you downloaded the JSON version."
        )

    return AnalysisResult(
        followers=followers,
        following=following,
        follower_files=sorted(set(follower_files)),
        following_files=sorted(set(following_files)),
        follower_timestamps=follower_timestamps,
        following_timestamps=following_timestamps,
    )


def sort_usernames(usernames: Sequence[str], sort_mode: str) -> List[str]:
    if sort_mode == "alpha":
        return sorted(usernames)
    if sort_mode == "length":
        return sorted(usernames, key=lambda username: (len(username), username))
    raise ValueError(f"Unsupported sort mode: {sort_mode}")


def apply_limit(usernames: Sequence[str], limit: Optional[int]) -> List[str]:
    if limit is None:
        return list(usernames)
    return list(usernames[:limit])


def write_txt(path: Path, usernames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{username}\n" for username in usernames]
    path.write_text("".join(lines), encoding="utf-8")


def write_csv(path: Path, usernames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["username", "profile_url"])
        for username in usernames:
            writer.writerow([username, profile_url_for(username)])


def write_json_report(
    path: Path,
    result: AnalysisResult,
    mode: str,
    sort_mode: str,
    limit: Optional[int],
    usernames: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "sort": sort_mode,
        "limit": limit,
        "stats": result.stats(),
        "time_ranges": result.relation_time_ranges(),
        "warnings": result.warnings(),
        "entries": [
            {"username": username, "profile_url": profile_url_for(username)}
            for username in usernames
        ],
        "used_files": {
            "followers": result.follower_files,
            "following": result.following_files,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_mode(args: argparse.Namespace) -> str:
    if args.fans:
        return "fans"
    if args.mutuals:
        return "mutuals"
    return "nonfollowers"


def parse_limit(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("--limit must be 0 or greater.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze followback relationships from an Instagram JSON export."
    )
    parser.add_argument(
        "export_path",
        help="Path to an extracted Instagram export folder or a .zip archive.",
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
        help="Show which JSON files were used for the calculation.",
    )
    parser.add_argument(
        "--inspect",
        metavar="USERNAME",
        help="Inspect one specific username and show how it was classified.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"instagram-followback-checker {VERSION}",
    )
    return parser


def print_summary(result: AnalysisResult) -> None:
    stats = result.stats()
    print(f"Followers found: {stats['followers']}")
    print(f"Following found: {stats['following']}")
    print(f"Do not follow back: {stats['nonfollowers']}")
    print(f"Fans: {stats['fans']}")
    print(f"Mutuals: {stats['mutuals']}")

    time_ranges = result.relation_time_ranges()
    follower_range = time_ranges["followers"]
    following_range = time_ranges["following"]
    if follower_range:
        print(
            "Followers time range: "
            f"{follower_range['start_date']} to {follower_range['end_date']}"
        )
    if following_range:
        print(
            "Following time range: "
            f"{following_range['start_date']} to {following_range['end_date']}"
        )

    for warning in result.warnings():
        print(f"Warning: {warning}")


def print_verbose_files(result: AnalysisResult) -> None:
    print("\nFollower files used:")
    for name in result.follower_files:
        print(f"  {name}")

    print("\nFollowing files used:")
    for name in result.following_files:
        print(f"  {name}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = analyze_export(Path(args.export_path).expanduser())
    except ExportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    mode = resolve_mode(args)
    selected_usernames = sort_usernames(result.usernames_for_mode(mode), args.sort)
    displayed_usernames = apply_limit(selected_usernames, args.limit)

    print_summary(result)

    if args.inspect:
        inspected = result.inspect_username(args.inspect)
        print("\nInspect:")
        print(f"Username: {inspected['username']}")
        print(f"In followers: {inspected['in_followers']}")
        print(f"In following: {inspected['in_following']}")
        print(f"Relationship: {inspected['relationship']}")

    if args.verbose:
        print_verbose_files(result)

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
