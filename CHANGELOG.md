# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.4.2] - 2026-11-13
- Skill: version and tags; Purpose, Prerequisites, Limitations and Troubleshooting sections.
- README: demo video and badges (CI, license, version, skill score); adds SECURITY.md.

## [0.4.1] - 2026-11-13
- Author metadata, Instructions and Examples in `SKILL.md`.

## [0.4.0] - 2026-11-12
- `popup-vigia`: an optional watcher that clicks Allow on macOS permission popups.

## [0.3.5] - 2026-11-12
- The holder drops input requests whose client already timed out (no late Cmd+V).
- `restart` calls `sudo`, `fdesetup`, `sysadminctl` and `shutdown` by absolute path; state files are written atomically.
- Clients check the socket folder before connecting; CI fails without the real dependencies.

## [0.3.4] - 2026-11-11
- Stops typing the password if the screen gets unlocked meanwhile.

## [0.3.3] - 2026-11-11
- Points to the catalog repo's new name, `lucas-tools`.

## [0.3.2] - 2026-11-11
- Catalog renamed to `lucas-tools`.

## [0.3.1] - 2026-11-09
- Docs: short description, catalog install, common heading names.

## [0.3.0] - 2026-11-08
- `restart`: restart the Mac remotely past the FileVault screen.
- `restart` keeps the sudo ticket, re-checks before restarting, and always drops the ticket.

## [0.2.2] - 2026-11-08
- The password command uses only its first line (works with `pass`, `gopass`); the README lists more password managers.
- CI runs the tests on Linux, macOS and Windows.

## [0.2.1] - 2026-11-08
- Reads the front app on newer macOS; short socket path for long home folders.

## [0.2.0] - 2026-11-08
- Safer `--app`, cancellable unlock, full clipboard restore.

## [0.1.0] - 2026-11-08
- First release: see and control a Mac through macOS Screen Sharing.
