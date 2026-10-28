"""Run with python -m unittest discover -s tests -v. All fixtures stay in repo."""
import importlib.util
import json
import plistlib
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from platform_config import config_paths, iterm_combo, parse_iterm, parse_kitty, read_terminals


def load_server(home, platform="win32", source=None):
    # Path.home is replaced before module initialisation: never use real configs.
    spec = importlib.util.spec_from_file_location("fixture_server", ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(Path, "home", return_value=home), patch.object(sys, "platform", platform), \
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(home / ".config")}):
        if source is None:
            spec.loader.exec_module(module)
        else:
            exec(compile(source, str(ROOT / "server.py"), "exec"), module.__dict__)
    module.LOG_PATH = home / "test.log"
    module.SNAP_DIR = home / "snapshots"
    return module


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-", dir=ROOT)
        self.home = Path(self.temp.name).resolve()
        assert self.home.is_relative_to(ROOT)
        self.addCleanup(self.temp.cleanup)


class Paths(Fixtures):
    def test_macos(self):
        p = config_paths("darwin", self.home, {})
        self.assertEqual(p["vscode"], self.home / "Library/Application Support/Code/User/keybindings.json")
        self.assertEqual(p["claude"], self.home / ".claude/keybindings.json")
        self.assertEqual(p["obsidian"], self.home / "Library/Application Support/obsidian/obsidian.json")

    def test_linux_xdg_and_relative_fallback(self):
        xdg = self.home / "settings with spaces"
        p = config_paths("linux", self.home, {"XDG_CONFIG_HOME": str(xdg)})
        self.assertEqual(p["vscode"], xdg / "Code/User/keybindings.json")
        self.assertEqual(p["kitty"], xdg / "kitty/kitty.conf")
        self.assertEqual(config_paths("linux", self.home, {"XDG_CONFIG_HOME": "relative"})["obsidian"],
                         self.home / ".config/obsidian/obsidian.json")

    def test_unsupported_os(self):
        with self.assertRaises(ValueError):
            config_paths("freebsd", self.home, {})

    def test_server_platform_selection(self):
        for os_name in ("win32", "darwin", "linux"):
            with self.subTest(platform=os_name):
                with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.home / ".config")}):
                    s = load_server(self.home, os_name)
                if os_name == "win32":
                    self.assertEqual(s.VSCODE_KEYBINDINGS, self.home / "AppData/Roaming/Code/User/keybindings.json")
                    self.assertEqual(len(s.WT_SETTINGS), 2)
                else:
                    self.assertEqual(s.VSCODE_KEYBINDINGS, config_paths(os_name, self.home, {})["vscode"])
                    self.assertEqual(s.WT_SETTINGS, [])
                    self.assertEqual(s.read_powertoys(), ([], 0))
                    with patch.object(s.subprocess, "run", side_effect=AssertionError("must not invoke taskkill")):
                        self.assertFalse(s.restart_powertoys()[0])

    def test_native_backend_selection_without_opening_a_window(self):
        for platform, gui in (("win32", None), ("darwin", "cocoa"), ("linux", "gtk")):
            s = load_server(self.home, platform)
            webview = types.SimpleNamespace(create_window=unittest.mock.Mock(), start=unittest.mock.Mock())
            with patch.dict(sys.modules, {"webview": webview}), patch.object(s, "acquire", return_value=(8743, None)):
                s.main()
            webview.create_window.assert_called_once()
            if gui is None:
                webview.start.assert_called_once_with()
            else:
                webview.start.assert_called_once_with(gui=gui)


