import os
import unittest
from unittest import mock

from scripts import prepare_desktop_runtime


class PrepareDesktopRuntimeTests(unittest.TestCase):
    def test_detect_runtime_profile_supports_windows_x64(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "IFB_DESKTOP_RUNTIME_OS": "windows",
                "IFB_DESKTOP_RUNTIME_ARCH": "amd64",
            },
            clear=False,
        ):
            profile = prepare_desktop_runtime.detect_runtime_profile()

        self.assertEqual(profile.name, "windows-x86_64")
        self.assertTrue(profile.asset.endswith("x86_64-pc-windows-msvc-install_only_stripped.tar.gz"))
        self.assertEqual(str(prepare_desktop_runtime.bundled_python_path(profile)), str(prepare_desktop_runtime.PYTHON_ROOT / "python.exe"))

    def test_detect_runtime_profile_supports_macos_arm64(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "IFB_DESKTOP_RUNTIME_OS": "darwin",
                "IFB_DESKTOP_RUNTIME_ARCH": "arm64",
            },
            clear=False,
        ):
            profile = prepare_desktop_runtime.detect_runtime_profile()

        self.assertEqual(profile.name, "macos-aarch64")
        self.assertEqual(profile.python_relative_path, "bin/python3.12")

    def test_detect_runtime_profile_rejects_unsupported_target(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "IFB_DESKTOP_RUNTIME_OS": "linux",
                "IFB_DESKTOP_RUNTIME_ARCH": "x86_64",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsupported desktop runtime target"):
                prepare_desktop_runtime.detect_runtime_profile()


if __name__ == "__main__":
    unittest.main()
