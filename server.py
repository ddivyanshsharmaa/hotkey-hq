"""Hotkey HQ — local server and config readers.

Everything here is local to this machine. The server binds 127.0.0.1, serves the
single-page interface, and reads the keyboard configuration of whoever is running
it: their Claude Code keybindings, their VS Code keybindings, their PowerToys
hotkeys and their Windows Terminal bindings.

The only files it ever writes are the Claude Code and VS Code keybinding files,
and only when the user clicks Assign or Remove.
"""
import ctypes
import json
import re
import secrets
import shutil
import socket
import sys
import threading
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_ID = "hotkey-hq"
VERSION = "1.0.0"
# The built-in shortcut table is a hand-maintained snapshot. Stamp it so a stale
# table is visible in the UI instead of quietly wrong.
VERIFIED_AGAINST = "Claude Code CLI + VS Code extension v2.1.218, checked 2026-07-28"

PORT = 8743
PORT_TRIES = 10
MAX_BODY = 64 * 1024

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
KEYBINDINGS_PATH = HOME / ".claude" / "keybindings.json"
POWERTOYS_DIR = HOME / "AppData" / "Local" / "Microsoft" / "PowerToys"
VSCODE_KEYBINDINGS = HOME / "AppData" / "Roaming" / "Code" / "User" / "keybindings.json"
WT_SETTINGS = [
    HOME / "AppData" / "Local" / "Packages" /
    "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
    HOME / "AppData" / "Local" / "Packages" /
    "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
]

# A page served by this server carries this token in a meta tag, and every API
# call must present it. Without it, any website open in any browser could POST to
# 127.0.0.1 and silently rewrite the user's keybindings — a cross-origin POST
# with a text/plain body needs no CORS preflight, so the browser would send it.
TOKEN = secrets.token_urlsafe(24)

LOG_PATH = ROOT / "hotkey-hq.log"


def log(msg):
    """Append to a log file. launch.bat runs pythonw.exe, which has no console,
    so without this every failure would be completely invisible."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def fatal(title, msg):
    """Report a startup failure the user can actually see."""
    log("FATAL: %s - %s" % (title, msg))
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, "Hotkey HQ - " + title, 0x10)
    except Exception:
        print(msg, file=sys.stderr)


def _strip_jsonc(text):
    """VS Code's keybindings.json is JSONC. Remove // and /* */ comments, keeping string
    literals intact, so json.loads can read it."""
    out, i, n = [], 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
            i += 1; continue
        if ch == '"':
            in_str = True; out.append(ch); i += 1; continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2; continue
        out.append(ch); i += 1
    # tolerate trailing commas
    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _read_jsonc(path):
    """Parse a JSON/JSONC file, or return None if it is missing or unreadable."""
    try:
        if not path.exists():
            return None
        return json.loads(_strip_jsonc(path.read_text(encoding="utf-8-sig")) or "null")
    except Exception as e:
        log("could not read %s: %s" % (path, e))
        return None


# ---------------------------------------------------------------- VS Code ----

def read_vscode():
    """The user's VS Code keybindings, split into what they added and what they
    switched off. A "-command" entry disables a built-in default, so the caller
    needs it to stop showing that default as if it were still live."""
    data = _read_jsonc(VSCODE_KEYBINDINGS)
    if not isinstance(data, list):
        return [], []
    added, removed = [], []
    for e in data:
        if not isinstance(e, dict) or not e.get("key"):
            continue
        cmd = str(e.get("command", ""))
        if cmd.startswith("-"):
            removed.append({"key": e["key"], "command": cmd[1:]})
        elif cmd:
            added.append(e)
    return added, removed


# -------------------------------------------------------------- PowerToys ----

# Virtual-key codes -> the names this app uses for keys.
VK = {
    8: "backspace", 9: "tab", 13: "enter", 27: "escape", 32: "space",
    33: "pageup", 34: "pagedown", 35: "end", 36: "home",
    37: "left", 38: "up", 39: "right", 40: "down", 45: "insert", 46: "delete",
    106: "*", 107: "+", 109: "-", 110: ".", 111: "/",
    186: ";", 187: "=", 188: ",", 189: "-", 190: ".", 191: "/", 192: "`",
    219: "[", 220: "\\", 221: "]", 222: "'",
}
for _i in range(48, 58):
    VK[_i] = chr(_i)
for _i in range(65, 91):
    VK[_i] = chr(_i).lower()
for _i in range(112, 136):
    VK[_i] = "f%d" % (_i - 111)


def _combo(node):
    """Turn a PowerToys shortcut object into e.g. 'win+shift+t'. None if unset."""
    if not isinstance(node, dict):
        return None
    code = node.get("code")
    if not code:  # 0 or missing means "no shortcut assigned"
        return None
    base = VK.get(code)
    if base is None:
        return None
    parts = []
    for flag in ("ctrl", "alt", "shift", "win"):
        if node.get(flag):
            parts.append(flag)
    order = [m for m in ("ctrl", "alt", "shift", "win") if m in parts]
    return "+".join(order + [base])


def _norm_module(name):
    """'Shortcut Guide' and 'ShortcutGuide' are the same module — PowerToys uses
    both spellings, one as a folder name and one as an enabled-map key."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def powertoys_enabled():
    """The user's enabled/disabled state per module, or None if it can't be read.
    A disabled module's hotkey is not pressed by anything, so reporting it as a
    conflict is just noise."""
    data = _read_jsonc(POWERTOYS_DIR / "settings.json")
    if not isinstance(data, dict):
        return None
    enabled = data.get("enabled")
    if not isinstance(enabled, dict):
        return None
    return {_norm_module(k): bool(v) for k, v in enabled.items()}


