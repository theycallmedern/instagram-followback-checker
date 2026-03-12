#!/usr/bin/env python3
"""Find accounts you follow on Instagram that do not follow you back.

The script works with official Instagram exports in either directory or ZIP
form. It avoids the unofficial API and instead parses the JSON files from an
account export.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

USERNAME_RE = re.compile(r"[a-z0-9._]{1,30}")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}


@dataclass
class AnalysisResult:
    followers: Set[str]
    following: Set[str]
    follower_files: List[str]
    following_files: List[str]

    def not_following_back(self) -> List[str]:
        return sorted(self.following - self.followers)


class JsonSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.is_zip = path.is_file() and path.suffix.lower() == ".zip"
        self._zip_file: Optional[zipfile.ZipFile] = None

    def __enter__(self) -> "JsonSource":
        if self.is_zip:
            self._zip_file = zipfile.ZipFile(self.path)
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

    def read_json(self, name: str) -> Any:
        if self.is_zip:
            assert self._zip_file is not None
            raw = self._zip_file.read(name)
            return json.loads(raw.decode("utf-8"))

        file_path = self.path / Path(name)
        return json.loads(file_path.read_text(encoding="utf-8"))


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
        if segments[0] in {"p", "reel", "stories", "tv", "explore"}:
            return None
        value = segments[0]

    value = value.lstrip("@").strip().strip("/").lower()
    if USERNAME_RE.fullmatch(value):
        return value
    return None


def extract_usernames(node: Any) -> Set[str]:
    usernames: Set[str] = set()

    if isinstance(node, dict):
        string_list = node.get("string_list_data")
        if isinstance(string_list, list):
            for item in string_list:
                if not isinstance(item, dict):
                    continue
                username = normalize_username(item.get("value"))
                if username is None:
                    username = normalize_username(item.get("href"))
                if username is not None:
                    usernames.add(username)

        for value in node.values():
            if isinstance(value, (dict, list, str)):
                usernames.update(extract_usernames(value))

    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list, str)):
                usernames.update(extract_usernames(item))

    elif isinstance(node, str):
        username = normalize_username(node)
        if username is not None:
            usernames.add(username)

    return usernames


def path_relation_hint(name: str) -> Optional[str]:
    basename = PurePosixPath(name).name.lower()

    if re.fullmatch(r"followers(?:_\d+)?\.json", basename):
        return "followers"
    if re.fullmatch(r"(?:following|followings)(?:_\d+)?\.json", basename):
        return "following"
    if basename == "relationships_followers.json":
        return "followers"
    if basename == "relationships_following.json":
        return "following"
    return None


def payload_relation_hint(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    keys = set(payload.keys())
    if "relationships_followers" in keys:
        return "followers"
    if "relationships_following" in keys:
        return "following"
    return None


def load_relation_documents(
    source: JsonSource, names: Iterable[str]
) -> Tuple[Set[str], List[str]]:
    usernames: Set[str] = set()
    used_files: List[str] = []

    for name in names:
        payload = source.read_json(name)
        extracted = extract_usernames(payload)
        if extracted:
            usernames.update(extracted)
            used_files.append(name)

    return usernames, used_files


def analyze_export(path: Path) -> AnalysisResult:
    if not path.exists():
        raise FileNotFoundError(f"Путь не найден: {path}")
    if not path.is_dir() and not (path.is_file() and path.suffix.lower() == ".zip"):
        raise ValueError("Нужна папка с экспортом Instagram или ZIP-архив.")

    with JsonSource(path) as source:
        names = source.iter_json_names()

        follower_candidates = [name for name in names if path_relation_hint(name) == "followers"]
        following_candidates = [name for name in names if path_relation_hint(name) == "following"]

        followers, follower_files = load_relation_documents(source, follower_candidates)
        following, following_files = load_relation_documents(source, following_candidates)

        inspected = set(follower_files + following_files)
        need_deep_scan = not follower_files or not following_files

        if need_deep_scan:
            for name in names:
                if name in inspected:
                    continue
                payload = source.read_json(name)
                relation = payload_relation_hint(payload)
                if relation == "followers":
                    extracted = extract_usernames(payload)
                    if extracted:
                        followers.update(extracted)
                        follower_files.append(name)
                elif relation == "following":
                    extracted = extract_usernames(payload)
                    if extracted:
                        following.update(extracted)
                        following_files.append(name)

    if not follower_files:
        raise RuntimeError(
            "Не удалось найти JSON с подписчиками в экспорте. Проверь, что выгрузка сделана в формате JSON."
        )
    if not following_files:
        raise RuntimeError(
            "Не удалось найти JSON с подписками в экспорте. Проверь, что выгрузка сделана в формате JSON."
        )

    return AnalysisResult(
        followers=followers,
        following=following,
        follower_files=sorted(set(follower_files)),
        following_files=sorted(set(following_files)),
    )


def write_csv(path: Path, usernames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["username"])
        for username in usernames:
            writer.writerow([username])


def write_json_report(path: Path, result: AnalysisResult, usernames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "followers_count": len(result.followers),
        "following_count": len(result.following),
        "not_following_back_count": len(usernames),
        "not_following_back": usernames,
        "used_files": {
            "followers": result.follower_files,
            "following": result.following_files,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Показывает аккаунты Instagram, на которые вы подписаны, "
            "но которые не подписаны на вас в ответ."
        )
    )
    parser.add_argument("export_path", help="Путь к папке экспорта Instagram или ZIP-архиву.")
    parser.add_argument("--csv", dest="csv_path", help="Сохранить результат в CSV.")
    parser.add_argument("--json", dest="json_path", help="Сохранить результат в JSON.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показать, какие JSON-файлы использовались для расчета.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = analyze_export(Path(args.export_path).expanduser())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    not_following_back = result.not_following_back()

    print(f"Подписчиков в экспорте: {len(result.followers)}")
    print(f"Подписок в экспорте: {len(result.following)}")
    print(f"Не подписаны в ответ: {len(not_following_back)}")

    if args.verbose:
        print("\nФайлы подписчиков:")
        for name in result.follower_files:
            print(f"  {name}")
        print("\nФайлы подписок:")
        for name in result.following_files:
            print(f"  {name}")

    if not_following_back:
        print("\nСписок:")
        for username in not_following_back:
            print(username)
    else:
        print("\nВсе взаимно.")

    if args.csv_path:
        csv_path = Path(args.csv_path).expanduser()
        write_csv(csv_path, not_following_back)
        print(f"\nCSV сохранен: {csv_path}")

    if args.json_path:
        json_path = Path(args.json_path).expanduser()
        write_json_report(json_path, result, not_following_back)
        print(f"JSON сохранен: {json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
