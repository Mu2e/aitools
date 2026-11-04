# claude-code harness

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](../../LICENSE)

[Claude Code](https://code.claude.com) for the [Mu2e experiment](https://mu2e.fnal.gov)
on the Fermilab gpvm machines: installed into group storage (not
`/nashome`), wired to litellm.fnal.gov, with the Mu2e MCP servers, Mu2e
context, guard rails and usage logging. Parallel to [`../goose/`](../goose/README.md);
overview of both in [`../README.md`](../README.md).

## Install

Prerequisites: a Mu2e account (member of the `mu2e` group) and a personal
API key from **https://litellm.fnal.gov** (for the default backend). No
Node.js needed.

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/aitools.git     # until merged: -b sc/harness https://github.com/corrodis/aitools.git
cd aitools/harness/claude-code
. setup.sh
```

At the prompt, choose **1** (litellm.fnal.gov) and paste your key (input is
hidden). `setup.sh` is safe to re-run; it only does what's missing:

1. Installs the native Claude Code build into
   `/exp/mu2e/app/users/$USER/claude-code/home/.local/` (~320 MB; it
   auto-updates). Pin a version with `. setup.sh 2.1.318` (or `stable`).
2. Seeds `settings.json` from `settings.json.example`: default model
   Sonnet, guard-rail and logging hooks, deny rules.
3. Links the Mu2e context `../shared/mu2e.md` as `~/.claude/CLAUDE.md`
   (loaded in every session).
4. Asks for a backend if none is configured (see [Backends](#backends)).
5. Connects the Mu2e MCP servers from the registry on mu2eaigpvm01.

## Use

```bash
claude                  # interactive (alias: mu2eai-claude)
claude -p "question"    # one-shot, prints the answer
```

In a **new shell**, load the harness first -- otherwise `claude` is not
found, or runs without the Mu2e setup:

```bash
. /exp/mu2e/app/users/$USER/aitools/harness/claude-code/env.sh
```

(`. setup.sh` does the same and also re-checks the install.) Add the line to
`~/.bashrc` to make it permanent. Always *source* (`. file`), don't run
(`./file`).

Inside claude:

| | |
|---|---|
| `/model` | switch model: `sonnet` (default), `opus`, `haiku`, `fable`, or any litellm name, e.g. `/model azure/claude-opus-4-8` |
| `/mcp` | the Mu2e MCP servers and their status |
| `/status` | which backend/account is in use |
| `/usage` | token use of this session |

## Backends

| Option in `setup.sh` | What you need | Notes |
|---|---|---|
| **1 litellm.fnal.gov** | personal key from https://litellm.fnal.gov | recommended |
| **2 Anthropic API key** | key from console.anthropic.com | pay as you go |
| **3 skip** → `/login` in claude | claude.ai Pro/Max/Team/Enterprise | |

**litellm:** `./setup-litellm.sh` (re)configures it at any time: it asks for
the key if none is saved and maps Claude Code's model names
(`sonnet`, `opus`, `haiku`, `fable`) to the newest matching litellm models
(`azure/claude-sonnet-5-5`, ...). Re-run it when litellm adds new models.
`./setup-litellm.sh --list-models` shows what litellm offers;
`--model NAME` forces a default model, overriding the one `/model` saves.

**claude.ai subscription:** choose 3, start `claude`, type `/login` (it
prints a URL -- open it in your laptop's browser and paste the code back).
If you used litellm or an API key before, move
`$CLAUDE_HOME/.env` away first: a key there takes precedence over the login.

The backend settings live in `/exp/mu2e/app/users/$USER/claude-code/home/.env`
(private, mode 600); `env.sh` loads it.

## MCP servers

Connected on install; re-run `./sync-mcp.sh` when the registry has new
servers. The token-gated ones (ecl, runs, memory, kb) need the shared Mu2e
MIKEY key: Claude Code fetches it on each connect via
`../shared/mcp-headers.sh` → `getMu2eKey`, so it is never stored in the
config and no personal token file is needed. (Where that can't work
without Kerberos, `sync-mcp.sh` stores the header instead and says so.)

## Guard rails

- The session has **no Kerberos ticket** and its `$HOME` is the harness
  directory (no `~/.ssh`), so remote logins fail. Run `kinit`, `ssh`, `sudo`
  yourself, outside claude.
- A PreToolUse hook (`hooks/block-commands.sh` →
  `../shared/block_commands.py`) refuses `ssh`, `scp`, `sftp`, `rsync`,
  `kinit`, `sudo`, `su` -- also behind `env`, `timeout`, `xargs`, `sh -c '...'`,
  `$(...)` -- and `permissions.deny` in `settings.json` adds Claude Code's
  own check. A guard rail, not a sandbox: the agent runs with your Unix
  permissions.

## Usage log

`hooks/log-session.sh` (on UserPromptSubmit, Stop and SessionEnd) runs
`log-session-claude.py`, which reads the session transcript and upserts one
line per session into `/exp/mu2e/app/users/$USER/claude-code/log/usage.jsonl`.
Records have the columns of the shared table `usage.sessions`
(`../usage/sessions.sql`), like the goose harness and the Slack bot: counts,
timings, model and tool names only -- no user, directory or content.

## Sandboxed: claudebox

`/cvmfs/mu2e.opensciencegrid.org/bin/claudebox` runs Claude Code in an
apptainer container that sees only `/cvmfs` (read-only) and **one work
directory** -- no home, no other `/exp` areas, no Kerberos, no tokens. With
the harness loaded, `claudebox` gets the harness defaults (via
`claudebox.sh`): this Claude Code binary, your litellm backend and model
mapping, the Mu2e MCP servers and their key, the Mu2e context, Sonnet.

```bash
cd /exp/mu2e/app/users/$USER/myproject
claudebox                          # interactive, only this directory writable (= claudebox run)
claudebox agent nightly -f task.txt   # unattended in the background (no permission prompts)
claudebox list | log nightly -f | stop nightly
claudebox help                     # everything else (--krb5, --github, --bind, ...)
```

The container has its own home (`/exp/mu2e/app/users/$USER/claudebox/`),
separate from the harness one. `run` sessions are added to the usage log;
`agent` runs are not (yet). `command claudebox` runs the plain CVMFS
version without the harness defaults.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `claude: command not found` in a new shell | `. env.sh` (see [Use](#use)) |
| `API Error: 400 ... Invalid model name` | the model name isn't one litellm knows: `./setup-litellm.sh` to refresh the model mapping, then restart claude |
| claude starts, but replies `Not logged in · Please run /login` | no backend configured: `./setup-litellm.sh`, or `/login` for a subscription |
| an MCP server shows `✘ Failed` in `claude mcp list` | `./sync-mcp.sh`; the token-gated servers need read access to the Mu2e key (member of `mu2e`) |
| `/login` seems ignored | a key in `$CLAUDE_HOME/.env` wins -- move the file away, open a new shell |

## Files

| File | Purpose |
|------|---------|
| `env.sh` | Source in each shell: `claude` with the harness `$HOME`, no Kerberos, private umask |
| `setup.sh` | Idempotent install + configuration (source it) |
| `setup-litellm.sh` | Configure litellm.fnal.gov: key, model mapping |
| `sync-mcp.sh`, `sync_mcp_claude.py` | Connect the MCP servers from the registry |
| `settings.json.example` | Template for `settings.json` (the live copy is outside the repo) |
| `claudebox.sh` | Harness defaults for the CVMFS `claudebox` (the `claudebox` function in `env.sh`) |
| `hooks/block-commands.sh` | PreToolUse guard rail |
| `hooks/log-session.sh`, `log-session-claude.py` | Usage log |
| `../shared/`, `../usage/` | Shared with goose -- see [`../README.md`](../README.md#whats-shared-between-the-harnesses) |

## For developers: test a fresh install

`CLAUDE_HARNESS_ROOT` points the whole harness at another directory, so you
can run the install from scratch without touching your own setup:

```bash
CLAUDE_HARNESS_ROOT=/tmp/claude-test . setup.sh
```

(Start a new shell afterwards to get back to your real setup.)
