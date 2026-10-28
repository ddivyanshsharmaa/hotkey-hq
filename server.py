"""Hotkey HQ — local server and config readers.

Everything here is local to this machine. The server binds 127.0.0.1, serves the
single-page interface, and reads the keyboard configuration of whoever is running
it: their Claude Code keybindings, their VS Code keybindings, their PowerToys
hotkeys and their Windows Terminal bindings.

It writes only when the user asks: the Claude Code and VS Code keybinding files
on Assign or Remove, a PowerToys hotkey when they resolve a conflict, and a
cheat sheet into their Obsidian vault on export. Every one of those takes a
backup first, and a snapshot of all keybinding files is kept in ./snapshots so
any change can be rolled back.
"""
import ctypes
import datetime
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from platform_config import config_paths, parse_kitty, read_terminals

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
PLATFORM = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
IS_WINDOWS = sys.platform == "win32"
NATIVE_PATHS = {} if IS_WINDOWS else config_paths(sys.platform, HOME)
KEYBINDINGS_PATH = HOME / ".claude" / "keybindings.json"
POWERTOYS_DIR = HOME / "AppData" / "Local" / "Microsoft" / "PowerToys"
VSCODE_KEYBINDINGS = HOME / "AppData" / "Roaming" / "Code" / "User" / "keybindings.json"
WT_SETTINGS = [
    HOME / "AppData" / "Local" / "Packages" /
    "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
    HOME / "AppData" / "Local" / "Packages" /
    "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
]
OBSIDIAN_REGISTRY = HOME / "AppData" / "Roaming" / "obsidian" / "obsidian.json"
if not IS_WINDOWS:
    KEYBINDINGS_PATH = NATIVE_PATHS["claude"]
    VSCODE_KEYBINDINGS = NATIVE_PATHS["vscode"]
    OBSIDIAN_REGISTRY = NATIVE_PATHS["obsidian"]
    WT_SETTINGS = []

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


VK_REVERSE = {}
for _code, _name in VK.items():
    # '-' and '.' and '/' appear twice in VK (main row and numpad); the main-row
    # code wins because that is what a user means by "minus".
    if _name not in VK_REVERSE or _code < VK_REVERSE[_name]:
        VK_REVERSE[_name] = _code


def _node_from_combo(combo):
    """Turn 'win+shift+c' back into the shape PowerToys stores on disk.
    Returns None if any part of it has no virtual-key code."""
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    if not parts:
        return None
    base = parts[-1]
    mods = set(parts[:-1])
    if not set(mods).issubset({"ctrl", "alt", "shift", "win"}):
        return None
    code = VK_REVERSE.get(base)
    if code is None:
        return None
    return {"win": "win" in mods, "ctrl": "ctrl" in mods,
            "alt": "alt" in mods, "shift": "shift" in mods,
            "code": code, "key": ""}


def rebind_powertoys(module, prop, combo):
    """Point one PowerToys hotkey at a new combination. Returns (ok, message)."""
    if not IS_WINDOWS:
        return False, "PowerToys is only available on Windows"
    target = POWERTOYS_DIR / module / "settings.json"
    if not target.is_file():
        return False, "no settings file for module %r" % module
    node = _node_from_combo(combo)
    if node is None:
        return False, "PowerToys cannot express the combination %r" % combo
    data = _read_jsonc(target)
    if not isinstance(data, dict):
        return False, "settings.json for %s is not readable JSON" % module
    props = data.get("properties") if isinstance(data.get("properties"), dict) else data
    if prop not in props:
        return False, "module %s has no setting called %r" % (module, prop)

    # Some hotkeys are stored bare, others wrapped in {"value": {...}}.
    existing = props[prop]
    if isinstance(existing, dict) and "value" in existing:
        props[prop]["value"] = node
    else:
        props[prop] = node

    backup = target.with_suffix(".json.hotkeyhq-backup")
    if not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    log("rebound PowerToys %s/%s to %s" % (module, prop, combo))
    return True, str(target)


