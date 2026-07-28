# Hotkey HQ

Every keyboard shortcut competing for a key on your Windows machine — Claude Code,
VS Code, Windows itself, PowerToys, Windows Terminal, and your own bindings — on a
single keyboard you can press keys against.

It answers the question you actually have when a shortcut misbehaves: **who is
eating this key?**

## Setup

1. Run **`setup.bat`** once. It needs Python 3 and creates a private `.venv`
   folder with one dependency ([pywebview](https://pywebview.flowrl.com/)).
2. Run **`launch.bat`** to start it. It opens as its own desktop window.

To get a Start Menu / Desktop icon, right-click `launch.bat` → Send to → Desktop,
then set the icon to `icon.ico` in the shortcut's Properties.

If nothing appears when you launch it, open `hotkey-hq.log` in this folder — every
startup failure is recorded there, and anything fatal also shows a message box.

## What it reads from your machine

Nothing is bundled about *your* setup — it is all read live, every time the page
loads, from the config files you already have:

| Source | Where it comes from |
| --- | --- |
| Your Claude Code bindings | `~/.claude/keybindings.json` |
| Your VS Code bindings | `%APPDATA%\Code\User\keybindings.json`, including `-command` entries that switch a default off |
| Your PowerToys hotkeys | `%LOCALAPPDATA%\Microsoft\PowerToys\*\settings.json` — **modules you have disabled are ignored**, so a switched-off module is never reported as stealing a key |
| Your Windows Terminal bindings | Windows Terminal's `settings.json` (stable or Preview) |

The Claude Code, VS Code, Windows, text-editing and PowerShell *defaults* are a
hand-maintained table. The version it was checked against is printed at the bottom
of the app, so you can tell when it has gone stale.

## What it does

- **Press any shortcut** and it names every layer that claims it, ranked by which
  one actually wins. `Ctrl+C` is three different things depending on focus.
- **Hold Ctrl / Alt / Shift** and the board lights up every key that combination
  unlocks, coloured by the app that owns it.
- **Click a key** for every combination that uses it.
- **Capture mode** (on by default) swallows shortcuts so they get explained
  instead of executed. Turn it off when you want Ctrl+C to actually copy. Tab is
  never swallowed, so the app stays usable without a mouse.
- **Find a free combo** — this finds combinations nothing on the machine claims.
- **Assign bindings** to Claude Code or VS Code, and remove them again.
- **Conflict detection** — flags where an enabled PowerToys module silently
  shadows an app shortcut.

## Keyboard layouts

Pick your board from the dropdown: laptop with numpad, laptop without, tenkeyless,
or 65%. The **Fn** toggle adds the Fn key and marks F1–F12 as shared with hardware
controls.

## What it deliberately cannot do

Some keys never reach any application, so no software can intercept them:

- **`Win+`anything, `Alt+Tab`, `Alt+F4`, `Ctrl+Alt+Del`, `Ctrl+Shift+Esc`** —
  Windows claims these before any app sees them.
- **PowerToys hotkeys** — PowerToys uses a system-wide keyboard hook that runs
  ahead of the focused window. The app lists them and flags the conflicts, but
  cannot stop them firing.
- **The `Fn` key** — resolved inside the keyboard's own firmware. It never sends
  anything to Windows at all. Click the Fn key in the app for the full
  explanation.

Beating those would require installing a global low-level keyboard hook — the
same mechanism keyloggers use, capturing every keystroke in every application.
That is not a reasonable trade for a reference tool, so it isn't done. Capture
only works while the Hotkey HQ window has focus.

## Privacy and safety

Everything is local. The app runs a small HTTP server bound to `127.0.0.1` and
nothing is sent anywhere.

Because a local server is reachable by any web page your browser has open, every
API call must present a one-time token that the server injects into the page as it
serves it, and requests carrying another site's `Origin` are refused. A website
cannot use this app to touch your keybindings.

It writes only to the two keybinding files above, and only when you click Assign
or Remove — taking a one-time backup of the VS Code one first, since that file may
contain comments that get lost when it is rewritten as plain JSON.

## Running it twice

Launching again while it is already running just opens another window onto the
same instance. A copy running from a *different* folder is left alone and the new
one takes the next free port, so an old install can never serve you its stale
interface.

## Files

| File | What it is |
| --- | --- |
| `index.html` | The whole interface — layout data, shortcut database, all logic |
| `server.py` | Local server, config readers/writers, opens the native window |
| `requirements.txt` | The single pinned dependency |
| `setup.bat` | One-time environment setup |
| `launch.bat` | Starts the app |
| `icon.ico` | App icon |
| `hotkey-hq.log` | Written at runtime; startup failures land here |
