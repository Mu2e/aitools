# Mu2e AI harnesses

Ready-to-use AI coding assistants for Mu2e, set up for the Fermilab gpvm
machines: Mu2e context, the Mu2e MCP servers (run database, DQM, ECL,
metacat, knowledge base, memory, literature), guard rails, and usage
logging -- without filling your home-directory quota.

| Harness | What it is | Start here |
|---|---|---|
| [`claude-code/`](claude-code/README.md) | [Claude Code](https://code.claude.com) on litellm.fnal.gov (or your own Anthropic key / claude.ai subscription) | **most users** |
| [`goose/`](goose/README.md) | [goose](https://github.com/block/goose) on vllm.fnal.gov, litellm.fnal.gov or ALCF | open models, no key needed for vllm |
| [`claudebox/`](claudebox/README.md) | Claude Code in a locked-down container: one work directory, no home, no credentials | untrusted input, unattended agents |
| [`slack/`](slack/README.md) | The `askmu2e` Slack bot (a service, not something you install) | maintainers |

## Install (Claude Code, on a gpvm)

You need: a Mu2e computing account (member of the `mu2e` group) and, for
the default backend, a personal API key from **https://litellm.fnal.gov**.
No Node.js, no root, nothing in `/nashome`.

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/aitools.git
cd aitools/harness/claude-code
. setup.sh
```

> Until the harness PR is merged into Mu2e/aitools, clone the fork instead:
> `git clone -b sc/harness https://github.com/corrodis/aitools.git`

`setup.sh` installs Claude Code, asks which backend to use (choose **1**,
litellm, and paste your key), and connects the Mu2e MCP servers. Then:

```bash
claude
```

Try: *"what were the last three runs?"* or *"search the ECL for CRV HV"*.

**In every new shell**, first load the harness (this is what makes
`claude` use the Mu2e setup):

```bash
. /exp/mu2e/app/users/$USER/aitools/harness/claude-code/env.sh
```

Add that line to your `~/.bashrc` if you want it always on.

For goose, the same with `cd aitools/harness/goose` -- see its
[README](goose/README.md).

### From a shared copy (CVMFS)

The harness also runs from a read-only copy -- nothing is written next to
the scripts; everything writable still goes to `/exp/mu2e/app/users/$USER/`.
Once it is published (see [Deploying](#deploying-a-shared-copy-maintainers)),
no clone is needed:

```bash
. <deployed harness>/claude-code/setup.sh     # first time
. <deployed harness>/claude-code/env.sh       # every new shell
```

Updates then arrive with each publish, without a `git pull`. If you
installed from a clone before, run setup once with `--refresh-settings` so
your hooks point at the shared copy.

## Note: source, don't run

Always `. setup.sh` / `. env.sh` (with the dot). `./setup.sh` installs, but
can't change your current shell, so `claude` won't pick up the harness.

## Where things live

Everything is under `/exp/mu2e/app/users/$USER/` (group storage, no quota
trouble):

| Path | What |
|---|---|
| `aitools/` | this repository (the harness scripts) |
| `claude-code/home/` | Claude Code's `$HOME`: binary (`.local/`), settings (`.claude/`), MCP config (`.claude.json`), backend key (`.env`, private) |
| `claude-code/log/usage.jsonl` | your session usage log |
| `goose/` | goose binary, config, sessions, usage log |
| `claudebox/` | home, runs and work dir of `claudebox` (Claude in a container) |

Inside a session the assistant is pointed away from your Kerberos ticket
and `~/.ssh` (on purpose), and `ssh`, `kinit`, `sudo` and the like are
refused. Run those yourself. This keeps the assistant from wandering off
the machine by accident; it is **not a security boundary** -- the session
still runs as you, and your ticket and files remain reachable to a process
that goes looking for them. For real isolation -- only one directory
visible at all -- use `claudebox` (Claude Code in a container, see
[claude-code/README.md](claude-code/README.md#sandboxed-claudebox)).

## Update

```bash
cd /exp/mu2e/app/users/$USER/aitools && git pull
cd harness/claude-code && . setup.sh
```

Claude Code itself updates automatically; re-running `setup.sh` picks up
harness changes and re-syncs the MCP servers. Your `settings.json` is the
exception: it is only written on first install. To take over a changed
template (new hooks or deny rules), or after moving the clone:
`. setup.sh --refresh-settings` (the old file is kept as a backup).

## Uninstall

```bash
rm -rf /exp/mu2e/app/users/$USER/claude-code   # Claude Code + its config
rm -rf /exp/mu2e/app/users/$USER/goose         # goose
rm -rf /exp/mu2e/app/users/$USER/aitools       # the scripts
```

and remove the `env.sh` line from `~/.bashrc` if you added one.

## Deploying a shared copy (maintainers)

```bash
harness/deploy.sh DEST          # e.g. a directory in a CVMFS transaction
```

copies the committed claude-code, claudebox and goose harnesses (not the Slack bot)
to `DEST` and writes `DEST/VERSION` (the commit, which the usage log
records as `harness_version`). It deploys `HEAD`, not uncommitted changes.

Users' `settings.json` and `~/.claude.json` store absolute paths into the
harness (hooks, MCP header helper), so publish to a **stable path**. If you
keep versioned directories, have users source through a fixed link
(`.../current/claude-code/setup.sh`): the scripts keep the path they were
sourced by, so the link is what ends up in the settings.

The hooks run straight from the shared copy, so a publish takes effect for
everyone at once -- including a broken one, and a broken guard-rail hook
fails open. Check before publishing.

## Skills

`setup.sh` of Claude Code and goose links the Mu2e skills of this repository
([`../skills/`](../skills)) into the harness, and your own next to them. A
skill is a folder with a `SKILL.md`: instructions the assistant loads when a
task calls for them (finding data with metacat, building with muse, FHiCL,
grid jobs, ...).

- **Your own skills** go in `/exp/mu2e/app/users/$USER/skills/<name>/SKILL.md`
  and are then available in every harness. A skill of yours with the name of
  a Mu2e one replaces it.
- **Updates:** a `git pull` updates the linked Mu2e skills. A new skill (in
  the repository, or one you wrote) appears after the next `. setup.sh`.
- **Which ones:** [`shared/skills.conf`](shared/skills.conf) lists the
  sources and the skills that are left out (the pull-request ones). More
  sources: add a `source` line there, or set `MU2E_SKILL_SOURCES`.
- **In the box** (`claudebox`, `goosebox`) they are copied in at every start,
  since the box does not see `/exp`. `MU2E_BOX_SKILLS` limits that, e.g. for
  an unattended agent: a list (`MU2E_BOX_SKILLS=finding-data-metacat,coding-with-fhicl`)
  or `none`.

A folder you put into the harness's own skill folder by hand is left alone.

## What's shared between the harnesses

| Path | What |
|---|---|
| `shared/mu2e.md` | Mu2e context given to every session (Claude's `CLAUDE.md`, goose's TOM). Edit it to change what the assistants know up front. |
| `shared/mikey-token.sh`, `shared/getMu2eKey` | Token for the Mu2e MCP servers: from `getMu2eKey` (CVMFS), no personal token file needed |
| `shared/mcp-headers.sh` | Lets Claude Code fetch that token on each connect instead of storing it |
| `shared/block_commands.py` | Guard rail that blocks `ssh`/`scp`/`rsync`/`kinit`/`sudo`/`su` in sessions |
| `shared/link-skills.py`, `shared/skills.conf` | Puts the skills where each harness reads them (see Skills) |
| `usage/` | Shared usage-record format (`usage_record.py`), the `usage.sessions` table (`sessions.sql`), and the service that takes the records (`server/`) |

Usage logs hold counts, timings, model and tool names only -- no user name,
directory or conversation content. Besides your local `usage.jsonl`, each
record goes to the [usage log service](usage/server/README.md) on
mu2eaigpvm01, which adds it to the shared usage table without recording who
sent it. To keep your records local: `LOG_URL=` (empty) before sourcing
`env.sh`.
