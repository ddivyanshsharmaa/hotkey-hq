"""macOS/Linux config locations and read-only terminal adapters (stdlib only).

Windows keeps its original readers in server.py. No shell commands, config
execution, recursive searches, or included config files are used here.
"""
import os
import plistlib
import shlex
from pathlib import Path


def config_paths(platform, home, env=None):
    env = os.environ if env is None else env
    home = Path(home)
    xdg = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    if not xdg.is_absolute():
        xdg = home / ".config"
    if platform == "darwin":
        app = home / "Library" / "Application Support"
        return {"claude": home / ".claude" / "keybindings.json",
                "vscode": app / "Code" / "User" / "keybindings.json",
                "obsidian": app / "obsidian" / "obsidian.json",
                "kitty": xdg / "kitty" / "kitty.conf",
                "iterm2": home / "Library" / "Preferences" / "com.googlecode.iterm2.plist"}
    if platform.startswith("linux"):
        return {"claude": home / ".claude" / "keybindings.json",
                "vscode": xdg / "Code" / "User" / "keybindings.json",
                "obsidian": xdg / "obsidian" / "obsidian.json",
                "kitty": xdg / "kitty" / "kitty.conf"}
    raise ValueError("unsupported platform: " + platform)


MODS = {"control": "ctrl", "ctrl": "ctrl", "alt": "alt", "opt": "alt",
        "option": "alt", "shift": "shift", "super": "win", "cmd": "win",
        "command": "win", "meta": "win", "win": "win"}
KEYS = {"esc": "escape", "return": "enter", "pgup": "pageup", "pgdn": "pagedown",
        "page_up": "pageup", "page_down": "pagedown", "backslash": "\\",
        "minus": "-", "equal": "=", "comma": ",", "period": ".",
        "slash": "/", "semicolon": ";", "apostrophe": "'", "grave": "`",
        "left_bracket": "[", "right_bracket": "]"}
BASES = {"escape", "enter", "tab", "space", "backspace", "delete", "insert",
         "home", "end", "pageup", "pagedown", "up", "down", "left", "right"}
BASES.update("f%d" % i for i in range(1, 13))


def terminal_combo(value, kitty_mod="ctrl+shift"):
    """The supported single-key kitty subset. Unknown syntax is never guessed."""
    parts = value.lower().replace("kitty_mod", kitty_mod.lower()).split("+")
    base = KEYS.get(parts[-1], parts[-1])
    if not (base in BASES or len(base) == 1 and base.isascii() and base.isprintable()):
        return None
    if not base or base == "+" or any(p not in MODS for p in parts[:-1]):
        return None
    mods = {MODS[p] for p in parts[:-1]}
    return "+".join([m for m in ("ctrl", "alt", "shift", "win") if m in mods] + [base])


def parse_kitty(text):
    rows, kitty_mod, skipped = {}, "ctrl+shift", 0
    # kitty_mod applies to all maps, even those above its definition.
    for line in text.splitlines():
        words = line.strip().split()
        if len(words) == 2 and words[0] == "kitty_mod":
            kitty_mod = words[1]
    for line in text.splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            skipped += 1
            continue
        if not words:
            continue
        if words[0] in ("include", "globinclude", "envinclude", "geninclude"):
            skipped += 1
        elif words[:2] == ["clear_all_shortcuts", "yes"]:
            rows.clear()
        elif words[0] == "map":
            combo = terminal_combo(words[1], kitty_mod) if len(words) >= 2 else None
            if combo is None:
                skipped += 1
                continue
            if len(words) == 2 or words[2] in ("no_op", "no_action", "noop"):
                rows.pop(combo, None)
            else:
                # Action arguments can contain arbitrary text. Only show its name.
                rows[combo] = {"combo": combo, "action": words[2], "source": "kitty"}
    return list(rows.values()), skipped


COCOA_KEYS = {0xF700: "up", 0xF701: "down", 0xF702: "left", 0xF703: "right",
              0xF727: "insert", 0xF728: "delete", 0xF729: "home", 0xF72B: "end",
              0xF72C: "pageup", 0xF72D: "pagedown", 0x7F: "backspace",
              0x1B: "escape", 0x09: "tab", 0x0D: "enter", 0x20: "space"}
COCOA_KEYS.update({0xF704 + i: "f%d" % (i + 1) for i in range(12)})


def iterm_combo(value):
    try:
        parts = [int(p, 16) for p in value.split("-")]
        if len(parts) not in (2, 3, 4) or len(parts) == 4 and parts[3] != 1:
            return None
        char, flags = parts[:2]
        # Leader/tmux and numeric-pad identities are not regular main keys.
        if flags & ~0x3E0000 or flags & 0x200000 and char not in range(0xF700, 0xF704):
            return None
        base = COCOA_KEYS.get(char) or chr(char).lower()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None
    mods = [name for mask, name in ((0x40000, "ctrl"), (0x80000, "alt"),
                                   (0x20000, "shift"), (0x100000, "win")) if flags & mask]
    return terminal_combo("+".join(mods + [base]))


def parse_iterm(data):
    if not isinstance(data, dict):
        return [], 1
    groups = [("global", data.get("GlobalKeyMap", {}))]
    for profile in data.get("New Bookmarks", []) if isinstance(data.get("New Bookmarks", []), list) else []:
        if isinstance(profile, dict):
            groups.append((str(profile.get("Name", "profile")), profile.get("Keyboard Map", {})))
    rows, skipped = [], 0
    for scope, mapping in groups:
        if not isinstance(mapping, dict):
            skipped += 1
            continue
        for key, action in mapping.items():
            combo = iterm_combo(key)
            if not combo or not isinstance(action, dict) or not isinstance(action.get("Action"), int):
                skipped += 1
                continue
            rows.append({"combo": combo, "action": "iTerm2 action %d (%s)" % (action["Action"], scope),
                         "source": "iTerm2"})
    return rows, skipped


def read_terminals(paths):
    rows, notes = [], []
    for name in ("kitty", "iterm2"):
        path = paths.get(name)
        if path is None or not path.is_file():
            continue
        try:
            if name == "kitty":
                found, skipped = parse_kitty(path.read_text(encoding="utf-8-sig"))
            else:
                found, skipped = parse_iterm(plistlib.loads(path.read_bytes()))
            rows.extend(found)
            if skipped:
                notes.append("%s: %d unsupported entries/includes skipped" % (name, skipped))
        except Exception as e:
            notes.append("%s: could not read config (%s)" % (name, type(e).__name__))
    notes.append("Terminal rows are saved overrides, not active-session detection. "
                 "kitty includes, conditional/sequence maps and iTerm2 custom preference folders are not read. "
                 "Physical-key/modifier remapping and tmux/leader bindings are not modelled. "
                 "Terminal.app, GNOME Terminal and Konsole custom bindings are not imported.")
    return rows, notes