def read_powertoys():
    """The user's live PowerToys hotkeys, skipping modules they have switched off.
    Returns ([], 0) if PowerToys isn't installed."""
    if not POWERTOYS_DIR.is_dir():
        return [], 0
    enabled = powertoys_enabled()
    out, seen, skipped = [], set(), 0

    for f in sorted(POWERTOYS_DIR.glob("*/settings.json")):
        module = f.parent.name
        # Unknown modules default to enabled — better a stray row than a missing
        # conflict — but anything explicitly switched off is dropped.
        if enabled is not None and enabled.get(_norm_module(module), True) is False:
            skipped += 1
            continue
        data = _read_jsonc(f)
        if not isinstance(data, dict):
            continue
        props = data.get("properties", data)
        if not isinstance(props, dict):
            continue

        # Collect candidate shortcut nodes, preferring the user's binding over the default.
        picked = {}
        for name, node in props.items():
            inner = node.get("value") if isinstance(node, dict) and "value" in node else node
            combo = _combo(inner)
            if not combo:
                continue
            is_default = name.startswith("Default")
            label = name.replace("Default", "").replace("_", " ").replace("-", " ").strip()
            key = label.lower()
            if key in picked and is_default:
                continue  # never let a Default overwrite a real binding
            picked[key] = (combo, label, is_default)

        for combo, label, _is_default in picked.values():
            sig = (module, combo)
            if sig in seen:
                continue
            seen.add(sig)
            nice = label
            for junk in ("ActivationShortcut", "activation shortcut", "hotkey", "shortcut"):
                nice = nice.replace(junk, "")
            nice = " ".join(nice.split()).strip() or "Activate"
            out.append({"combo": combo, "module": module, "action": nice[:1].upper() + nice[1:]})
    return out, skipped


# ------------------------------------------------------- Windows Terminal ----

# Windows Terminal spells a few keys differently to the browser.
WT_KEY = {
    "pgup": "pageup", "pgdn": "pagedown", "esc": "escape", "del": "delete",
    "ins": "insert", "plus": "=", "minus": "-", "app": "menu",
}


