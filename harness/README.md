# Mu2e AI harnesses

Ready-to-use AI coding assistants for Mu2e, set up for the Fermilab gpvm
machines: Mu2e context, the Mu2e MCP servers (run database, DQM, ECL,
metacat, knowledge base, memory, literature), guard rails, and usage
logging -- without filling your home-directory quota.

| Harness | What it is | Start here |
|---|---|---|
| [`claude-code/`](claude-code/README.md) | [Claude Code](https://code.claude.com) on litellm.fnal.gov (or your own Anthropic key / claude.ai subscription) | **most users** |
| [`goose/`](goose/README.md) | [goose](https://github.com/block/goose) on vllm.fnal.gov, litellm.fnal.gov or ALCF | open models, no key needed for vllm |
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

Inside a session the assistant has no Kerberos ticket and no `~/.ssh`
(on purpose): it can't `ssh`, `kinit` or `sudo`. Run those yourself.
For stronger isolation -- only one directory visible at all -- use
`claudebox` (Claude Code in a container, see
[claude-code/README.md](claude-code/README.md#sandboxed-claudebox)).

## Update

```bash
cd /exp/mu2e/app/users/$USER/aitools && git pull
cd harness/claude-code && . setup.sh
```

Claude Code itself updates automatically; re-running `setup.sh` picks up
harness changes and re-syncs the MCP servers.

## Uninstall

```bash
rm -rf /exp/mu2e/app/users/$USER/claude-code   # Claude Code + its config
rm -rf /exp/mu2e/app/users/$USER/goose         # goose
rm -rf /exp/mu2e/app/users/$USER/aitools       # the scripts
```

and remove the `env.sh` line from `~/.bashrc` if you added one.

## What's shared between the harnesses

| Path | What |
|---|---|
| `shared/mu2e.md` | Mu2e context given to every session (Claude's `CLAUDE.md`, goose's TOM). Edit it to change what the assistants know up front. |
| `shared/mikey-token.sh`, `shared/getMu2eKey` | Token for the Mu2e MCP servers: from `getMu2eKey` (CVMFS), no personal token file needed |
| `shared/mcp-headers.sh` | Lets Claude Code fetch that token on each connect instead of storing it |
| `shared/block_commands.py` | Guard rail that blocks `ssh`/`scp`/`rsync`/`kinit`/`sudo`/`su` in sessions |
| `usage/` | Shared usage-record format (`usage_record.py`) and the `usage.sessions` table (`sessions.sql`) |

Usage logs hold counts, timings, model and tool names only -- no user name,
directory or conversation content.
