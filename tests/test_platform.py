"""Tests for cs2platform (capture registry, auto selection, focus, notify) and import-time hygiene.

No real screenshots, no real notifications, no real hyprctl/osascript: every OS call is mocked.
Run: /home/andrii/fun/.venv/bin/python -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cs2platform import capture, focus, notify  # noqa: E402


def _which_from(available):
    """shutil.which stand-in: returns a path for tools in `available`, else None."""
    return lambda name: f"/usr/bin/{name}" if name in available else None


class CaptureRegistryTests(unittest.TestCase):
    def test_registry_keys_are_the_explicit_config_tools(self):
        self.assertEqual(set(capture.CAPTURERS), {"mss", "pil", "screencapture", "grim", "imagemagick"})

    def test_auto_only_entries_are_not_selectable_by_config_name(self):
        # "win32" / "macos_mss" must not be reachable through screenshot_tool, only through auto mode.
        self.assertTrue(set(capture.AUTO_CAPTURERS).isdisjoint(capture.CAPTURERS))


class SelectAutoToolTests(unittest.TestCase):
    def test_windows_always_uses_win32_path(self):
        self.assertEqual(capture.select_auto_tool("win32", _which_from(set()), {}), "win32")

    def test_macos_prefers_screencapture_then_mss(self):
        self.assertEqual(capture.select_auto_tool("darwin", _which_from({"screencapture"}), {}), "screencapture")
        self.assertEqual(capture.select_auto_tool("darwin", _which_from(set()), {}), "macos_mss")

    def test_linux_wayland_with_grim_uses_grim(self):
        env = {"WAYLAND_DISPLAY": "wayland-1"}
        self.assertEqual(capture.select_auto_tool("linux", _which_from({"grim", "import"}), env), "grim")

    def test_linux_wayland_without_grim_falls_back_to_import(self):
        env = {"WAYLAND_DISPLAY": "wayland-1"}
        self.assertEqual(capture.select_auto_tool("linux", _which_from({"import"}), env), "imagemagick")

    def test_linux_x11_with_grim_present_still_uses_import(self):
        # grim is only chosen when WAYLAND_DISPLAY is set, matching the previous behaviour.
        self.assertEqual(capture.select_auto_tool("linux", _which_from({"grim", "import"}), {}), "imagemagick")

    def test_linux_without_any_tool_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            capture.select_auto_tool("linux", _which_from(set()), {})
        self.assertIn("No screenshot tool found", str(ctx.exception))


class CaptureDispatchTests(unittest.TestCase):
    """capture_screenshot picks a capturer and calls it; every capturer is replaced by a mock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _patch_common(self, tool, platform_name="linux", available=frozenset(), env=None):
        stack = [
            mock.patch.object(capture, "load_config", return_value={"screenshot_tool": tool}),
            mock.patch.object(capture, "get_screenshot_dir", return_value=self.tmp_dir),
            mock.patch.object(capture.sys, "platform", platform_name),
            mock.patch.object(capture.shutil, "which", side_effect=_which_from(set(available))),
            mock.patch.dict(os.environ, env or {}, clear=True),
        ]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)

    def test_explicit_tool_calls_registry_entry(self):
        fake = mock.Mock(side_effect=lambda out: out)
        self._patch_common("grim")
        with mock.patch.dict(capture.CAPTURERS, {"grim": fake}):
            out = capture.capture_screenshot()
        fake.assert_called_once()
        self.assertEqual(fake.call_args.args[0], out)
        self.assertEqual(out.parent, self.tmp_dir)
        self.assertTrue(out.name.startswith("cs2_") and out.name.endswith(".png"))

    def test_unknown_tool_name_goes_through_auto_selection(self):
        grim = mock.Mock(side_effect=lambda out: out)
        imagemagick = mock.Mock(side_effect=lambda out: out)
        self._patch_common("not-a-tool", platform_name="linux", available={"import"})
        with mock.patch.dict(capture.CAPTURERS, {"grim": grim, "imagemagick": imagemagick}):
            capture.capture_screenshot()
        imagemagick.assert_called_once()
        grim.assert_not_called()

    def test_auto_on_wayland_uses_grim(self):
        grim = mock.Mock(side_effect=lambda out: out)
        self._patch_common("auto", platform_name="linux", available={"grim", "import"}, env={"WAYLAND_DISPLAY": "w"})
        with mock.patch.dict(capture.CAPTURERS, {"grim": grim}):
            capture.capture_screenshot()
        grim.assert_called_once()

    def test_auto_without_tools_raises_and_never_captures(self):
        self._patch_common("auto", platform_name="linux", available=set())
        with self.assertRaises(RuntimeError):
            capture.capture_screenshot()


class RunCheckedTests(unittest.TestCase):
    def test_failed_command_raises_with_stderr(self):
        res = mock.Mock(returncode=1, stderr="boom\n")
        with mock.patch.object(capture.subprocess, "run", return_value=res) as run:
            with self.assertRaises(RuntimeError) as ctx:
                capture._run_checked(["import", "-window", "root", "x.png"], Path("/nonexistent/x.png"))
        run.assert_called_once()
        self.assertIn("Screenshot failed (import -window root x.png): boom", str(ctx.exception))


