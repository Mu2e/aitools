# claude-code harness

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](../../LICENSE)

Claude Code for the [Mu2e experiment](https://mu2e.fnal.gov), with
home-directory relocation (shared home quotas), MCP server sync, Kerberos
scrubbing, and guardrails hooks. Parallel to `../goose/`, sharing the same
MCP registry and the same `../shared/mu2e.md` context file.

## Install

```bash
cd ~/aitools/harness/claude-code
. setup.sh
```

`setup.sh` will:

1. Redirect `$HOME` for claude to `/exp/mu2e/app/users/$USER/claude-code/home/`
   (keeps `~/.claude.json` and `~/.claude/` off the quota-constrained `/nashome`)
2. Install the native Claude Code build there if missing (Anthropic's
   `install.sh`; self-contained, **no Node.js needed**, ~320 MB, auto-updates).
   Pin a version with `. setup.sh 2.1.318` (or `stable`).
3. Prompt for an API backend if none is configured:
   - **litellm.fnal.gov** (recommended — shared Fermilab endpoint, personal key from https://litellm.fnal.gov)
   - **Personal Anthropic API key** (`console.anthropic.com`)
4. Seed `~/.claude/settings.json` (default model `sonnet`; hooks: guardrails, session logging)
5. Symlink `../shared/mu2e.md` → `$CLAUDE_HOME/CLAUDE.md` (auto-discovered context)
6. Sync MCP servers from the internal registry

> **Every new shell** needs `. env.sh` (or `. setup.sh`) to put claude on PATH
> and activate the HOME override.

## Use

```bash
claude          # or: mu2eai-claude
```

## Backends

`setup.sh` prompts for a backend on first run:

| Option | How | Best for |
|--------|-----|---------|
| **litellm.fnal.gov** | Personal API key from https://litellm.fnal.gov | Most users — shared Fermilab-hosted endpoint |
| **Anthropic API key** | Key from console.anthropic.com | Pay-as-you-go direct access |
| **claude.ai subscription** | Skip setup, then `/login` inside claude | Pro/Max/Team/Enterprise subscribers |

To switch to or update the LiteLLM backend later:

```bash
./setup-litellm.sh                 # re-prompts for key if needed, maps model aliases
./setup-litellm.sh --list-models   # see what's available
```

To use a claude.ai subscription instead, skip the key prompt during setup (option 3)
then run `claude` and type `/login` at the prompt.

## Sync MCP servers

MCP servers are synced automatically on install. Re-run only if the registry
has changed:

```bash
./sync-mcp.sh
```

## Shared context (CLAUDE.md / TOM)

Both Claude Code and the goose harness draw from the same file:

```
../shared/mu2e.md
```

- **Claude Code**: auto-discovered as `$HOME/CLAUDE.md` (symlinked by setup.sh).
  Injected into every session automatically.
- **goose**: injected via the TOM (Top Of Mind) extension using
  `GOOSE_MOIM_MESSAGE_FILE`, set automatically by `../goose/env.sh`.

Edit `../shared/mu2e.md` to update context for both tools at once.

## Hooks / guardrails

Configured in `$CLAUDE_HOME/.claude/settings.json` (seeded from
`settings.json.example`):

| Hook | Trigger | Effect |
|------|---------|--------|
| `block-commands.sh` | PreToolUse (Bash) | Blocks `ssh`, `scp`, `sftp`, `rsync`, `kinit`, `sudo`, `su` |
| `log-session.sh` | UserPromptSubmit + Stop | Session usage log (see below) |

Blocking is a heuristic guard rail, not a hard sandbox. The `permissions.deny`
rules in `settings.json` add a second layer (Claude Code's own permission
system), but the agent still runs with your full Unix permissions.

## Session usage logging

`hooks/log-session.sh` runs `log-session-claude.py` after every prompt and
reply. It reads the session transcript
(`$CLAUDE_HOME/.claude/projects/<dir>/<session-id>.jsonl`) and upserts one
line per session into `/exp/mu2e/app/users/$USER/claude-code/log/usage.jsonl`.

Records have the columns of the shared table `usage.sessions`
(`../usage/sessions.sql`), the same shape the Slack bot and the goose harness
write (shared code: `../usage/usage_record.py`): counts, timings, model and
tool names only -- no user, working directory or content.

## Files

| File | Purpose |
|------|---------|
| `env.sh` | Source to put claude on PATH and activate HOME override |
| `setup.sh` | Idempotent install + config setup (source or run) |
| `setup-litellm.sh` | Configure the litellm.fnal.gov backend |
| `sync-mcp.sh` | Sync MCP servers from the internal registry |
| `sync_mcp_claude.py` | Python worker for sync-mcp.sh |
| `settings.json.example` | Tracked settings template (live settings outside this repo) |
| `hooks/block-commands.sh` | PreToolUse guardrail |
| `hooks/log-session.sh` | Usage logging hook (runs `log-session-claude.py`) |
| `../shared/mu2e.md` | Shared experiment context (CLAUDE.md + goose TOM) |
| `../shared/mikey-token.sh` | MIKEY token lookup for MCP sync (env → personal file → `getMu2eKey`) |
| `../shared/getMu2eKey` | Vendored copy of the CVMFS `getMu2eKey` (fallback when CVMFS is absent) |

## Testing a fresh install

To try `setup.sh` from scratch without touching your real setup, point the
harness at an empty directory:

```bash
CLAUDE_HARNESS_ROOT=/tmp/claude-test . setup.sh
```
