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
wrapper uses this copy and falls back to the CVMFS one; `$CLAUDEBOX_BIN`
selects another.
