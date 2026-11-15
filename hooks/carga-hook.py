#!/usr/bin/env python3
"""PreToolUse hook of the mac-remote plugin: when carga-vigia says the Mac is heavy, tell Claude once per
heavy episode and per session to ask the user before heavy actions. Never blocks anything (always exit 0).
Fast on purpose: standard library only (os, sys, json), no subprocess, no network.
State: ~/.local/state/carga-vigia/pesado (written by bin/carga-vigia) and avisado/<session_id> (the episode
start this session was already told about). Env CARGA_VIGIA_STATE overrides the folder (tests)."""
import json
import os
import sys
import time

ST = os.environ.get("CARGA_VIGIA_STATE") or os.path.expanduser("~/.local/state/carga-vigia")


def main():
    try:
        with open(ST + "/pesado") as f:
            p = json.load(f)
        inicio = p["inicio"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    try:
        sid = str(json.load(sys.stdin).get("session_id") or "sem-sessao")
    except (ValueError, OSError, AttributeError):
        sid = "sem-sessao"
    sid = "".join(c for c in sid if c.isalnum() or c in "-_")[:80] or "sem-sessao"
    marca = "%s/avisado/%s" % (ST, sid)
    try:
        with open(marca) as f:
            if f.read().strip() == str(inicio):
                return 0
    except OSError:
        pass
    try:
        os.makedirs(ST + "/avisado", exist_ok=True)
        with open(marca, "w") as f:
            f.write(str(inicio))
        if int(time.time()) % 50 == 0:                      # now and then, drop marks older than 2 days
            for n in os.listdir(ST + "/avisado"):
                fp = ST + "/avisado/" + n
                if time.time() - os.stat(fp).st_mtime > 172800:
                    os.remove(fp)
    except OSError:
        pass
    cul = ", ".join("%s (%s)" % (c.get("app"), c.get("info")) for c in p.get("culpados", [])[:2]) or "?"
    hora = time.strftime("%H:%M", time.localtime(inicio))
    msg = ("Heavy Mac (load %s, culprit %s, since %s). Before heavy actions (many calls, robots, builds, VNC), "
           "ask the user with AskUserQuestion whether to pause or go on. Write to them in their language."
           % (p.get("carga"), cul, hora))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