class Terminals(Fixtures):
    def test_kitty_mod_order_override_and_unmap(self):
        rows, skipped = parse_kitty('map kitty_mod+t new_tab\nkitty_mod cmd+shift\n'
                                  'map cmd+shift+t next_tab\nmap ctrl+c copy_to_clipboard\n'
                                  'map ctrl+c no_op\nmap alt+f2 launch "some argument"')
        self.assertEqual(rows, [{"combo": "shift+win+t", "action": "next_tab", "source": "kitty"},
                                {"combo": "alt+f2", "action": "launch", "source": "kitty"}])
        self.assertEqual(skipped, 0)

    def test_kitty_skips_sequences_conditionals_and_includes(self):
        rows, skipped = parse_kitty('include other.conf\nmap ctrl+a>ctrl+b x\nmap --when-focus title:x ctrl+t new_tab\nmap hyper+x x\nmap "broken')
        self.assertEqual(rows, [])
        self.assertEqual(skipped, 5)

    def test_kitty_clear(self):
        self.assertEqual(parse_kitty('map ctrl+t new_tab\nclear_all_shortcuts yes')[0], [])
        self.assertEqual(parse_kitty('map ctrl+t new_tab\nmap ctrl+t')[0], [])

    def test_iterm_modifier_bits_and_function_keys(self):
        self.assertEqual(iterm_combo("0x74-0x120000"), "shift+win+t")
        self.assertEqual(iterm_combo("0xf700-0xc0000"), "ctrl+alt+up")
        self.assertEqual(iterm_combo("0xf704-0x0"), "f1")
        self.assertEqual(iterm_combo("0x7f-0x0"), "backspace")
        self.assertEqual(iterm_combo("0x74-0x120000-0x11"), "shift+win+t")
        self.assertEqual(iterm_combo("0x74-0x120000-0x11-0x1"), "shift+win+t")
        self.assertEqual(iterm_combo("0xf700-0x2c0000"), "ctrl+alt+up")
        self.assertIsNone(iterm_combo("0x74-0x100000000"))
        self.assertIsNone(iterm_combo("0x31-0x200000"))
        for bad in (None, "bad", "0xff-0x0", "0x110000-0x0"):
            self.assertIsNone(iterm_combo(bad))

    def test_iterm_profiles_are_labelled(self):
        data = {"GlobalKeyMap": {"0x74-0x100000": {"Action": 10}},
                "New Bookmarks": [{"Name": "Example", "Keyboard Map": {"0x74-0x100000": {"Action": 11}}}]}
        rows, skipped = parse_iterm(data)
        self.assertEqual(len(rows), 2)
        self.assertIn("Example", rows[1]["action"])
        self.assertEqual(skipped, 0)

    def test_plist_xml_and_binary(self):
        p = config_paths("darwin", self.home, {})
        p["iterm2"].parent.mkdir(parents=True)
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            p["iterm2"].write_bytes(plistlib.dumps({"GlobalKeyMap": {"0x63-0x100000": {"Action": 14}}}, fmt=fmt))
            self.assertEqual(read_terminals(p)[0][0]["combo"], "win+c")

    def test_missing_and_bad_configs(self):
        p = config_paths("darwin", self.home, {})
        self.assertEqual(read_terminals(p)[0], [])
        write(p["iterm2"], "not a plist")
        rows, notes = read_terminals(p)
        self.assertEqual(rows, [])
        self.assertTrue(any("could not read" in n for n in notes))
        write(p["iterm2"], '<?xml version="1.0"?><plist>')
        self.assertTrue(any("could not read" in n for n in read_terminals(p)[1]))


