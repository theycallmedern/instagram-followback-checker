#!/usr/bin/env python3
"""Prepare a bundled standalone Python runtime for the Tauri desktop build."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RUNTIME_TAG = "20260310"
PYTHON_VERSION = "3.12.13"
PLAYWRIGHT_VERSION = "1.58.0"


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    asset: str
    python_relative_path: str


RUNTIME_PROFILES = {
    ("darwin", "arm64"): RuntimeProfile(
        name="macos-aarch64",
        asset=f"cpython-{PYTHON_VERSION}+{RUNTIME_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz",
        python_relative_path="bin/python3.12",
    ),
    ("darwin", "x86_64"): RuntimeProfile(
        name="macos-x86_64",
        asset=f"cpython-{PYTHON_VERSION}+{RUNTIME_TAG}-x86_64-apple-darwin-install_only_stripped.tar.gz",
        python_relative_path="bin/python3.12",
    ),
    ("windows", "arm64"): RuntimeProfile(
        name="windows-arm64",
        asset=f"cpython-{PYTHON_VERSION}+{RUNTIME_TAG}-aarch64-pc-windows-msvc-install_only_stripped.tar.gz",
        python_relative_path="python.exe",
    ),
    ("windows", "x86_64"): RuntimeProfile(
        name="windows-x86_64",
        asset=f"cpython-{PYTHON_VERSION}+{RUNTIME_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        python_relative_path="python.exe",
    ),
}

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = REPO_ROOT / ".desktop-runtime"
PYTHON_ROOT = RUNTIME_ROOT / "python"
BROWSERS_ROOT = RUNTIME_ROOT / "playwright-browsers"
MANIFEST_PATH = RUNTIME_ROOT / "manifest.json"


def print_step(message: str) -> None:
    print(f"[desktop-runtime] {message}")


def normalize_machine(value: str) -> str:
    machine = value.lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86-64": "x86_64",
        "aarch64": "arm64",
    }
    return aliases.get(machine, machine)


def detect_runtime_profile() -> RuntimeProfile:
    system_name = os.environ.get("IFB_DESKTOP_RUNTIME_OS", platform.system()).strip().lower()
    machine = normalize_machine(os.environ.get("IFB_DESKTOP_RUNTIME_ARCH", platform.machine()))
    key = (system_name, machine)
    profile = RUNTIME_PROFILES.get(key)
    if profile is None:
        supported = ", ".join(f"{os_name}/{arch}" for os_name, arch in sorted(RUNTIME_PROFILES))
        raise RuntimeError(
            f"Unsupported desktop runtime target {system_name}/{machine}. Supported targets: {supported}."
        )
    return profile


def runtime_url(profile: RuntimeProfile) -> str:
    return (
        f"https://github.com/astral-sh/python-build-standalone/releases/download/{RUNTIME_TAG}/"
        f"{profile.asset.replace('+', '%2B')}"
    )


def archive_path(profile: RuntimeProfile) -> Path:
    return RUNTIME_ROOT / "downloads" / profile.asset


def runtime_manifest(profile: RuntimeProfile) -> dict[str, str]:
    return {
        "runtime_tag": RUNTIME_TAG,
        "runtime_asset": profile.asset,
        "runtime_profile": profile.name,
        "playwright_version": PLAYWRIGHT_VERSION,
    }


def bundled_python_path(profile: RuntimeProfile) -> Path:
    return PYTHON_ROOT / Path(profile.python_relative_path)


def runtime_is_current(profile: RuntimeProfile) -> bool:
    if not bundled_python_path(profile).exists():
        return False
    if not BROWSERS_ROOT.exists():
        return False
    if not MANIFEST_PATH.exists():
        return False
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload == runtime_manifest(profile)


def download_runtime_archive(profile: RuntimeProfile) -> None:
    downloads_dir = RUNTIME_ROOT / "downloads"
    archive = archive_path(profile)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        print_step(f"Using cached runtime archive: {archive.name}")
        return
    print_step(f"Downloading standalone Python runtime: {profile.asset}")
    with urllib.request.urlopen(runtime_url(profile)) as response, archive.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def extract_runtime(profile: RuntimeProfile) -> None:
    print_step(f"Extracting standalone Python runtime for {profile.name}")
    if PYTHON_ROOT.exists():
        shutil.rmtree(PYTHON_ROOT)
    with tempfile.TemporaryDirectory(prefix="ifb-runtime-") as temp_dir:
        temp_path = Path(temp_dir)
        with tarfile.open(archive_path(profile), "r:gz") as archive:
            archive.extractall(temp_path)
        extracted_root = temp_path / "python"
        if not extracted_root.exists():
            raise RuntimeError("Standalone runtime archive did not contain the expected python/ directory.")
        shutil.copytree(extracted_root, PYTHON_ROOT)


def install_playwright(profile: RuntimeProfile) -> None:
    python_bin = bundled_python_path(profile)
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


def write_manifest(profile: RuntimeProfile) -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(runtime_manifest(profile), indent=2), encoding="utf-8")


def main() -> int:
    profile = detect_runtime_profile()
    print_step(f"Preparing bundled runtime for {profile.name}")

    if runtime_is_current(profile):
        print_step("Bundled Python runtime is already ready")
        return 0

    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    download_runtime_archive(profile)
    extract_runtime(profile)
    install_playwright(profile)
    write_manifest(profile)
    print_step("Bundled Python runtime is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