def restart_powertoys():
    """PowerToys reads most hotkeys at module start, so a file edit alone is not
    reliably picked up. Returns (ok, message)."""
    if not IS_WINDOWS:
        return False, "PowerToys is only available on Windows"
    exe = HOME / "AppData" / "Local" / "PowerToys" / "PowerToys.exe"
    if not exe.is_file():
        return False, "PowerToys.exe not found at %s" % exe
    try:
        subprocess.run(["taskkill", "/F", "/IM", "PowerToys.exe", "/T"],
                       capture_output=True, timeout=15)
        time.sleep(1.5)
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        log("restarted PowerToys")
        return True, "PowerToys restarted"
    except Exception as e:
        return False, str(e)


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
    if not IS_WINDOWS or not POWERTOYS_DIR.is_dir():
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

        # Collect candidate shortcut nodes, preferring the user's binding over the
        # default. Defaults are kept in their own map: both names reduce to the
        # same label, so storing them together loses whichever arrives second —
        # and the vendor default is what the conflict resolver offers as a fix.
        picked, defaults = {}, {}
        for name, node in props.items():
            inner = node.get("value") if isinstance(node, dict) and "value" in node else node
            combo = _combo(inner)
            if not combo:
                continue
            is_default = name.startswith("Default")
            label = name.replace("Default", "").replace("_", " ").replace("-", " ").strip()
            key = label.lower()
            if is_default:
                defaults[key] = combo
                if key in picked:
                    continue          # a real binding already won this slot
            picked[key] = (combo, label, is_default, name)

        for key, (combo, label, _is_default, prop) in picked.items():
            sig = (module, combo)
            if sig in seen:
                continue
            seen.add(sig)
            nice = label
            for junk in ("ActivationShortcut", "activation shortcut", "hotkey", "shortcut"):
                nice = nice.replace(junk, "")
            nice = " ".join(nice.split()).strip() or "Activate"
            # `prop` and `default` let the conflict resolver rebind this exact
            # setting, and offer the vendor default as the suggested new home.
            # PowerToys reads the user-override slot, so strip a "Default"
            # prefix when telling the resolver where to write.
            write_prop = prop[len("Default"):] if prop.startswith("Default") else prop
            entry = {"combo": combo, "module": module, "prop": write_prop,
                     "action": nice[:1].upper() + nice[1:]}
            dflt = defaults.get(key)
            if dflt and dflt != combo:
                entry["default"] = dflt
            out.append(entry)
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
    if not IS_WINDOWS:
        return read_terminals(NATIVE_PATHS)[0]
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

# ------------------------------------------------- snapshots and history ----

SNAP_DIR = ROOT / "snapshots"


def _tracked_files():
    """Every keybinding file this machine has, as {label: Path}."""
    files = {
        "claude": KEYBINDINGS_PATH,
        "vscode": VSCODE_KEYBINDINGS,
    }
    if not IS_WINDOWS:
        # iTerm2 preferences may be binary plists; the text snapshot format
        # deliberately covers only kitty, Claude, VS Code and Obsidian here.
        files["kitty"] = NATIVE_PATHS["kitty"]
    for p in WT_SETTINGS:
        if p.is_file():
            files["terminal"] = p
            break
    vaults = obsidian_vaults()
    if vaults:
        files["obsidian"] = Path(vaults[0]["path"]) / ".obsidian" / "hotkeys.json"
    if IS_WINDOWS and POWERTOYS_DIR.is_dir():
        for f in sorted(POWERTOYS_DIR.glob("*/settings.json")):
            files["powertoys/" + f.parent.name] = f
    return {k: v for k, v in files.items() if v and v.is_file()}


def take_snapshot(reason="manual"):
    """Copy every keybinding file into a timestamped folder. Skips writing a new
    one when nothing has changed, so the history stays meaningful."""
    files = _tracked_files()
    if not files:
        return None, "no keybinding files found to snapshot"

    payload = {}
    for label, path in files.items():
        try:
            payload[label] = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue

    previous = latest_snapshot()
    if previous and previous.get("files") == payload:
        return previous, "no change since the last snapshot"

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = {"id": stamp, "taken": stamp, "reason": reason,
            "paths": {k: str(v) for k, v in files.items()}, "files": payload}
    (SNAP_DIR / (stamp + ".json")).write_text(json.dumps(snap, indent=1), encoding="utf-8")
    log("snapshot %s (%s), %d files" % (stamp, reason, len(payload)))
    return snap, str(SNAP_DIR / (stamp + ".json"))


def list_snapshots():
    if not SNAP_DIR.is_dir():
        return []
    out = []
    for f in sorted(SNAP_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d.get("id", f.stem), "taken": d.get("taken"),
                        "reason": d.get("reason", ""), "files": sorted(d.get("files", {}).keys())})
        except Exception:
            continue
    return out


