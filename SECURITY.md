# Security Policy

## Reporting a vulnerability

Please report it privately: open the repository's **Security** tab and choose **Report a vulnerability**
(https://github.com/lucas-saldanha-werneck/mac-remote/security/advisories/new). Please do not open a public issue for a
security problem.

## Supported versions

Only the latest release (0.4.2) is supported. Fixes go into a new release.

## What mac-remote accesses

- Gives Claude full control of your Mac through macOS Screen Sharing: it can unlock the screen, take screenshots, click, type and press keys, then lock again. Read the warning at the top of the README before you install it.
- Connects only to `127.0.0.1:5900` (your own Mac's Screen Sharing). It makes no other network requests.
- Gets your Mac password from the command you configure (`MAC_REMOTE_PASS_CMD`, such as the Keychain or `op read`), only to unlock or to restart past FileVault; it is never printed, logged or put on a command line. `mac-remote restart` uses `sudo` and drops the ticket afterwards.
- Screenshots are saved under `~/.local/share/mac-remote/` in a folder only your user can read, and can show anything that was on the screen. The optional `popup-vigia` watcher can click Allow on macOS permission popups and logs each click to `~/Library/Logs/popup-vigia/`.
