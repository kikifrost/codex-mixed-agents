#!/usr/bin/env python3
"""Deterministic fake Kimi CLI for runtime tests.

Accepts -m ALIAS, -p MESSAGE, -S SESSION_ID, --agent-file PATH and
--output-format stream-json. The prompt may embed JSON chunks separated by
blank lines with keys: text, delay (seconds), stderr (string to emit, or
{"char": C, "repeat": N} for a compact N-character payload that stays
under Linux's per-argument size cap), exit (exit code) and descendant (seconds for a sleeping child process in
the same process group, used to verify group cleanup). Emits stream-json
events: a system version line, one assistant message and a
session.resume_hint (echoing -S when resuming).
"""
import json
import os
import subprocess
import sys
import time

TAKES_VALUE = {"-m", "-p", "-S", "--agent-file", "--output-format"}


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    model, session, prompt = "fake", None, ""
    args = sys.argv[1:]
    if "--version" in args:
        print("2.1.1")
        sys.exit(0)
    if "--help" in args:
        print("Usage: kimi [options]")
        print("  -m, --model MODEL       model alias")
        print("  -S, --session ID        resume session")
        print("  --agent-file PATH       profile path")
        print("  -p, --prompt TEXT       prompt text")
        print("  --output-format FORMAT  stream-json")
        sys.exit(0)
    i = 0
    while i < len(args):
        if args[i] in TAKES_VALUE and i + 1 < len(args):
            if args[i] == "-m":
                model = args[i + 1]
            elif args[i] == "-S":
                session = args[i + 1]
            elif args[i] == "-p":
                prompt = args[i + 1]
            i += 2
        else:
            i += 1
    spec, plain = {}, []
    for part in prompt.split("\n\n"):
        try:
            value = json.loads(part)
        except ValueError:
            value = None
        if isinstance(value, dict):
            spec.update(value)
        else:
            plain.append(part)
    emit({"role": "meta", "type": "system.version", "version": "0.0.0-fake"})
    if spec.get("descendant"):
        subprocess.Popen([sys.executable, "-c",
                          "import sys,time;time.sleep(float(sys.argv[1]))",
                          str(float(spec["descendant"]))])
    if spec.get("stderr"):
        target = spec["stderr"]
        if isinstance(target, dict):  # compact form: Linux caps one argv arg at 128 KiB
            payload = str(target.get("char", "")) * int(target.get("repeat", 0))
        else:
            payload = str(target)
        sys.stderr.write(payload)
        sys.stderr.flush()
    session_id = session or "session_fake_%d" % int(time.time() * 1000)
    emit({"role": "meta", "type": "system.env", "KIMI_CODE_HOME": os.environ.get("KIMI_CODE_HOME", "")})
    emit({"role": "meta", "type": "session.resume_hint", "session_id": session_id})
    time.sleep(float(spec.get("delay", 0.05)))
    text = " / ".join(plain + ([str(spec["text"])] if "text" in spec else [])) or "ok"
    prefix = "RESUMED %s" % session if session else "NEW"
    home = os.environ.get("KIMI_CODE_HOME", "")
    emit({"role": "assistant", "content": "%s %s %s ENV:%s" % (prefix, model, text, home)})
    sys.exit(int(spec.get("exit", 0)))


if __name__ == "__main__":
    main()
