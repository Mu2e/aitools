# goose harness

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](../../LICENSE)

AI assistant for the [Mu2e experiment](https://mu2e.fnal.gov), built on top of
[goose](https://github.com/block/goose). This harness handles binary installation,
XDG relocation (shared home-dir quotas are small), MCP server sync, Kerberos
scrubbing, guardrails, and optional local tracing.

## Install

On a gpvm (see also the overview in [`../README.md`](../README.md)):

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/aitools.git     # until merged: -b sc/harness https://github.com/corrodis/aitools.git
cd aitools/harness/goose
. setup.sh           # source it, so goose is also on PATH in this shell
```

`setup.sh` will:

1. Redirect XDG dirs to group storage (`/exp/mu2e/app/users/$USER/goose/`)
2. Seed `config.yaml` from `config.yaml.example` if none exists yet
3. Download the goose binary (skips if already present)
4. Sync MCP servers from the internal registry

For a pinned version: `. setup.sh 1.0.25`

> **Every new shell** needs
> `. /exp/mu2e/app/users/$USER/aitools/harness/goose/env.sh` (or `. setup.sh`)
> to put goose on PATH. Add it to your `~/.bashrc` to make this automatic.

## Use

```bash
mu2eai               # start an interactive session
```

### Switch models

```bash
mu2eai-model         # interactive fuzzy picker (requires fzf)
```

| Backend | How | Key |
|---|---|---|
| `vllm.fnal.gov` (default) | `mu2eai-model` → default, or `./alcf.sh --restore-default` | none |
| `litellm.fnal.gov` | `mu2eai-model` → litellm, or `./setup-litellm.sh --model NAME` | personal key from https://litellm.fnal.gov (the same key works for the claude-code harness) |
| ALCF (metis, sophia, minerva, tara, ...) | `mu2eai-model` → alcf, or `./alcf.sh --cluster NAME --model NAME` | Globus login (fnal.gov or anl.gov identity) |

`./setup-litellm.sh --list-models`, `./alcf.sh --list-clusters` and
`./alcf.sh --list-models --cluster NAME` show what's available.

Keys are checked before use. A litellm key that litellm rejects (expired,
revoked, mistyped) is reported and you're asked for a new one. ALCF
periodically requires a *fresh* Globus login ("high-assurance timeout"),
which a token refresh doesn't satisfy: the scripts then ask you to log out
at https://app.globus.org/logout and log in again.

### Sync MCP servers

MCP servers are synced automatically on install. Re-run only if the registry has changed
(the token for the token-gated servers comes from `getMu2eKey`, see `../shared/mikey-token.sh`):

```bash
./sync-mcp.sh
```

### Session usage log

Token counts, model, and tool usage are logged after every turn to
`/exp/mu2e/app/users/$USER/goose/data/goose/log/usage.jsonl` (one line per
session, updated live). Records have the columns of the shared table
`usage.sessions` (`../usage/sessions.sql`), the same shape the Slack bot and
the claude-code harness write: counts, timings, model and tool names only --
no user, session name, working directory or content.

### Guard rails

goose runs with your `$HOME` pointed at `/exp/mu2e/app/users/$USER/goose/home`
(no `~/.ssh`) and without your Kerberos ticket. The plugin
`guardrails-plugin/` (linked into that home by `env.sh`) also refuses
`ssh`, `scp`, `sftp`, `rsync`, `kinit`, `sudo`, `su` -- also behind `env`,
`timeout`, `sh -c '...'`, `$(...)` -- via `../shared/block_commands.py`, the
same check the claude-code harness uses. A guard rail, not a sandbox.

When the plugin is active, an interactive session (`mu2eai`) starts with a
`⚛ mu2eai` banner line (`goose run` doesn't show it). **No banner = no
guard rail and no usage log** for that session (seen occasionally, cause
not yet known); check that the `plugins:` section of
`/exp/mu2e/app/users/$USER/goose/config/goose/config.yaml` lists
`mu2e-guardrails` with `enabled: true`, and start a new session.

### Optional: local tracing (OpenTelemetry → Jaeger)

```bash
./otel.sh start      # start a local Jaeger container
./otel.sh status     # check it's up
./otel.sh stop       # stop it
```

Tracing is auto-enabled when Jaeger is running — no flag needed. Trace data is
ephemeral (lost on container stop), which is fine for a local debugging aid.

To view from your laptop:

```bash
ssh -L 16686:localhost:16686 <this-host>
# then open http://localhost:16686
```

## Files

| File | Purpose |
|---|---|
| `env.sh` | Source this to put goose on PATH and set XDG dirs |
| `setup.sh` | Idempotent install + env setup (source or run) |
| `install.sh` | Raw binary installer (called by setup.sh) |
| `sync-mcp.sh` | Sync MCP servers from the internal registry |
| `goose-model.sh` | Interactive model/backend picker (`mu2eai-model`) |
| `otel.sh` | Start/stop/status local Jaeger for tracing |
| `alcf.sh` | Switch to an ALCF inference endpoint |
| `setup-litellm.sh` | Switch to the litellm.fnal.gov endpoint |
| `config.yaml.example` | Tracked config template (live config stays outside this repo) |
| `log-session.py` | Session usage from goose's SQLite → `usage.jsonl` (`usage.sessions` format) |
| `../shared/mikey-token.sh` | MIKEY token lookup for MCP sync (env → personal file → `getMu2eKey`) |
| `../shared/getMu2eKey` | Vendored copy of the CVMFS `getMu2eKey` (fallback when CVMFS is absent) |
| `guardrails-plugin/` | goose plugin: guard rail (→ `../shared/block_commands.py`), session banner (⚛ mu2eai), usage logging |
| `../shared/mu2e.md` | Mu2e context, injected via TOM (`GOOSE_MOIM_MESSAGE_FILE`, set by `env.sh`) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `goose: command not found` in a new shell | `. env.sh` |
| `mu2eai-model` shows an empty model list | fixed: the scripts now print the server's error; follow it (new litellm key / fresh ALCF login) |
| no `⚛ mu2eai` banner when an interactive session starts | see [Guard rails](#guard-rails) |
| an MCP server missing | `./sync-mcp.sh`; the token-gated ones need read access to the Mu2e key (member of `mu2e`) |

## For developers: test a fresh install

`GOOSE_HARNESS_ROOT` points the whole harness at another directory:

```bash
GOOSE_HARNESS_ROOT=/tmp/goose-test . setup.sh
```

(Start a new shell afterwards to get back to your real setup.)
