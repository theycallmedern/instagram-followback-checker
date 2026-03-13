#!/usr/bin/env python3
"""Prepare a bundled standalone Python runtime for the Tauri desktop build."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

RUNTIME_TAG = "20260310"
RUNTIME_ASSET = "cpython-3.12.13+20260310-aarch64-apple-darwin-install_only_stripped.tar.gz"
RUNTIME_URL = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{RUNTIME_TAG}/"
    f"{RUNTIME_ASSET.replace('+', '%2B')}"
)
PLAYWRIGHT_VERSION = "1.58.0"
RUNTIME_MANIFEST = {
    "runtime_tag": RUNTIME_TAG,
    "runtime_asset": RUNTIME_ASSET,
    "playwright_version": PLAYWRIGHT_VERSION,
}

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = REPO_ROOT / ".desktop-runtime"
DOWNLOADS_DIR = RUNTIME_ROOT / "downloads"
ARCHIVE_PATH = DOWNLOADS_DIR / RUNTIME_ASSET
PYTHON_ROOT = RUNTIME_ROOT / "python"
BROWSERS_ROOT = RUNTIME_ROOT / "playwright-browsers"
MANIFEST_PATH = RUNTIME_ROOT / "manifest.json"


def print_step(message: str) -> None:
    print(f"[desktop-runtime] {message}")


def runtime_is_current() -> bool:
    if not (PYTHON_ROOT / "bin" / "python3.12").exists():
        return False
    if not BROWSERS_ROOT.exists():
        return False
    if not MANIFEST_PATH.exists():
        return False
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload == RUNTIME_MANIFEST


def download_runtime_archive() -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if ARCHIVE_PATH.exists():
        print_step(f"Using cached runtime archive: {ARCHIVE_PATH.name}")
        return
    print_step(f"Downloading standalone Python runtime: {RUNTIME_ASSET}")
    with urllib.request.urlopen(RUNTIME_URL) as response, ARCHIVE_PATH.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def extract_runtime() -> None:
    print_step("Extracting standalone Python runtime")
    if PYTHON_ROOT.exists():
        shutil.rmtree(PYTHON_ROOT)
    with tempfile.TemporaryDirectory(prefix="ifb-runtime-") as temp_dir:
        temp_path = Path(temp_dir)
        with tarfile.open(ARCHIVE_PATH, "r:gz") as archive:
            archive.extractall(temp_path)
        extracted_root = temp_path / "python"
        if not extracted_root.exists():
            raise RuntimeError("Standalone runtime archive did not contain the expected python/ directory.")
        shutil.copytree(extracted_root, PYTHON_ROOT)


def install_playwright() -> None:
    python_bin = PYTHON_ROOT / "bin" / "python3.12"
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_ROOT)

    print_step("Installing Playwright into the bundled runtime")
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", f"playwright=={PLAYWRIGHT_VERSION}"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )

    print_step("Installing Chromium into the bundled runtime")
    subprocess.run(
        [str(python_bin), "-m", "playwright", "install", "chromium"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def write_manifest() -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(RUNTIME_MANIFEST, indent=2), encoding="utf-8")


def main() -> int:
    if runtime_is_current():
        print_step("Bundled Python runtime is already ready")
        return 0

    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    download_runtime_archive()
    extract_runtime()
    install_playwright()
    write_manifest()
    print_step("Bundled Python runtime is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
