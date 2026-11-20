#!/usr/bin/env python3
"""PreToolUse guard rail shared by the claude-code and goose harnesses.

Reads the hook payload JSON on stdin ({"tool_input": {"command": "..."}}),
and if the shell command would *run* one of BLOCKED, prints
{"decision": "block", "reason": "..."} (the format both tools accept);
otherwise prints nothing. Always exits 0: a broken hook must not break
the session.

Usage: block_commands.py "<tool name for the message>"

A command name counts when it is in command position -- start of a
line, after ; | & && || ( or inside $(...) / `...` -- also behind
assignments (FOO=1 ssh), shell keywords (then, do, else, {, !, ...) and
wrappers (env, command, exec, nohup, nice, time, timeout, setsid, stdbuf,
xargs), and inside sh/bash -c '...', eval, and here-documents or
here-strings fed to a shell. Mentions as arguments (grep ssh,
git log --grep=su, echo "ssh") and in other here-document bodies
(cat <<EOF > notes.md) are not blocked.

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
# Shell keywords after which the next word is still a command.
KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "{", "!"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# <<EOF, <<-EOF, <<'EOF', << "EOF", <<\\EOF at a given place in a line.
HEREDOC = re.compile(r"""<<(-?)\s*(\\?)(['"]?)(\w+)\3""")
WORD = re.compile(r"[\w./-]+")


def _heredoc_ops(line: str):
    """(dash, quoted, marker) for every here-document operator of the line
    that the shell would see as one: not inside quotes, not in a comment,
    and not the here-string <<<. `echo '<<EOF'` starts no here-document."""
    ops, quote, i, n = [], "", 0, len(line)
    while i < n:
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
        elif c == "\\":
            i += 2
        elif c in "'\"":
            quote = c
            i += 1
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        elif line.startswith("<<<", i):
            i += 3
        elif line.startswith("<<", i):
            m = HEREDOC.match(line, i)
            if m:
                ops.append((m.group(1), bool(m.group(2) or m.group(3)), m.group(4)))
                i = m.end()
            else:
                i += 2
        else:
            i += 1
    return ops


def _strip_heredocs(cmd: str) -> str:
    """Drop here-document bodies that are data, not commands (a README
    written with `cat <<EOF` may well mention ssh). Kept:
      - the whole body when the line feeds a shell (`bash <<EOF`): it is
        what gets run;
      - of a body the shell expands (the marker is not quoted), the lines
        with a command substitution: `cat <<EOF` runs the $(...) in it;
      - everything when the end marker is missing (then it probably was
        not a here-document)."""
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        to_shell = any(os.path.basename(w) in SHELLS for w in WORD.findall(line))
        for dash, quoted, marker in _heredoc_ops(line):
            end = next((j for j in range(i, len(lines))
                        if (lines[j].lstrip("\t") if dash else lines[j]) == marker), None)
            if end is None:
                break
            if to_shell:
                out.extend(lines[i:end])
            elif not quoted:
                out.extend(l for l in lines[i:end] if "$(" in l or "`" in l)
            i = end + 1
    return "\n".join(out)


def _tokens(cmd: str):
    # A newline ends a command (a backslash-newline continues it), and
    # command substitution starts a new one: treat them as separators.
    # Inside quotes this stays inside the quoted token, which is what we
    # want (sh -c "...", eval "..." are tokenized again).
    cmd = cmd.replace("\\\n", " ").replace("\n", " ; ")
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
    toks = _tokens(_strip_heredocs(cmd))
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
        if ASSIGNMENT.match(tok) or tok in KEYWORDS:
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
                if toks[j] == "<<<" and j + 1 < len(toks):  # here-string
                    hit = find_blocked(toks[j + 1], depth + 1)
                    if hit:
                        return hit
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
