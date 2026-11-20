#!/usr/bin/env python3
"""Put the Mu2e skills, and the user's own, where a harness looks for skills.

    link-skills.py DEST [--copy] [--only LIST] [-v]

DEST is the harness's skill folder (Claude Code: <home>/.claude/skills,
goose: <home>/.agents/skills). Every skill found in the sources -- a folder
with a SKILL.md, the Agent Skills layout -- gets a link DEST/<name> to it.
With --copy it gets a copy instead: for the sandbox home, where the sources
(/exp, a git clone) are not mounted. --only limits the set to the named
skills ("a,b,c"), or to nothing ("none"): the box wrappers pass
$MU2E_BOX_SKILLS there, for an agent that should start with few or none.

Sources and exclusions are listed in skills.conf next to this file:

    source DIR       a folder of skills; relative to this file's folder,
                     $USER and ~ expanded. Earlier sources win a name clash.
    exclude NAME     a skill that is not linked, from whatever source

$MU2E_SKILL_SOURCES (colon-separated folders) come before those of the
file. A source that does not exist is skipped.

What is in DEST already is respected: a real folder or file of a skill's
name is the user's own and is left alone (it wins); only what an earlier run
put there (recorded in DEST/.mu2e-skills) is replaced or, when its skill is
gone or excluded now, removed. Prints one line: how many skills, from where.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = ".mu2e-skills"


def read_conf(path: Path) -> tuple[list[Path], set[str]]:
    sources: list[Path] = []
    exclude: set[str] = set()
    for raw in os.environ.get("MU2E_SKILL_SOURCES", "").split(":"):
        if raw.strip():
            sources.append(Path(os.path.expandvars(raw.strip())).expanduser())
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            kind, _, value = line.partition(" ")
            value = value.strip()
            if kind == "source" and value:
                p = Path(os.path.expandvars(value)).expanduser()
                sources.append(p if p.is_absolute() else (HERE / p))
            elif kind == "exclude" and value:
                exclude.add(value)
            else:
                print(f"link-skills: {path.name}: line not understood: {line}", file=sys.stderr)
    return sources, exclude


def find_skills(sources: list[Path], exclude: set[str]) -> dict[str, Path]:
    """name -> the skill's folder; the first source that has a name wins."""
    found: dict[str, Path] = {}
    for src in sources:
        if not src.is_dir():
            continue
        for d in sorted(src.iterdir()):
            if d.name.startswith(".") or d.name in exclude or d.name in found:
                continue
            if (d / "SKILL.md").is_file():
                found[d.name] = d.resolve()
    return found


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def main(argv: list[str]) -> int:
    argv = list(argv)
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 >= len(argv):
            print("link-skills: --only needs a list of names, or none", file=sys.stderr)
            return 2
        only = {n.strip() for n in argv[i + 1].split(",") if n.strip() and n.strip() != "none"}
        del argv[i:i + 2]
    args = [a for a in argv[1:] if not a.startswith("-")]
    copy, verbose = "--copy" in argv, "-v" in argv or "--verbose" in argv
    if len(args) != 1 or "-h" in argv or "--help" in argv:
        print(__doc__, file=sys.stderr)
        return 2
    dest = Path(args[0])
    dest.mkdir(parents=True, exist_ok=True)
    sources, exclude = read_conf(HERE / "skills.conf")
    skills = find_skills(sources, exclude)
    if only is not None:
        for name in sorted(only - set(skills)):
            print(f"link-skills: no skill named {name} (--only)", file=sys.stderr)
        skills = {n: p for n, p in skills.items() if n in only}

    manifest = dest / MANIFEST
    # In a box the destination is writable from inside: the record is taken
    # as a list of plain names in this folder and nothing else, and neither it
    # nor the folder is followed if it has been made a link.
    if (copy and dest.is_symlink()) or manifest.is_symlink():
        print(f"link-skills: {dest} or its record is a link; not touching it", file=sys.stderr)
        return 1
    try:
        ours = {n for n in json.loads(manifest.read_text())
                if isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", n)}
    except (OSError, ValueError, TypeError):
        ours = set()

    # What an earlier run put here and is not wanted any more.
    for name in sorted(ours - set(skills)):
        target = dest / name
        if target.is_symlink() or target.exists():
            remove(target)
            if verbose:
                print(f"  removed {name}")
    placed, own = [], []
    for name, src in skills.items():
        target = dest / name
        if (target.exists() or target.is_symlink()) and name not in ours:
            own.append(name)          # the user's own (folder or link) of that name: it wins
            continue
        if target.is_symlink() or target.exists():
            remove(target)
        if copy:
            shutil.copytree(src, target, symlinks=False)
        else:
            target.symlink_to(src, target_is_directory=True)
        placed.append(name)
        if verbose:
            print(f"  {name}  <-  {src.parent}")
    if manifest.exists():
        manifest.unlink()
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(sorted(placed)) + "\n")

    used = [str(s) for s in sources if s.is_dir()]
    note = f", {len(own)} of your own kept ({', '.join(own)})" if own else ""
    print(f"{len(placed)} skills {'copied' if copy else 'linked'} into {dest}{note}"
          + (f" from {len(used)} source(s)" if verbose else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