def _wt_label(entry):
    """A readable name for a Windows Terminal action."""
    cmd = entry.get("id") or entry.get("command")
    if isinstance(cmd, dict):
        cmd = cmd.get("action") or "Action"
    cmd = str(cmd or "Action")
    cmd = cmd.split(".")[-1]                              # Terminal.CopyToClipboard -> CopyToClipboard
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", cmd).strip()  # CopyToClipboard -> Copy To Clipboard
    words = words[:1].upper() + words[1:].lower()
    return words or "Action"


def _wt_combo(keys):
    """Normalise one Windows Terminal key string, e.g. 'alt+shift+d'."""
    if not isinstance(keys, str) or not keys.strip():
        return None
    parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
    if not parts:
        return None
    base = parts.pop()
    base = WT_KEY.get(base, base)
    mods = [m for m in ("ctrl", "alt", "shift", "win") if m in parts]
    return "+".join(mods + [base])


def read_terminal():
    """The user's Windows Terminal key bindings — both the current `keybindings`
    schema and the older `actions` one, whichever their settings file uses."""
    for path in WT_SETTINGS:
        data = _read_jsonc(path)
        if not isinstance(data, dict):
            continue
        entries = []
        for block in ("keybindings", "actions"):
            val = data.get(block)
            if isinstance(val, list):
                entries.extend(x for x in val if isinstance(x, dict))
        out, seen = [], set()
        for e in entries:
            keys = e.get("keys")
            if isinstance(keys, list):
                keys = keys[0] if keys else None
            combo = _wt_combo(keys)
            if not combo or combo in seen:
                continue
            seen.add(combo)
            out.append({"combo": combo, "action": _wt_label(e)})
        if out:
            return out
    return []


# ------------------------------------------------------------------ HTTP ----