def load_snapshot(snap_id):
    f = SNAP_DIR / (str(snap_id) + ".json")
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_snapshot():
    snaps = sorted(SNAP_DIR.glob("*.json"), reverse=True) if SNAP_DIR.is_dir() else []
    if not snaps:
        return None
    try:
        return json.loads(snaps[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def restore_snapshot(snap_id, which=None):
    """Put files back as they were. Takes a snapshot of the current state first,
    so restoring is itself undoable."""
    snap = load_snapshot(snap_id)
    if not snap:
        return False, "snapshot %r not found" % snap_id
    take_snapshot("before restoring " + str(snap_id))

    restored, failed = [], []
    for label, text in (snap.get("files") or {}).items():
        if which and label != which:
            continue
        path = Path((snap.get("paths") or {}).get(label, ""))
        if not str(path):
            failed.append(label)
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            restored.append(label)
        except Exception as e:
            failed.append("%s (%s)" % (label, e))
    log("restored %s from %s" % (", ".join(restored) or "nothing", snap_id))
    return (not failed), {"restored": restored, "failed": failed}


def _hotkey_map():
    """Flatten every source into {combo: 'where — what'} so two points in time
    can be compared by meaning rather than by raw file text."""
    out = {}
    try:
        rows, _ = read_powertoys()
        for r in rows:
            out["PowerToys " + r["combo"]] = "%s — %s" % (r["module"], r.get("action", ""))
    except Exception:
        pass
    try:
        for b in read_vscode():
            if isinstance(b, dict) and b.get("key"):
                out["VS Code " + str(b["key"])] = str(b.get("command", ""))
    except Exception:
        pass
    try:
        cfg = _read_jsonc(KEYBINDINGS_PATH) or {}
        for block in cfg.get("bindings", []) or []:
            for k, v in (block.get("bindings") or {}).items():
                out["Claude Code " + k] = "%s (%s)" % (v, block.get("context", ""))
    except Exception:
        pass
    try:
        rows, _v = read_obsidian()
        for r in rows:
            out["Obsidian " + r["combo"]] = r.get("action", "")
    except Exception:
        pass
    return out


def history():
    """What changed between consecutive snapshots, in shortcut terms."""
    snaps = sorted(SNAP_DIR.glob("*.json")) if SNAP_DIR.is_dir() else []
    entries = []
    for f in snaps:
        try:
            entries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    if len(entries) < 2:
        return []

    def flatten(snap):
        """Rebuild the shortcut map from a snapshot's stored file text."""
        m = {}
        for label, text in (snap.get("files") or {}).items():
            if label == "kitty":
                for row in parse_kitty(text)[0]:
                    m["kitty " + row["action"]] = row["combo"]
                continue
            try:
                data = json.loads(_strip_jsonc(text) or "{}")
            except Exception:
                continue
            if label.startswith("powertoys/"):
                module = label.split("/", 1)[1]
                props = data.get("properties", data)
                if isinstance(props, dict):
                    for name, node in props.items():
                        inner = node.get("value") if isinstance(node, dict) and "value" in node else node
                        c = _combo(inner)
                        if c and not name.startswith("Default"):
                            m["PowerToys %s/%s" % (module, name)] = c
            elif label == "vscode" and isinstance(data, list):
                for b in data:
                    if isinstance(b, dict) and b.get("key"):
                        m["VS Code " + str(b.get("command", "?"))] = str(b["key"])
            elif label == "claude" and isinstance(data, dict):
                for block in data.get("bindings", []) or []:
                    for k, v in (block.get("bindings") or {}).items():
                        m["Claude Code " + str(v)] = k
            elif label == "obsidian" and isinstance(data, dict):
                for cmd, binds in data.items():
                    if isinstance(binds, list) and binds:
                        b = binds[0]
                        mods = [OBS_MOD.get(str(x).lower()) for x in (b.get("modifiers") or [])]
                        mods = [x for x in mods if x]
                        order = [x for x in ("ctrl", "alt", "shift", "win") if x in mods]
                        if b.get("key"):
                            m["Obsidian " + cmd] = "+".join(order + [str(b["key"]).lower()])
        return m

    out = []
    for older, newer in zip(entries, entries[1:]):
        a, b = flatten(older), flatten(newer)
        changes = []
        for k in sorted(set(a) | set(b)):
            if a.get(k) == b.get(k):
                continue
            if k not in a:
                changes.append({"what": k, "kind": "added", "to": b[k]})
            elif k not in b:
                changes.append({"what": k, "kind": "removed", "from": a[k]})
            else:
                changes.append({"what": k, "kind": "moved", "from": a[k], "to": b[k]})
        if changes:
            out.append({"at": newer.get("taken"), "reason": newer.get("reason", ""),
                        "changes": changes})
    out.reverse()
    return out


# ------------------------------------------------------------- Obsidian ----

def obsidian_vaults():
    """Obsidian keeps a registry of every vault, so we never hardcode a path.
    Most-recently-opened first."""
    reg = _read_jsonc(OBSIDIAN_REGISTRY)
    if not isinstance(reg, dict):
        return []
    vaults = reg.get("vaults")
    if not isinstance(vaults, dict):
        return []
    rows = []
    for v in vaults.values():
        p = v.get("path")
        if p and Path(p).is_dir():
            rows.append({"path": p, "open": bool(v.get("open")), "ts": v.get("ts") or 0})
    rows.sort(key=lambda r: (not r["open"], -r["ts"]))
    return rows


# Obsidian writes "Mod", which is Ctrl on Windows and Cmd on macOS.
OBS_MOD = {"mod": "ctrl", "ctrl": "ctrl", "control": "ctrl",
           "alt": "alt", "shift": "shift", "meta": "win"}
if PLATFORM == "macos":
    OBS_MOD.update({"mod": "win", "cmd": "win", "command": "win", "option": "alt"})


def read_obsidian():
    """The user's *customised* Obsidian hotkeys. Obsidian only stores overrides,
    so anything left at its factory binding is covered by the defaults table in
    the page instead."""
    vaults = obsidian_vaults()
    if not vaults:
        return [], None
    vault = vaults[0]["path"]
    data = _read_jsonc(Path(vault) / ".obsidian" / "hotkeys.json")
    if not isinstance(data, dict):
        return [], vault
    out = []
    for command, binds in data.items():
        if not isinstance(binds, list):
            continue
        for b in binds:
            if not isinstance(b, dict) or not b.get("key"):
                continue
            mods = [OBS_MOD.get(str(m).lower()) for m in (b.get("modifiers") or [])]
            mods = [m for m in mods if m]
            order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
            key = str(b["key"]).lower()
            nice = command.split(":")[-1].replace("-", " ").strip()
            out.append({"combo": "+".join(order + [key]),
                        "command": command,
                        "action": (nice[:1].upper() + nice[1:]) or command})
    return out, vault


def export_obsidian(markdown):
    """Write the cheat sheet into the user's vault. Returns (ok, message)."""
    if not isinstance(markdown, str) or not markdown.strip():
        return False, "nothing to write"
    vaults = obsidian_vaults()
    if not vaults:
        return False, "no Obsidian vault found on this machine"
    vault = Path(vaults[0]["path"])

    # Respect a PARA layout if the vault uses one, otherwise drop it at the root.
    folder = vault
    for candidate in ("2 - Areas", "Areas", "3 - Resources", "Resources"):
        if (vault / candidate).is_dir():
            folder = vault / candidate
            break
    target = folder / "Keyboard Shortcuts (Hotkey HQ).md"
    try:
        target.write_text(markdown, encoding="utf-8")
    except Exception as e:
        return False, "could not write %s: %s" % (target, e)
    log("exported cheat sheet to %s" % target)
    return True, str(target)


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
                        .replace("__HKHQ_VERIFIED__", VERIFIED_AGAINST)
                        .replace("__HKHQ_PLATFORM__", PLATFORM)
                        .replace("/*__HKHQ_PLATFORM_DATA__*/", "" if IS_WINDOWS else
                                 (ROOT / "platform_data.js").read_text(encoding="utf-8")))
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
                self._send_json(200, {"installed": IS_WINDOWS and POWERTOYS_DIR.is_dir(),
                                      "shortcuts": shortcuts, "skipped": skipped})
            except Exception as e:
                log("powertoys read failed: %s" % e)
                self._send_json(200, {"installed": False, "shortcuts": [], "skipped": 0,
                                      "error": str(e)})

        elif self.path == "/api/snapshots":
            self._send_json(200, {"snapshots": list_snapshots()})
            return

        elif self.path == "/api/history":
            try:
                self._send_json(200, {"history": history()})
            except Exception as e:
                log("history failed: %s" % e)
                self._send_json(200, {"history": [], "error": str(e)})
            return

        elif self.path == "/api/obsidian":
            try:
                rows, vault = read_obsidian()
                self._send_json(200, {"installed": vault is not None,
                                      "vault": vault, "shortcuts": rows})
            except Exception as e:
                log("obsidian read failed: %s" % e)
                self._send_json(200, {"installed": False, "vault": None,
                                      "shortcuts": [], "error": str(e)})
            return

        elif self.path == "/api/terminal":
            try:
                if IS_WINDOWS:
                    self._send_json(200, {"bindings": read_terminal()})
                else:
                    rows, notes = read_terminals(NATIVE_PATHS)
                    self._send_json(200, {"bindings": rows, "notes": notes})
            except Exception as e:
                log("windows terminal read failed: %s" % e)
                self._send_json(200, {"bindings": [], "error": str(e)})

        else:
            self.send_response(404); self.end_headers()

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        if self.path not in ("/api/assign", "/api/unassign",
                             "/api/powertoys/rebind", "/api/powertoys/restart",
                             "/api/export/obsidian",
                             "/api/snapshot", "/api/restore"):
            self.send_response(404); self.end_headers(); return
        if not self._authorised():
            return

        if not IS_WINDOWS and self.path.startswith("/api/powertoys/"):
            self._send_json(400, {"ok": False, "error": "PowerToys is only available on Windows"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "bad Content-Length"})
            return
        if length > MAX_BODY:
            self._send_json(413, {"ok": False, "error": "request too large"})
            return
        raw = self.rfile.read(length) if length else b"{}"

        # ---- backup and restore ----
        if self.path == "/api/snapshot":
            snap, msg = take_snapshot("manual")
            self._send_json(200, {"ok": snap is not None, "message": msg,
                                  "id": snap.get("id") if snap else None})
            return

        if self.path == "/api/restore":
            try:
                d = json.loads(raw)
                ok, detail = restore_snapshot(d["id"], d.get("which"))
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "bad request: %s" % e})
                return
            self._send_json(200 if ok else 500,
                            {"ok": ok, "detail": detail,
                             "note": "A snapshot of the previous state was taken first, "
                                     "so this restore can itself be undone."})
            return

        # ---- PowerToys conflict resolver ----
        if self.path == "/api/powertoys/restart":
            ok, msg = restart_powertoys()
            self._send_json(200 if ok else 500, {"ok": ok, "message": msg})
            return

        if self.path == "/api/powertoys/rebind":
            try:
                d = json.loads(raw)
                ok, msg = rebind_powertoys(d["module"], d["prop"], d["combo"])
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "bad request: %s" % e})
                return
            self._send_json(200 if ok else 400,
                            {"ok": ok, "message": msg,
                             "note": "PowerToys reads its hotkeys at module start, "
                                     "so restart PowerToys for this to take effect."})
            return

        # ---- Obsidian cheat sheet ----
        if self.path == "/api/export/obsidian":
            try:
                d = json.loads(raw)
                ok, msg = export_obsidian(d.get("markdown", ""))
            except Exception as e:
                self._send_json(400, {"ok": False, "error": "bad request: %s" % e})
                return
            self._send_json(200 if ok else 500, {"ok": ok, "message": msg})
            return

        try:
            data = json.loads(raw)
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
        # Snapshot on every launch so a history builds up without being asked.
        # take_snapshot() is a no-op when nothing has changed, so this stays
        # cheap and the timeline only shows real edits.
        try:
            take_snapshot("app launched")
        except Exception as e:
            log("startup snapshot failed: %s" % e)

    try:
        import webview
    except ImportError:
        fatal("Setup incomplete",
              "The pywebview package is missing, so the window cannot open.\n\n"
              "Run %s once in:\n%s" % ("setup.bat" if IS_WINDOWS else "bash setup.sh", ROOT))
        return

    webview.create_window(
        "Hotkey HQ",
        "http://127.0.0.1:%d/" % port,
        width=1040,
        height=920,
        min_size=(640, 480),
    )
    if IS_WINDOWS:
        webview.start()
    else:
        webview.start(gui="cocoa" if PLATFORM == "macos" else "gtk")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fatal("Could not start", "%s\n\nDetails were written to:\n%s" % (exc, LOG_PATH))
        log(traceback.format_exc())
        sys.exit(1)