class HTTP(Fixtures):
    def start(self, os_name):
        s = load_server(self.home, os_name)
        httpd = s.Server(("127.0.0.1", 0), s.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.s = s
        self.url = "http://127.0.0.1:%d" % httpd.server_address[1]
        return s

    def request(self, path, data=None, token=True, origin=None):
        headers = {"X-HotkeyHQ-Token": self.s.TOKEN} if token else {}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def test_windows_page_and_token_origin_guards(self):
        self.start("win32")
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertNotIn("__HKHQ_", html)
        self.assertIn('content="windows"', html)
        self.assertNotIn("var HKHQPlatform =", html)
        self.assertEqual(self.request("/api/ping", token=False)[0], 200)
        self.assertEqual(self.request("/api/vscode", token=False)[0], 403)
        self.assertEqual(self.request("/api/vscode", origin="https://example.invalid")[0], 403)
        self.assertEqual(self.request("/api/assign", {}, origin="https://example.invalid")[0], 403)

    def test_each_platform_endpoints_use_fixture_paths(self):
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform):
                self.start(platform)
                for path in ("/api/keybindings", "/api/vscode", "/api/terminal", "/api/obsidian", "/api/powertoys", "/api/snapshots", "/api/history"):
                    self.assertEqual(self.request(path)[0], 200, path)
                html = self.request("/")[1]
                if platform != "win32":
                    self.assertIn("var HKHQPlatform =", html)
                    self.assertEqual(self.request("/api/powertoys/restart", {})[0], 400)

    def test_assign_remove_preserves_vscode_removals_and_backup(self):
        for platform, combo in (("win32", "ctrl+alt+t"), ("darwin", "cmd+alt+t"), ("linux", "ctrl+alt+t")):
            self.start(platform)
            write(self.s.VSCODE_KEYBINDINGS, '// comment\n[{"key":"ctrl+p","command":"-workbench.action.quickOpen"},]')
            d = {"context": "-", "target": "vscode", "keystroke": combo, "action": "example.command"}
            self.assertTrue(json.loads(self.request("/api/assign", d)[1])["ok"])
            saved = json.loads(self.s.VSCODE_KEYBINDINGS.read_text())
            self.assertEqual(saved[0]["command"], "example.command")
            self.assertEqual(saved[1]["command"], "-workbench.action.quickOpen")
            self.assertIn('// comment', self.s.VSCODE_KEYBINDINGS.with_suffix('.json.hotkeyhq-backup').read_text())
            self.assertEqual(self.request("/api/assign", d)[0], 409)
            self.assertTrue(json.loads(self.request("/api/unassign", d)[1])["removed"])

    def test_claude_assign_conflict_and_remove(self):
        self.start("darwin")
        d = {"context": "Chat", "keystroke": "super+k", "action": "chat:clearScreen"}
        self.assertEqual(self.request("/api/assign", d)[0], 200)
        d["action"] = "chat:stash"
        self.assertEqual(self.request("/api/assign", d)[0], 409)
        d["force"] = True
        self.assertEqual(self.request("/api/assign", d)[0], 200)
        self.assertTrue(json.loads(self.request("/api/unassign", d)[1])["removed"])

    def test_kitty_snapshot_restore_and_history(self):
        self.start("linux")
        kitty = self.s.NATIVE_PATHS["kitty"]
        write(kitty, "map ctrl+t new_tab")
        first = self.s.take_snapshot()[0]
        write(kitty, "map alt+t new_tab")
        # Avoid second-resolution timestamp collisions in the original format.
        (self.s.SNAP_DIR / (first["id"] + ".json")).rename(self.s.SNAP_DIR / "2000-01-01_00-00-00.json")
        self.s.take_snapshot()
        self.assertEqual(self.s.history()[0]["changes"][0]["kind"], "moved")
        ok, _ = self.s.restore_snapshot("2000-01-01_00-00-00", "kitty")
        self.assertTrue(ok)
        self.assertEqual(kitty.read_text(), "map ctrl+t new_tab")

    def test_obsidian_mac_mod_and_export(self):
        self.start("darwin")
        vault = self.home / "vault"
        write(vault / ".obsidian/hotkeys.json", {"editor:save-file": [{"modifiers": ["Mod", "Shift"], "key": "S"}]})
        write(self.s.OBSIDIAN_REGISTRY, {"vaults": {"example": {"path": str(vault), "open": True}}})
        self.assertEqual(self.s.read_obsidian()[0][0]["combo"], "shift+win+s")
        self.assertTrue(self.s.export_obsidian("# Example")[0])
        self.assertEqual((vault / "Keyboard Shortcuts (Hotkey HQ).md").read_text(), "# Example")


class WindowsRegression(Fixtures):
    def test_windows_readers(self):
        current = load_server(self.home)
        write(current.VSCODE_KEYBINDINGS, [{"key": "ctrl+p", "command": "test"}, {"key": "ctrl+t", "command": "-removed"}])
        write(current.WT_SETTINGS[0], {"actions": [{"keys": "ctrl+shift+pgup", "command": "copy"}]})
        write(current.POWERTOYS_DIR / "settings.json", {"enabled": {"Test": True, "Disabled": False}})
        write(current.POWERTOYS_DIR / "Test/settings.json", {"properties": {"hotkey": {"value": {"win": True, "code": 65}}}})
        write(current.POWERTOYS_DIR / "Disabled/settings.json", {"properties": {"hotkey": {"code": 66}}})
        self.assertEqual(current.read_vscode(), ([{"key": "ctrl+p", "command": "test"}], [{"key": "ctrl+t", "command": "removed"}]))
        self.assertEqual(current.read_terminal(), [{"combo": "ctrl+shift+pageup", "action": "Copy"}])
        self.assertEqual(current.read_powertoys()[0][0]["combo"], "win+a")
        self.assertEqual(current.read_powertoys()[1], 1)
        self.assertEqual(current.obsidian_vaults(), [])
        self.assertEqual(set(current._tracked_files()), {"vscode", "terminal", "powertoys/Test", "powertoys/Disabled"})
        self.assertIsNone(current._node_from_combo("invalid+z"))


if __name__ == "__main__":
    unittest.main()
