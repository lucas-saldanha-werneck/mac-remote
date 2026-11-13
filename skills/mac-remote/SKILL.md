---
name: mac-remote
description: 'See and control this Mac through macOS Screen Sharing (VNC), even when the screen is locked (unlocks with the Mac password from the configured secret source, holds the session, acts, locks again). Use when the user is away from the Mac and an item on it needs a click, a popup closed, an app or a dialog handled; when they say "the screen is locked", "unlock the Mac", "click on the Mac", "what is on the screen?", "close that popup", "mac-remote"; or when another task is stuck on a macOS dialog that can only be solved on screen. Also to restart the Mac from far away with FileVault on ("restart the Mac", "reboot the Mac remotely").'
argument-hint: "[status|unlock|shot|click|move|type|keys|lock|restart]"
version: "0.4.2"
metadata:
  author: Lucas Saldanha Werneck <lucas@2olhares.com>
  tags: [remote, macos, vnc]
---

# mac-remote: the Mac's screen from a distance (local VNC)

## Purpose

Let Claude see and control the Mac's screen from a distance, even when it is locked, for the cases where only a click on screen solves the problem.

Everything goes through the `mac-remote` command (it is on the Bash tool's PATH while the plugin is enabled; full path: `${CLAUDE_PLUGIN_ROOT}/bin/mac-remote`). `mac-remote --help` lists every option.

If a command says a Python module is missing, run `mac-remote setup` once. If it says there is no password source, tell the user to configure `MAC_REMOTE_PASS_CMD` (see the plugin README). Do not try to find or read the password yourself.

## Instructions

1. `mac-remote status` to see whether the screen is locked and whether a holder is running.
2. `mac-remote unlock` if locked, then `mac-remote shot` and open the image with Read.
3. Act with `click`, `move`, `type` or `keys` (prefer `--app NAME`), then `shot` again to check.
4. Always finish with `mac-remote lock`. Never type or read the Mac password yourself.

## The fact that drives the design

macOS locks the screen again about 1 s after the VNC session that unlocked it disconnects. So `unlock` starts a **holder**: a detached process that keeps ONE VNC connection open and serves the other commands over a Unix socket (`holder.sock`, mode 600, same user only). Unlocking and disconnecting does not work.

## Flow

1. `mac-remote status`: locked?, seconds since the last local input, holder (pid, idle time, when it exits), displays, password source (not fetched).
2. `mac-remote unlock [--idle-timeout S]` (default 600): starts the holder, types the password, checks the screen really unlocked, then returns. If a holder already holds the session, it only re-arms the timeout.
3. `mac-remote shot` (or `shot --display 0` for one display): JPG of at most 1600 px; prints its path. **Open the image with Read** before clicking. Without a holder it opens a one-off connection (works locked or not, slower).
4. `click X Y` (pixels of the LAST shot image; `--right`, `--double`; `--raw` = framebuffer coordinates), `move X Y`, `type "text" --app TextEdit`, `keys cmd+tab return esc --app Finder`. With a holder they go through it. Without a holder and with the screen locked, they refuse.
   - Prefer **`--app NAME`**: brings the app to the front and refuses if another app stays in front.
5. `shot` again to check the result.
6. **Always finish with `mac-remote lock`**: the holder drops the connection and macOS locks by itself. If it does not, the command falls back to Ctrl+Cmd+Q over VNC, then `pmset displaysleepnow`.

## Restart

`mac-remote restart --dry-run` first: it checks FileVault, authenticated-restart support and that sudo accepts the password, and restarts nothing. Then `mac-remote restart --yes` only after the user said to restart now, in this conversation. With FileVault on it uses `fdesetup authrestart`, so the Mac does not stop at the disk password screen. Unsaved work in open apps can be lost: say so before `--yes`. After it, wait a few minutes and run `mac-remote status`. Do not use `--force` (someone at the Mac) unless the user said yes.

## Safety

- The holder exits (and the screen locks) when: `--idle-timeout` seconds pass with no command **and** no local input; the VNC connection drops; the screen gets locked some other way; or it gets SIGTERM.
- **Someone using the Mac = hands off.** click/move/type/keys and `lock` refuse if there was local input (not ours) in the last 20 s. Use `--force` only after the user said yes. VNC input also resets the system idle time, so mac-remote records the time of its own input (`state.json`) and only counts input after that as someone else's.
- If someone comes back while the holder is open, the holder logs "LOCAL ACTIVITY" in `holder.log` and stays connected, so the screen does not lock in their face. The idle timer restarts from their last input. Ask before running `lock` if they are at the Mac.
- `type` with `' " ` ~ ^` on a keyboard layout with dead keys, or with characters VNC cannot send (emoji), pastes instead: save every clipboard item and format, copy, Cmd+V, then restore them (not if something new was copied meanwhile). It refuses before touching the clipboard when the screen is locked or someone is at the Mac. Text equal to `$MAC_REMOTE_PASS` is never pasted.
- **Never** type a secret with `type`. The Mac password exists only inside the holder during `unlock` (and the holder drops it right after). Do not read it, print it, or send it anywhere.
- While the holder holds the session, anyone at the desk sees an unlocked Mac. Hold it only as long as needed.
- `unlock` refuses a password with `' " ` ~ ^` when the keyboard layout has dead keys. Tell the user; do not set `MAC_REMOTE_ALLOW_DEAD_KEY_PASSWORD` yourself.
- If `unlock` fails ("still locked"), run `shot` and look at the screen. It tries twice and stops. Do not retry in a loop: too many wrong passwords can lock the account.

## Known issues

- The first connection after a long idle period sometimes drops (`IncompleteReadError`); the command retries up to 4 times. This is normal.
- Keys only land reliably while the client is reading the framebuffer. The holder reads all the time; a one-off connection reads a full frame and taps Shift before acting.
- Lock state: `CGSSessionScreenIsLocked`. Local input: `HIDIdleTime` from `ioreg -c IOHIDSystem`.
- Files (default `~/.local/share/mac-remote/`, or `$MAC_REMOTE_HOME`): `holder.log` (events and exit reason), `holder.json`, `holder.sock`, `last-shot.json`, `state.json`, `shots/`.

## Prerequisites

- macOS with Screen Sharing turned on, and python3; the command exits with an error on other systems.
- Python packages installed by the setup command (asyncvnc, pillow, pyobjc-framework-Quartz).
- The Mac password in a configured secret source, so unlock can work.

## Limitations

- macOS only. Input commands refuse when someone used the Mac in the last 20 seconds, unless the user said yes to force.
- The holder exits and the screen locks after the idle timeout, when the VNC connection drops or when the screen is locked another way.
- A password with characters that need dead keys on the keyboard layout is refused by unlock.

## Troubleshooting

- Error "still locked" from unlock: it tries twice and stops. Run shot and look at the screen; never retry in a loop.
- Error IncompleteReadError on the first connection after a long idle: normal; the command retries up to 4 times.

## Examples

- User: "the screen is locked, close the popup on the Mac". Claude runs `mac-remote unlock`, takes a `shot`, clicks the popup's button, checks with another `shot`, then runs `mac-remote lock`.
- User: "what is on the screen?". Claude runs `mac-remote shot` and describes the image it opens.
- User: "restart the Mac". Claude runs `mac-remote restart --dry-run`, warns that unsaved work can be lost, and runs `mac-remote restart --yes` only after the user confirms.
