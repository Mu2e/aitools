#!/usr/bin/env python3
"""PreToolUse guard rail shared by the claude-code and goose harnesses.

Reads the hook payload JSON on stdin ({"tool_input": {"command": "..."}}),
and if the shell command would *run* one of BLOCKED, prints
{"decision": "block", "reason": "..."} (the format both tools accept);
otherwise prints nothing. Always exits 0: a broken hook must not break
the session.

Usage: block_commands.py "<tool name for the message>"

A command name counts when it is in command position -- start of the
line, after ; | & && || ( or inside $(...) / `...` -- also behind
assignments (FOO=1 ssh) and wrappers (env, command, exec, nohup, nice,
time, timeout, setsid, stdbuf, xargs), and inside sh/bash -c '...' and
eval. Mentions as arguments (grep ssh, git log --grep=su, echo "ssh")
are not blocked.

Still a heuristic, not a sandbox: the agent runs with the user's Unix
permissions, and a determined script (python -c, a written-out file)
gets around any such list. The harness's real protection against remote
access is that sessions have no Kerberos ticket and no ~/.ssh.
"""

import json
import os
import re
import shlex
import sys

BLOCKED = {"ssh", "scp", "sftp", "rsync", "kinit", "sudo", "su"}
WRAPPERS = {"env", "command", "builtin", "exec", "nohup", "nice", "time",
            "timeout", "setsid", "stdbuf", "xargs", "unbuffer"}
SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}
# Wrapper options that take a separate value (`timeout -s KILL 5 cmd`).
OPT_WITH_VALUE = {
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a"},
    "stdbuf": {"-i", "-o", "-e"},
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _tokens(cmd: str):
    # Command substitution starts a new command; treat it as a separator.
    cmd = cmd.replace("$(", " ; ").replace("`", " ; ")
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return cmd.replace(";", " ; ").replace("|", " | ").replace("&", " & ").split()


def _is_operator(tok: str) -> bool:
    return bool(tok) and all(c in "();|&<>" for c in tok)


def find_blocked(cmd: str, depth: int = 0):
    """Name of the first blocked command `cmd` would run, or None."""
    if depth > 5:
        return None
    toks = _tokens(cmd)
    i, at_cmd = 0, True
    while i < len(toks):
        tok = toks[i]
        if _is_operator(tok):
            if set(tok) <= set("<>"):
                i += 2  # redirection: the next token is a file name
                continue
            at_cmd = True
            i += 1
            continue
        if not at_cmd:
            i += 1
            continue
        if ASSIGNMENT.match(tok):
            i += 1
            continue
        name = os.path.basename(tok)
        if name in BLOCKED:
            return name
        if name in WRAPPERS:
            i += 1
            # Skip the wrapper's options (and timeout's duration).
            takes_value = OPT_WITH_VALUE.get(name, set())
            while i < len(toks) and toks[i].startswith("-"):
                i += 2 if toks[i] in takes_value else 1
            if name == "timeout" and i < len(toks) and not _is_operator(toks[i]):
                i += 1
            continue  # still at command position
        if name in SHELLS:
            for j in range(i + 1, len(toks)):
                if _is_operator(toks[j]):
                    break
                if toks[j].startswith("-") and "c" in toks[j][1:] and j + 1 < len(toks):
                    hit = find_blocked(toks[j + 1], depth + 1)
                    if hit:
                        return hit
                    break
        if name == "eval":
            rest = []
            for t in toks[i + 1:]:
                if _is_operator(t):
                    break
                rest.append(t)
            hit = find_blocked(" ".join(rest), depth + 1)
            if hit:
                return hit
        at_cmd = False
        i += 1
    return None


def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else "agent"
    try:
        payload = json.load(sys.stdin)
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        hit = find_blocked(cmd) if cmd else None
    except Exception:
        return 0
    if hit:
        print(json.dumps({
            "decision": "block",
            "reason": (f"{hit} is blocked inside {tool} sessions by this harness "
                       "(guard rail against lateral/remote access and privilege "
                       "escalation). Run it from your own shell instead."),
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