class Handler(BaseHTTPRequestHandler):
    server_version = "HotkeyHQ/" + VERSION

    # -- helpers ------------------------------------------------------------
    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        """A browser attaches Origin to any cross-site request. Our own page is
        same-origin, so anything else is somebody else's page talking to us."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)

    def _authorised(self):
        if not self._origin_ok():
            log("refused a request from origin %s" % self.headers.get("Origin"))
            self._send_json(403, {"ok": False, "error": "cross-origin request refused"})
            return False
        sent = self.headers.get("X-HotkeyHQ-Token", "")
        if not secrets.compare_digest(sent, TOKEN):
            self._send_json(403, {"ok": False, "error": "missing or bad token"})
            return False
        return True

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                html = (ROOT / "index.html").read_text(encoding="utf-8")
            except Exception as e:
                self.send_error(500, "index.html could not be read: %s" % e)
                return
            html = (html.replace("__HKHQ_TOKEN__", TOKEN)
                        .replace("__HKHQ_VERSION__", VERSION)
                        .replace("__HKHQ_VERIFIED__", VERIFIED_AGAINST))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/icon.ico":
            try:
                blob = (ROOT / "icon.ico").read_bytes()
            except Exception:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return

        # Open on purpose: this is how a second launch discovers that this
        # install is already running. It exposes nothing the caller can act on.
        if self.path == "/api/ping":
            self._send_json(200, {"app": APP_ID, "version": VERSION, "root": str(ROOT)})
            return

        if not self.path.startswith("/api/"):
            self.send_response(404); self.end_headers(); return
        if not self._authorised():
            return

        if self.path == "/api/keybindings":
            if KEYBINDINGS_PATH.exists():
                try:
                    # utf-8-sig tolerates a BOM, which PowerShell's -Encoding utf8 writes
                    config = json.loads(KEYBINDINGS_PATH.read_text(encoding="utf-8-sig"))
                except Exception as e:
                    self._send_json(200, {"bindings": [], "parseError": str(e)})
                    return
            else:
                config = {"bindings": []}
            self._send_json(200, config)

        elif self.path == "/api/vscode":
            added, removed = read_vscode()
            self._send_json(200, {"exists": VSCODE_KEYBINDINGS.exists(),
                                  "path": str(VSCODE_KEYBINDINGS),
                                  "bindings": added, "removals": removed})

        elif self.path == "/api/powertoys":
            try:
                shortcuts, skipped = read_powertoys()
                self._send_json(200, {"installed": POWERTOYS_DIR.is_dir(),
                                      "shortcuts": shortcuts, "skipped": skipped})
            except Exception as e:
                log("powertoys read failed: %s" % e)
                self._send_json(200, {"installed": False, "shortcuts": [], "skipped": 0,
                                      "error": str(e)})

        elif self.path == "/api/terminal":
            try:
                self._send_json(200, {"bindings": read_terminal()})
            except Exception as e:
                log("windows terminal read failed: %s" % e)
                self._send_json(200, {"bindings": [], "error": str(e)})

        else:
            self.send_response(404); self.end_headers()

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        if self.path not in ("/api/assign", "/api/unassign"):
            self.send_response(404); self.end_headers(); return
        if not self._authorised():
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "bad Content-Length"})
            return
        if length > MAX_BODY:
            self._send_json(413, {"ok": False, "error": "request too large"})
            return

        try:
            data = json.loads(self.rfile.read(length))
            context = data["context"]
            keystroke = data["keystroke"]
            action = data.get("action")
            force = bool(data.get("force"))
        except Exception as e:
            self._send_json(400, {"ok": False, "error": "bad request: %s" % e})
            return

        if not isinstance(keystroke, str) or not keystroke.strip():
            self._send_json(400, {"ok": False, "error": "keystroke is required"})
            return

        target = data.get("target", "claude")
        try:
            if target == "vscode":
                self._vscode_post(data, keystroke, action, force)
            else:
                self._claude_post(context, keystroke, action, force)
        except Exception as e:
            log("write failed: %s\n%s" % (e, traceback.format_exc()))
            self._send_json(500, {"ok": False, "error": str(e)})

    # -- VS Code ------------------------------------------------------------
    def _vscode_post(self, data, keystroke, action, force):
        entries, removals = read_vscode()
        # Keep the user's "-command" rows: they switch off a built-in default, and
        # rewriting the file without them would silently turn those defaults back on.
        keep_removals = [{"key": r["key"], "command": "-" + r["command"]} for r in removals]

        if self.path == "/api/unassign":
            before = len(entries)
            entries = [e for e in entries if e.get("key") != keystroke]
            self._write_vscode(entries + keep_removals)
            self._send_json(200, {"ok": True, "path": str(VSCODE_KEYBINDINGS),
                                  "removed": len(entries) < before})
            return

        if not action:
            self._send_json(400, {"ok": False, "error": "action is required"})
            return

        existing = [e for e in entries if e.get("key") == keystroke]
        if existing and not force:
            self._send_json(409, {"ok": False, "conflict": True,
                                  "existing": existing[0].get("command", "?")})
            return
        entries = [e for e in entries if e.get("key") != keystroke]
        entry = {"key": keystroke, "command": action}
        if data.get("when"):
            entry["when"] = data["when"]
        entries.append(entry)
        self._write_vscode(entries + keep_removals)
        self._send_json(200, {"ok": True, "path": str(VSCODE_KEYBINDINGS)})

    def _write_vscode(self, entries):
        """Write VS Code keybindings, backing up once so any comments in the
        original are recoverable (we reserialise as plain JSON)."""
        VSCODE_KEYBINDINGS.parent.mkdir(parents=True, exist_ok=True)
        backup = VSCODE_KEYBINDINGS.with_suffix(".json.hotkeyhq-backup")
        if VSCODE_KEYBINDINGS.exists() and not backup.exists():
            shutil.copy2(VSCODE_KEYBINDINGS, backup)
        VSCODE_KEYBINDINGS.write_text(json.dumps(entries, indent=4) + "\n", encoding="utf-8")

    # -- Claude Code --------------------------------------------------------
    def _claude_post(self, context, keystroke, action, force):
        if self.path == "/api/unassign":
            if not KEYBINDINGS_PATH.exists():
                self._send_json(200, {"ok": True, "path": str(KEYBINDINGS_PATH), "removed": False})
                return
            try:
                config = json.loads(KEYBINDINGS_PATH.read_text(encoding="utf-8-sig"))
            except Exception as e:
                self._send_json(500, {"ok": False,
                                      "error": "keybindings.json is not valid JSON: %s" % e})
                return
            removed = False
            for b in config.get("bindings", []):
                if b.get("context") == context and keystroke in b.get("bindings", {}):
                    del b["bindings"][keystroke]
                    removed = True
            # drop any context block left with no bindings
            config["bindings"] = [b for b in config.get("bindings", []) if b.get("bindings")]
            self._write_claude(config)
            self._send_json(200, {"ok": True, "path": str(KEYBINDINGS_PATH), "removed": removed})
            return

        if not action:
            self._send_json(400, {"ok": False, "error": "action is required"})
            return

        KEYBINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if KEYBINDINGS_PATH.exists():
            try:
                config = json.loads(KEYBINDINGS_PATH.read_text(encoding="utf-8-sig"))
            except Exception as e:
                self._send_json(500, {"ok": False,
                                      "error": "existing keybindings.json is not valid JSON: %s" % e})
                return
        else:
            config = {
                "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
                "$docs": "https://code.claude.com/docs/en/keybindings",
                "bindings": [],
            }
        config.setdefault("bindings", [])

        block = None
        for b in config["bindings"]:
            if b.get("context") == context:
                block = b
                break
        if block is None:
            block = {"context": context, "bindings": {}}
            config["bindings"].append(block)
        block.setdefault("bindings", {})

        existing = block["bindings"].get(keystroke)
        if existing is not None and existing != action and not force:
            self._send_json(409, {"ok": False, "conflict": True, "existing": existing})
            return

        block["bindings"][keystroke] = action
        self._write_claude(config)
        self._send_json(200, {"ok": True, "path": str(KEYBINDINGS_PATH)})

    def _write_claude(self, config):
        KEYBINDINGS_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def log_message(self, format, *args):
        pass


class Server(ThreadingHTTPServer):
    # The stdlib default is 1, which on Windows lets a second process bind a port
    # that is already in use — so the "is one already running?" check below used
    # to succeed twice and requests went to whichever instance Windows picked.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def ping(port, timeout=0.7):
    """Ask whoever owns this port to identify themselves."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def acquire():
    """Find a port to use.

    Returns (port, httpd). httpd is None when this exact install is already
    running — then we simply open another window onto it. A copy running from a
    different folder is left alone and we take the next port, so an old instance
    can never serve its stale interface to a newly launched one.
    """
    for port in range(PORT, PORT + PORT_TRIES):
        info = ping(port)
        if info is not None:
            if info.get("app") == APP_ID and info.get("root") == str(ROOT):
                log("reusing the instance already running on port %d" % port)
                return port, None
            continue                       # someone else's service — try the next port
        try:
            httpd = Server(("127.0.0.1", port), Handler)
        except OSError as e:
            log("port %d unavailable: %s" % (port, e))
            continue
        return port, httpd
    raise RuntimeError(
        "Ports %d-%d are all in use, so Hotkey HQ has nowhere to listen."
        % (PORT, PORT + PORT_TRIES - 1))


def main():
    port, httpd = acquire()
    if httpd is not None:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log("serving %s on port %d" % (ROOT, port))

    try:
        import webview
    except ImportError:
        fatal("Setup incomplete",
              "The pywebview package is missing, so the window cannot open.\n\n"
              "Run setup.bat once in:\n%s" % ROOT)
        return

    webview.create_window(
        "Hotkey HQ",
        "http://127.0.0.1:%d/" % port,
        width=1040,
        height=920,
        min_size=(640, 480),
    )
    webview.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fatal("Could not start", "%s\n\nDetails were written to:\n%s" % (exc, LOG_PATH))
        log(traceback.format_exc())
        sys.exit(1)
