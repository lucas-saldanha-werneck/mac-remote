# Security Policy

## Reporting a vulnerability

Please report it privately: open the repository's **Security** tab and choose **Report a vulnerability**
(https://github.com/lucas-saldanha-werneck/mac-remote/security/advisories/new). Please do not open a public issue for a
security problem.

## Supported versions

Only the latest release is supported. Fixes go into a new release.

## What mac-remote accesses

- Gives Claude full control of your Mac through macOS Screen Sharing: it can unlock the screen, take screenshots, click, type and press keys in any app, bring an app to the front (`osascript`), and lock again (falling back to `pmset displaysleepnow`). When `type` has to paste, it puts the text on your clipboard for a moment and then puts the old contents back. Read the warning at the top of the README before you install it.
- Network: when it runs, it connects only to `127.0.0.1:5900` (your own Mac's Screen Sharing). `mac-remote setup` downloads its Python packages from PyPI with `pip`.
- Gets your Mac password from the command you configure (`MAC_REMOTE_PASS_CMD`, such as the Keychain or `op read`) or from the `MAC_REMOTE_PASS` environment variable, only to unlock or to restart past FileVault. It is never printed, logged or put on a command line: it is typed into the lock screen over the local VNC connection, and given to `sudo` and `fdesetup authrestart` on stdin.
- `mac-remote restart --yes` restarts the Mac with `sudo`, after running your optional `MAC_REMOTE_RESTART_PRE_CMD`, and drops the sudo ticket afterwards. With FileVault on it uses `fdesetup authrestart`, so the disk is unlocked once at the next boot.
- Screenshots, its log and state files go to `~/.local/share/mac-remote/` (or `$MAC_REMOTE_HOME`), a folder only your user can read. Screenshots can show anything that was on the screen. A path you give to `shot` is written where you say.
- The optional `popup-vigia` watcher (you schedule it with launchd yourself) unlocks the screen with the same password when a permission request is stuck, clicks Allow on macOS permission popups, and saves the window text (read with `tesseract`) and before/after screenshots to `~/Library/Logs/popup-vigia/`. It reads recent `tccd` entries from the system log; if you set `NOTIFY_CMD`, it runs that command with the popup text as an argument. `popup-vigia sync-config` copies your mac-remote config to `~/.local/state/popup-vigia/`.
