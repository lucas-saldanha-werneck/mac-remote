# mac-remote

A Claude Code plugin that lets Claude see and control your Mac through macOS's own Screen Sharing (VNC), even when the screen is locked. It is meant for when you are away from the Mac (for example on your phone with Remote Control) and a task is stuck on a popup, a dialog or an app that needs a click.

Claude can unlock the screen with your Mac password, take screenshots, click, type and press keys, then lock the screen again. It can also restart the Mac without it stopping at the FileVault disk password screen (`mac-remote restart`).

## Warning: this gives Claude full control of your Mac

Read this before you install.

- While the plugin is enabled, Claude can unlock your Mac and use the mouse and keyboard like a person sitting at it. Anything you can do on the Mac, Claude can do.
- Claude can read the password source you configure. mac-remote keeps the password out of the conversation: it never prints it, logs it, writes it to disk, pastes it, or puts it on a command line. But Claude could run the same password command itself. The plugin does not stop that.
- While mac-remote holds the session, the Mac is unlocked. Anyone at your desk can use it.
- Screenshots are saved to disk (in a folder only your user can read) and may show private things on your screen.
- `restart --yes` restarts the Mac right away. Apps are not asked to save first (both `fdesetup authrestart` and `shutdown -r now` go through `shutdown`), so unsaved work in open apps is lost.
- Several wrong passwords in a row can lock your macOS account. `unlock` tries twice and then stops.

Use it only on a Mac you own, in a place you trust, and only if you are fine with an AI agent operating it.

## What Claude sees

`mac-remote status` (real output, screen locked, nothing held yet):

```
locked: yes
idle: 5270s since last input
holder: none (no VNC session held)
VNC user: you; password source: MAC_REMOTE_PASS_CMD (not fetched by status)
```

`mac-remote shot` prints the image path and how to map clicks; Claude then opens the JPG and looks at it:

```
~/.local/share/mac-remote/shots/shot-20261108-101500.jpg
image 1600x636, scale 0.3236, offset 0,0; locked: no
display 0: framebuffer rect x=0 y=0 w=3024 h=1964 -> image rect x=0 y=0 w=979 h=636
display 1: framebuffer rect x=3024 y=0 w=1920 h=1080 -> image rect x=979 y=0 w=621 h=350
click X Y uses this image's pixel coordinates
```

A typical session is `status`, `unlock`, `shot`, `click 812 440`, `shot`, `lock`.

`mac-remote restart --dry-run` (FileVault on; output from the tests, with fake `fdesetup` and `sudo`):

```
FileVault: on; authenticated restart supported (the disk unlocks once at the next boot)
password: accepted by sudo for you
FileVault user: you is enabled
pre-restart command: none
dry-run: everything is ready; not restarting. `mac-remote restart --yes` restarts.
```

## Install

In Claude Code:

```
/plugin marketplace add lucas-saldanha-werneck/claude-plugins
/plugin install mac-remote@lucas-tools
```

Then, once, in a terminal or by asking Claude:

```
mac-remote setup
```

This creates a Python virtualenv in `~/.local/share/mac-remote/.venv` and installs the dependencies. The `mac-remote` command is on Claude's Bash `PATH` while the plugin is enabled. In your own terminal it is not, unless you add the plugin's `bin/` folder to your `PATH`.

### Turn on Screen Sharing

System Settings > General > Sharing > Screen Sharing. Make sure your user is allowed to connect. This menu path was not checked on every macOS version; if it differs, search System Settings for "Screen Sharing".

mac-remote only connects to `127.0.0.1:5900`, the Mac it runs on. Turning Screen Sharing on is a system setting with its own exposure, separate from this plugin; check who may connect and your firewall settings.

### Tell mac-remote where the password is

Set `MAC_REMOTE_PASS_CMD` to a command that prints your Mac login password. Put it in `~/.config/mac-remote/config`:

```
# ~/.config/mac-remote/config
MAC_REMOTE_PASS_CMD=security find-generic-password -a "$USER" -s mac-remote -w
```

Examples:

| Source | Store it once | `MAC_REMOTE_PASS_CMD` |
|---|---|---|
| macOS Keychain | `security add-generic-password -a "$USER" -s mac-remote -w` (prompts for the password) | `security find-generic-password -a "$USER" -s mac-remote -w` |
| 1Password CLI | save a login item in 1Password, then copy the secret reference of its password field | `op read "<secret reference>"` |
| Bitwarden (`bw`) | save a login item | `bw get password "<item>"` (needs `BW_SESSION` from `bw unlock`) |
| Bitwarden (`rbw`) | save a login item | `rbw get "<item>"` |
| LastPass | save a site item | `lpass show --password "<item>"` |
| Dashlane | save a login item | `dcli read "dl://<item>/password"` |
| Keeper | save a record | `keeper find-password "<record UID or title>"` |
| pass / gopass | `pass insert <item>` / `gopass insert <item>` | `pass show <item>` / `gopass show <item> --password` |
| Any other manager | | any command that prints the password on its first line |

