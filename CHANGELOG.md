# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.4.8] - 2026-11-15
- New `carga-vigia` (bin/carga-vigia, a LaunchAgent every 60 s): records load 1/5/15, free memory, swap and the 5 apps with most CPU and the 5 with most memory in `~/Library/Logs/carga-vigia/carga.jsonl` (7 days). Processes of one app are grouped (Dropbox + DropboxFileProvider = "Dropbox"); `fileproviderd` and `FPCKService` count as Dropbox only when Dropbox (30% CPU or more) and that daemon (10% or more) are high in the same measurement.
- "Heavy Mac" state: load1 above 40 for 3 measurements in a row creates `~/.local/state/carga-vigia/pesado` and sends one notice through `NOTIFY_CMD` (default `avisa-lucas`, at most 1 per 30 min); below 25 for 3 in a row removes it (a "back to normal" notice only after an episode longer than 10 min). Thresholds and language (`LANG=pt`) in `~/.config/carga-vigia/config`.
- `mac-remote` unlock/shot/click/move/type/keys: when the heavy state is on, print a 1-line warning on stderr and go on as usual. New `--wait-load [SECONDS]` waits (default 300 s) for the heavy state to end first; it never refuses.
- New PreToolUse hook (`hooks/hooks.json`, `hooks/carga-hook.py`) for Bash, Agent/Task and computer-control tools: during a heavy episode it tells Claude once per session to ask with AskUserQuestion before heavy actions. It blocks nothing.

## [0.4.7] - 2026-11-15
- VNC login no longer drops about 1 in 6 connections. asyncvnc sends the Apple login choice (byte 33) and the RSA key request as two writes; when screensharingd read between them it took the request's leading 0 as a second, unoffered login type ("viewer selected auth type 0 which was not advertised") and closed the connection (IncompleteReadError). mac-remote now passes asyncvnc a thin writer wrapper (`JoinAuthChoice`) that sends both in one write; the rest of the asyncvnc login, including its fallback to other auth types, is unchanged. In the screensharingd log every good login read the 15 bytes together and every drop read the 1 byte alone. The 4 retries stay.

## [0.4.6] - 2026-11-15
- `popup-vigia`: a notice about a window whose request sentence wraps no longer carries words OCR read from the window behind the dialog ("pede: | off . Mar files managed by ..."); junk at the start of a wrapped line and after the sentence is dropped, and an unknown request that still looks dirty becomes "a permission window".
- `popup-vigia`: plain notices (both languages) for "access files in your X" without the word folder, "access files managed by X" (Dropbox, iCloud Drive) and the Apple Music media library request. Tests use every real request text from the click journal.

## [0.4.5] - 2026-11-15
- `popup-vigia`: rewrote every notice as up to 3 short lines (what happened, what the watcher did, what to do, with the exact command such as `popup-vigia reset`). A notice about a window now names the app and the request ("Grok Bot" wants to access files in your Desktop folder) instead of dumping the raw OCR text; when nothing clean is found it says "a permission window". The full OCR text stays in `rodadas.log` and `cliques.jsonl`.
- `popup-vigia`: new language setting for the notices, `LANG=pt` in `~/.config/popup-vigia/config` (or env `POPUP_VIGIA_LANG`); `en` is the default.
- `popup-vigia`: the "clicked, result unknown" notice now also tells you to run `popup-vigia reset`.

## [0.4.4] - 2026-11-15
- `popup-vigia`: the unlock deadline is 120 s (was 75 s; real unlocks took 74-80 s); the forced-lock watchdog is 185 s and the whole round 230 s, keeping the same margins.
- `popup-vigia`: a plain "Don't Allow / Allow" popup is clicked even when OCR adds words from the screen behind the translucent dialog on the same line (words at least 2 text heights away from the pair are ignored; the forbidden-word block still reads the whole line).

## [0.4.3] - 2026-11-15
- `popup-vigia`: an UNLOCK-FAILED lock caused by a timeout of the unlock call now expires like a connection drop (after 15 min, shared 3/day cap); a refused password still stays locked.

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
