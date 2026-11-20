# claudebox

`claudebox` runs [Claude Code](https://code.claude.com) inside a locked-down
apptainer container. Unlike the [claude-code harness](../claude-code/README.md),
whose guard rails only stop accidents, this is real isolation: the session
sees `/cvmfs` (read-only), **one work directory** (read-write) and a sandbox
home of its own -- no `$HOME`, no `/pnfs`, no other `/exp` areas, no Kerberos
ticket, no tokens unless you pass them in.

```bash
claudebox run [DIR]                 # interactive session, DIR (default: .) writable
claudebox agent NAME -f task.txt    # detached, self-driving agent
claudebox list | log NAME -f | stop NAME
claudebox help                      # all subcommands and options
```

Needs apptainer and CVMFS (the container image is
`/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-dev-el9:devel`). Its
home, runs and logs go to `/exp/mu2e/app/users/$USER/claudebox/`
(`$CLAUDEBOX_ROOT`).

## With the Mu2e harness

A bare `claudebox` knows nothing about Mu2e. With the claude-code harness
loaded (`. ../claude-code/env.sh`), the `claudebox` command goes through
[`../claude-code/claudebox.sh`](../claude-code/claudebox.sh), which adds the
harness's Claude Code binary, your litellm backend and model mapping, the
Mu2e MCP servers with their key, and the Mu2e context -- see
[Sandboxed: claudebox](../claude-code/README.md#sandboxed-claudebox).

## Where this copy comes from

`claudebox` is Ray's work. The script here is a copy of `/cvmfs/mu2e.opensciencegrid.org/bin/claudebox`
(version 0.1, as published 2026-09-25), added unchanged so the harness and
the sandbox can be reviewed, versioned and deployed together. The harness
wrappers run `agentbox` (below), which is this script with a few changes;
`$CLAUDEBOX_BIN` selects another, this copy or the CVMFS one included.

## Example: the same box for goose (`agentbox`, `goosebox`)

`agentbox` is a proposal for Ray, not a replacement: his `claudebox` with
the Claude-specific parts moved into `claude_*` functions and a second
set, `goose_*`, next to them. Called as `goosebox` (a link to it) it runs
[goose](../goose/README.md); under any other name, or with `--tool claude`,
Claude Code. The goose harness uses it for its `goosebox` command
([`../goose/goosebox.sh`](../goose/goosebox.sh) adds the Mu2e MCP servers,
the Mu2e context and usage logging, as `claudebox.sh` does for Claude
Code), and so does the claude-code harness for `claudebox`, where it shows
its messages under that name.

```bash
diff claudebox agentbox             # exactly what moved or was added
./goosebox run [DIR]                # goose session in the sandbox
./goosebox agent NAME -f task.txt   # goose run, detached; list/log/stop/clean as before
```

About two thirds of `claudebox` is not about Claude at all (sandbox root,
apptainer arguments, binds, secrets, credential imports, agent lifecycle)
and is untouched. What an agent has to supply is nine small functions and
a renderer for its stream output; they are listed under `TOOL ADAPTERS` in
the script. For Claude the generated apptainer command is the same as
`claudebox`'s, apart from the order of two `--env` options.

How goose differs inside the box:

| | Claude Code | goose |
|---|---|---|
| executable | native `claude`, one file bound in | `goose`, one file bound in (links only libc/libstdc++) |
| settings | `settings.json` copied once | `config.yaml` copied on every launch, without its MCP extensions |
| credentials | `ANTHROPIC_*` or the OAuth login, copied in | provider key from the environment or `secrets.yaml`, passed as environment; none at all on vllm.fnal.gov |
| `--mcp FILE` | `--mcp-config`, `${VAR}` expanded by Claude | same file; goose does not expand `${VAR}`, so the servers are written with values filled in to a private config under the session's `/tmp`, removed afterwards |
| `--model`, permissions | command-line options | `GOOSE_MODEL`, `GOOSE_MODE` |
| `--budget` | `--max-budget-usd` | not available (refused with a hint) |
| unattended run | `claude -p --output-format stream-json` | `goose run -i - --output-format stream-json` |

Further changes in `agentbox`, none specific to goose:

- **Usage logging.** `$AGENTBOX_POST_HOOK` names an executable that the
  script runs on the host, outside the sandbox, when a `run` session or an
  agent ends. [`log-usage.sh`](log-usage.sh) is such a hook: it finds the
  sessions the box wrote and hands them to the harness's usual loggers, so
  sandboxed sessions -- Claude or goose, interactive or agent -- land in the
  same `usage.jsonl` as any other. Nothing has to run inside the container.
  The claude-code wrapper sets the hook when the box script supports it.
- **A fix for agents: `--cleanenv`.** `apptainer exec instance://...` does
  not apply the instance's `--containall` to the environment, so in the
  original an agent runs with the caller's whole host environment -- every
  exported token included. `run` is not affected. `agentbox` adds
  `--cleanenv` to that one call. This is worth taking into `claudebox`
  whatever happens to the rest of the example.

- **What runs on the host is kept apart from what the box can write.**
  The script's Python helpers run isolated (`python3 -I`), so a `yaml.py`
  or `json.py` left in the launch directory is not imported on the host;
  files written into the box home (which a session can fill with links) are
  created without following a link; and `${NAME}` in an `--mcp` file is
  filled only from what was passed for the run, not from the whole
  environment. The first two also apply to `claudebox`.

Tried on mu2egpvm with goose 1.57.0 on vllm.fnal.gov: an `agent` run in
the container (work directory readable, `/exp` absent, `HOME=/home/box`, no
Kerberos cache), `log`, `list`, an `--mcp` server receiving the filled-in
header, and the usage record. With Claude Code through the harness wrapper
on litellm: an `agent` run and a `run` in print mode, including a call to a
Mu2e MCP server and the usage records.

Interactive sessions (2026-11-19). Claude Code in `agentbox`: a Mu2e MCP
call, a file written in the work directory, only the work directory visible
under `/exp`, and the usage record after exit. goose: the same isolation and
the file. goose through `goosebox.sh`, as an `agent` run on vllm.fnal.gov: a
Mu2e MCP call, the Mu2e context, and the usage record. `$USER` is not set
inside the box.