class NotifyCommandTests(unittest.TestCase):
    def test_linux_notify_send_strips_ld_library_path(self):
        env = {"HOME": "/h", "LD_LIBRARY_PATH": "/bad"}
        spec = notify.build_notification_command("T", "M", "linux", _which_from({"notify-send"}), env)
        argv, kwargs = spec
        self.assertEqual(argv, ["notify-send", "T", "M"])
        self.assertEqual(kwargs, {"env": {"HOME": "/h"}, "check": False})

    def test_linux_without_notify_send_builds_nothing(self):
        self.assertIsNone(notify.build_notification_command("T", "M", "linux", _which_from(set()), {}))

    def test_macos_osascript_escapes_quotes_and_backslashes(self):
        argv, kwargs = notify.build_notification_command(
            'Ti"tle', 'a "b" \\c', "darwin", _which_from({"osascript"}), {}
        )
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertEqual(argv[2], 'display notification "a \\"b\\" \\\\c" with title "Ti\\"tle"')
        self.assertEqual(kwargs, {"check": False})

    def test_macos_without_osascript_builds_nothing(self):
        self.assertIsNone(notify.build_notification_command("T", "M", "darwin", _which_from(set()), {}))

    def test_windows_is_a_noop(self):
        self.assertIsNone(notify.build_notification_command("T", "M", "win32", _which_from({"notify-send"}), {}))

    def test_send_runs_built_command_with_mocked_subprocess(self):
        with mock.patch.object(notify.sys, "platform", "linux"), \
                mock.patch.object(notify.shutil, "which", side_effect=_which_from({"notify-send"})), \
                mock.patch.object(notify.subprocess, "run") as run:
            notify.send_notification("Title", "Body")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["notify-send", "Title", "Body"])

    def test_send_swallows_subprocess_errors(self):
        with mock.patch.object(notify.sys, "platform", "linux"), \
                mock.patch.object(notify.shutil, "which", side_effect=_which_from({"notify-send"})), \
                mock.patch.object(notify.subprocess, "run", side_effect=OSError("no bus")):
            notify.send_notification("Title", "Body")  # must not raise


class FocusTests(unittest.TestCase):
    def test_is_cs2_client_matches_class_names(self):
        self.assertTrue(focus.is_cs2_client({"class": "cs2"}))
        self.assertTrue(focus.is_cs2_client({"class": "x", "initialClass": "steam_app_730"}))
        self.assertFalse(focus.is_cs2_client({"class": "firefox"}))

    def test_linux_without_hyprctl_assumes_cs2_is_active(self):
        with mock.patch.object(focus.sys, "platform", "linux"), \
                mock.patch.object(focus.shutil, "which", return_value=None):
            self.assertTrue(focus.is_cs2_active())

    def test_hyprland_active_window_decides(self):
        def run(cmd, **kwargs):
            info = {"class": "cs2"} if "cs2" == self.active else {"class": "kitty"}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(info), stderr="")

        self.active = "cs2"
        with mock.patch.object(focus.sys, "platform", "linux"), \
                mock.patch.object(focus.shutil, "which", side_effect=_which_from({"hyprctl"})), \
                mock.patch.object(focus.subprocess, "run", side_effect=run):
            self.assertTrue(focus.is_cs2_active())
            self.active = "kitty"
            self.assertFalse(focus.is_cs2_active())


class ImportHygieneTests(unittest.TestCase):
    def test_importing_server_creates_no_screenshot_dir_and_reads_no_config(self):
        # Run in a fresh interpreter: guard Path.mkdir for 'screenshots' and make load_config fail if
        # anything reads it at import time. Importing server must still succeed.
        code = (
            "import sys, pathlib; sys.path.insert(0, %r)\n"
            "orig = pathlib.Path.mkdir\n"
            "def guard(self, *a, **k):\n"
            "    if self.name == 'screenshots':\n"
            "        raise AssertionError('screenshots dir created at import')\n"
            "    return orig(self, *a, **k)\n"
            "pathlib.Path.mkdir = guard\n"
            "import storage.config_store as config_store\n"
            "def boom(*a, **k):\n"
            "    raise AssertionError('config read at import')\n"
            "config_store.load_config = boom\n"
            "import app.server as server\n"
            "print('IMPORT_OK')\n"
        ) % str(ROOT)
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertIn("IMPORT_OK", res.stdout)

    def test_init_applies_runtime_config_to_server_globals(self):
        import app.server as server

        cfg = {"tab_delay_sec": 1.5, "tab_cooldown_sec": 9.0, "cs2_only": False, "notify_enabled": False}
        with mock.patch.object(server, "load_config", return_value=cfg), \
                mock.patch.object(server, "_TAB_DELAY_SEC", server._TAB_DELAY_SEC), \
                mock.patch.object(server, "_TAB_COOLDOWN_SEC", server._TAB_COOLDOWN_SEC), \
                mock.patch.object(server, "_CS2_ONLY", server._CS2_ONLY), \
                mock.patch.object(server, "_NOTIFY_ENABLED", server._NOTIFY_ENABLED):
            server.init()
            self.assertEqual(server._TAB_DELAY_SEC, 1.5)
            self.assertEqual(server._TAB_COOLDOWN_SEC, 9.0)
            self.assertFalse(server._CS2_ONLY)
            self.assertFalse(server._NOTIFY_ENABLED)

    def test_server_notify_wrapper_respects_enabled_flag(self):
        import app.server as server

        with mock.patch.object(server, "_NOTIFY_ENABLED", False), \
                mock.patch.object(server, "send_notification") as send:
            server._send_notification("T", "M")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