The manager must already be unlocked: the command runs with no input, so it cannot ask for a master password (KeePassXC's `keepassxc-cli`, for example, only works with a key file). These commands come from each manager's documentation and were run with the author's handoff-terminal plugin, not with mac-remote; mac-remote was run live with 1Password.

The command runs with a shell, only when a new VNC login is needed. Its output is captured and its error output is discarded, so nothing it prints reaches Claude. If it fails, mac-remote only reports the exit code; run the command yourself to see why.

Keychain caveat: if macOS asks "allow access to this item?", nobody can click Allow while the screen is locked. Storing the item with the `security` command as shown means `security` can read it without asking. This was not tested while the screen was locked.

## Settings

Environment variables override the config file. The config file accepts only `MAC_REMOTE_USER`, `MAC_REMOTE_PASS_CMD` and `MAC_REMOTE_RESTART_PRE_CMD`.

| Variable | Default | What it does |
|---|---|---|
| `MAC_REMOTE_PASS_CMD` | none | Command that prints the Mac password. |
| `MAC_REMOTE_PASS` | none | The password itself, from the environment only. Avoid it: any command Claude runs can read the environment. |
| `MAC_REMOTE_USER` | your current user name | Account used to log in to Screen Sharing. |
| `MAC_REMOTE_CONFIG` | `~/.config/mac-remote/config` | Config file path (respects `XDG_CONFIG_HOME`). |
| `MAC_REMOTE_HOME` | `~/.local/share/mac-remote` | Virtualenv, screenshots, log and socket (respects `XDG_DATA_HOME`). |
| `MAC_REMOTE_PYTHON` | the virtualenv, else `python3` | Python used to run mac-remote, and to create the virtualenv in `setup`. |
| `MAC_REMOTE_RESTART_PRE_CMD` | none | Command `restart --yes` runs before restarting (for example a script that saves your open terminal sessions). If it fails, the Mac is not restarted. Also accepted in the config file. |
| `MAC_REMOTE_ALLOW_DEAD_KEY_PASSWORD` | unset | Set to `1` to type a password that has `' " ` ~ ^` even though your keyboard layout has dead keys. Use it only if the lock screen uses a layout without them. |

Command options (`mac-remote <command> --help` lists them all):

- `unlock --idle-timeout SECONDS` (default 600): the holder lets go, and macOS locks, after this long with no command and no local input.
- `shot [PATH] [--display N]`: JPG, longest side 1600 px.
- `click X Y [--right | --double] [--raw]`, `move X Y [--raw]`: X Y are pixels of the last screenshot; `--raw` means framebuffer pixels.
- `type "text"`, `keys cmd+tab return esc`.
- `click`, `move`, `type` and `keys` take `--app NAME`: bring that app to the front first, and refuse if it does not stay there.
- `click`, `move`, `type`, `keys` and `lock` take `--force` to act even when someone seems to be using the Mac.
- `restart --dry-run` checks everything and does not restart; `restart --yes` restarts. One of the two is required. `--force` restarts even when someone seems to be using the Mac.

## Restart remotely (FileVault)

With FileVault on, a normal restart stops at the disk password screen before macOS starts, so Screen Sharing never comes back and you cannot reach the Mac from far away. `mac-remote restart` uses `fdesetup authrestart` instead: macOS unlocks the disk once at the next boot, then starts normally. With FileVault off it runs `shutdown -r now`, and warns if automatic login is off (the Mac would stop at the login window).

In order, it:

1. Reads the FileVault state (`fdesetup isactive`). With FileVault on, it refuses if `fdesetup supportsauthrestart` is not `true`.
2. Refuses if `MAC_REMOTE_USER` is not the user running mac-remote (sudo needs that user's password), or if someone used the Mac in the last 20 seconds (unless `--force`).
3. Gets the password from `MAC_REMOTE_PASS_CMD` (or `MAC_REMOTE_PASS`), drops any old sudo ticket (`sudo -k`) and checks the password with `sudo -S -v`. The password goes to sudo on stdin. With FileVault on, it refuses if your user is not in `sudo -n fdesetup list`.
4. `--dry-run` stops here and says whether everything is ready.
5. `--yes` runs `MAC_REMOTE_RESTART_PRE_CMD` if set, with no sudo ticket while it runs (a failure stops the restart, and the end of its error output goes to `restart.log`), then checks the password with sudo again. It checks once more that nobody is using the Mac (unless `--force`), then runs `sudo -n fdesetup authrestart -inputplist`, with the user name and password in a property list on stdin. With FileVault off: `sudo -n shutdown -r now`.
6. Whatever happens, it drops the sudo ticket (`sudo -k`) at the end.

Per `man fdesetup`, FileVault protections are reduced until that next boot: an unlock key is kept in memory (and, on supported systems, the System Management Controller) until the disk is unlocked.

The password is never printed, logged or put on a command line. Each step goes to `restart.log` in the data folder. Your user must be allowed to use sudo (an admin account) and must be a FileVault-enabled user.

After the restart, wait a few minutes and run `mac-remote status`. If the Mac stopped at the login window (FileVault off, no automatic login), `mac-remote unlock` can type the password there; that path was not tested.

## Requirements

- macOS. The command exits with an error on other systems.
- Screen Sharing turned on (above).
- `python3` to create the virtualenv. Tested with Python 3.15.
- Python packages, installed by `mac-remote setup` from `requirements.txt`: `asyncvnc` 1.3.0 (pinned, mac-remote uses some of its internals; it pulls in `numpy`, `cryptography` and `keysymdef`), `pillow`, `pyobjc-framework-Quartz`. To install them yourself instead: `pip install -r requirements.txt` and point `MAC_REMOTE_PYTHON` at that Python. Note that `asyncvnc` is GPL licensed; it is installed separately and not bundled here.

Optional: a password manager CLI such as 1Password's `op`. Without one, use the macOS Keychain example above.

## How it works

macOS locks the screen again about a second after the VNC session that unlocked it disconnects (measured on the author's Mac). So `unlock` starts a background process, the holder, that keeps one VNC connection open and types the password on the lock screen. The other commands talk to the holder over a Unix socket that only your user can open. Without a holder, `shot`, `click`, `type` and `keys` open a one-off connection; input commands refuse while the screen is locked.

Safety rules in the code:

- Input commands refuse if the screen is locked, or if there was local mouse or keyboard input in the last 20 seconds that mac-remote did not send. mac-remote records the time of its own input so it can tell the difference.
- If you come back while the holder is open, it logs "LOCAL ACTIVITY" and stays connected so the screen does not lock on you. The idle timer restarts from your last input.
- The holder exits, and macOS locks, on idle timeout, when the VNC connection drops, when the screen gets locked some other way, or on SIGTERM.
- A stop request (SIGTERM or `lock`) during `unlock` cancels it: the password is not typed, or Return is not pressed. If the password command is still running, the holder waits for it to finish, then exits without using the password. When `unlock` gives up after 120 seconds, it waits until the holder has exited before it says so.
- The holder fetches the password once and drops it right after unlocking.
- `lock` asks the holder to disconnect. If the screen stays unlocked it sends Ctrl+Cmd+Q over VNC, then runs `pmset displaysleepnow`.
- `type` pastes through the clipboard when typing key by key would lose characters: emoji, or `' " ` ~ ^` on a keyboard layout with dead keys. It checks the lock and local activity first, so a refused command does not touch the clipboard. It saves every item and format on the clipboard (text, images, files, rich text) and puts them back after the paste. If something new was copied in the meantime, it leaves that and says the clipboard was not restored. Text equal to `MAC_REMOTE_PASS` is never pasted. Do not use `type` for secrets.
- `--app NAME` is passed to AppleScript as an argument, never as script code.

State lives in `~/.local/share/mac-remote/`: `holder.log` (what happened and why the holder exited), `restart.log`, `holder.json`, `holder.sock`, `last-shot.json`, `state.json`, `shots/`.

## Uninstall

```
/plugin uninstall mac-remote@mac-remote
/plugin marketplace remove mac-remote
```

Then delete `~/.local/share/mac-remote` (virtualenv, screenshots, log) and `~/.config/mac-remote`. Turn Screen Sharing off if you do not use it otherwise.

## Limits

- Tests in `tests/` replace the VNC connection with fakes and never touch the screen: `python3 tests/test_mac_remote.py`. This release was also run on a real Mac (macOS 27, Apple silicon, U.S. International layout): `status` and `shot` on the locked screen, `unlock` (11 s), `shot`, `click`, `type` key by key, `type` by paste with `' " ~ ^`, `keys`, and `lock` (the Mac re-locked in about 1 to 2 s).
- The tests ran on one Mac with Python 3.14 and 3.15. Other macOS versions, Intel Macs and setups with several users were not tested.
- If `MAC_REMOTE_HOME` makes the socket path longer than a Unix socket allows (about 104 bytes on macOS), the socket goes to `/tmp/mac-remote-<uid>/` instead, a folder that must be yours with mode 700.
- Not tested: the holder rejecting a socket client from another user (only reading the peer's user id was tested), and a real VNC drop in the middle of a held session.
- Passwords with characters VNC cannot type are refused at unlock time. So are passwords with `' " ` ~ ^` when the selected keyboard layout has dead keys (U.S. International, ABNT and similar), because typed key by key those characters can change or vanish and cause two wrong attempts. mac-remote reads your selected layout, not the lock screen's; see `MAC_REMOTE_ALLOW_DEAD_KEY_PASSWORD`.
- Clipboard save and restore was tested live with text only (the text came back after a paste); images and files were tested with fakes and a private pasteboard. Data that an app only provides on request (some promised files) may not come back.
- `restart` was tested only with fake `fdesetup`, `sudo`, `sysadminctl` and `shutdown` on `PATH`; the tests never restart anything. It was not run on a real Mac in this release. The steps follow the `fdesetup` manual page (`authrestart -inputplist` with `Username` and `Password` keys) and a script the author used on his own Mac.
- Not tested in the Claude desktop app or on claude.ai. Per the Claude Code docs, claude.ai and Cowork do not install plugins that have a top-level `bin/` folder.

## License

MIT, see [LICENSE](LICENSE).

Built with Claude Code
