---
name: running-on-gpvms
description: Reference for shell and session pitfalls that Mu2e users hit when running their own scripts and jobs on the interactive GPVM nodes. Use when helping a user work out why a job they started produced nothing or died, on set -u aborting muse setup, a nohup job killed when the login closed, "Key has expired" in an old tmux session, stat/ls on /pnfs being slow, pyenv ana not found, hadd or root not found, Ceph quota or data-area placement, or which machine (GPVM, build node, grid) suits a piece of work.
compatibility: Describes the Mu2e interactive GPVM nodes and the /cvmfs/mu2e.opensciencegrid.org setup scripts; tmux is optional
metadata:
  version: "1.0.0"
  last-updated: "2026-10-16"
---

# Running on the GPVMs

Practical pitfalls for users running their own scripts and jobs on the shared Mu2e
interactive nodes (`mu2egpvm01`-`07`). Many of them fail **silently**: the job seems
to start, nothing runs, and there is no error. If a job "started fine" but produced
nothing, the checklist at the end goes through the usual causes in order.

**Scope.** This is a reference for the user's own work on the nodes. It does not
cover an AI assistant starting jobs, opening ssh connections or handling Kerberos
credentials itself. When using this skill, an assistant explains the likely cause
and proposes the commands; the user runs them.

## 1. Shell basics

### `set -u` goes after environment setup

`setupmu2e-art.sh`, `muse setup` and Spack setup scripts reference unset
variables. `set -u` before them aborts the whole shell, and the error vanishes
if setup output is sent to `/dev/null`. Source the setup first, then enable
`set -u`.

### `muse setup` under `set -e`

Muse versions before v4_16_00 could print
``export: `MU2E-TRIG-CONFIG_INC=...' not a valid identifier`` (hyphenated
package name) and return non-zero even though the environment was set up
correctly; newer versions convert the hyphen. `|| true` is harmless either way
in scripts using `set -e`:

```bash
muse setup || true
```

### `pyenv ana` is a shell function

`pyenv` on the GPVMs is a shell function defined by the Mu2e setup script, not
the standalone pyenv version manager. It does not exist until the script is
sourced, so a script (or any new non-login shell) has to source and activate
together:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && pyenv ana
```

Afterwards `python3` has the analysis stack (`uproot`, `awkward`, `numpy`,
`matplotlib`, `scipy`, `pyutils`). The system Python on the nodes lacks these.

### Which setup puts `hadd` and `root` on PATH

`hadd` and `root` come from the art/ROOT Spack environment that `muse setup`
loads, not from the base setup script or the analysis Python environment:

| after `source setupmu2e-art.sh` and ... | `hadd` / `root` |
|---|---|
| nothing else | not on PATH |
| `pyenv ana` | not on PATH |
| `muse setup ops` | not on PATH |
| `muse setup` in a Muse working directory | on PATH |
| `muse setup SimJob` (any published release) | on PATH |

To merge ntuples from a `pyenv ana` shell, open a separate shell with
`muse setup` (or `muse setup SimJob`) for the `hadd`, rather than sourcing a
ROOT `thisroot.sh` by hand. Checked on an AL9 GPVM, 2026-10-16.

### FHiCL `#include` does not take absolute paths

Generated wrapper FCLs that `#include "/abs/path/base.fcl"` fail at startup.
Prepend the directory to `FHICL_FILE_PATH` and include by relative name; see
"Gotchas" in [coding-with-fhicl](../coding-with-fhicl/SKILL.md).

## 2. Jobs that should outlive your login

A job started in the background (`cmd &`) belongs to your login shell. When the
login closes, the shell can take the job down with it (SIGHUP) before it writes
anything: the log stays 0 bytes and `ps` shows nothing. To leave a job running
after you log out, start it with `nohup`, or inside a `tmux` (or `screen`) session
that you detach from:

```bash
nohup bash job.sh </dev/null > job.log 2>&1 &
# or
tmux new -d -s myjob 'bash job.sh > job.log 2>&1'
```

The same applies to a job started with a one-line `ssh <node> "..."` command,
which is a login that closes as soon as the command returns. In that form, also
keep `nohup` and the `&` around the job only, with all three streams redirected,
or the `ssh` command waits until the job ends:

```bash
ssh <node> "cd /path/to/workdir && ( nohup bash job.sh </dev/null > job.log 2>&1 & )"
```

Before starting a job again because it looks stuck, check whether it is still
running (`pgrep -af job.sh`, `tail job.log`); otherwise it runs twice.

## 3. Kerberos tickets in long-lived tmux sessions

Each login to a GPVM gets its own copy of your Kerberos ticket in a new cache
file (`/tmp/krb5cc_<uid>_*`). The copy expires on its own schedule and is not
renewed in place, and a tmux session keeps using the cache from the login that
started it.

So a tmux session older than the ticket lifetime can hold an **expired** ticket
while a fresh login on the same node shows a valid one. Symptoms: `Key has
expired` errors from the home directory (`/nashome/...`) or `.bashrc`, and new
tmux windows that close before running anything. A tmux server started long ago
passes the same old credentials to every new session it starts.

What helps:

1. For a job that will run for hours inside tmux, start the tmux session from a
   fresh login, so it begins with a fresh ticket.
2. Check the ticket **inside** the session with `klist`; a check from a separate
   login looks at a different cache.
3. If new sessions on a node keep failing with `Key has expired`, the tmux server
   there is holding stale credentials. Exiting all your tmux sessions on that
   node (or `tmux kill-server`, which ends all of them) lets the next one start
   fresh. Logging in to another GPVM also works.
4. `k5push <node>` (see the Authentication wiki page) pushes your current ticket
   to another node. Whether it refreshes the cache an already running tmux
   session holds is not confirmed.

## 4. /pnfs access

`/pnfs` is a dCache namespace backed by a database. `find`, `ls -l`/`ls -lr`
and `stat` fetch the full record of every file and are slow and expensive; a
plain `ls` reads only the directory record (see the Dcache wiki page). Many
nodes alias `ls` to `ls --color=auto`, which is slower; use `\ls` to bypass the
alias.

- In scripts, list or check existence with `ifdh ls <path>` (or `ifdh ll`), or use
  `samweb locate-file` / metacat for dataset queries.
- Copy with `ifdh cp`, or read via xrootd.
- art/`mu2e` jobs read their input art files straight from the `/pnfs` path
  (`mu2e -s /pnfs/.../file.art`, `-n N` to limit events); interactive runs do not
  need the inputs prestaged to `/tmp`.

See [finding-data-sam](../finding-data-sam/SKILL.md) for catalogue queries.

## 5. Where data goes, and quota

- **App area** (`/exp/mu2e/app/users/<user>/`): code, build products, scripts.
  No data, log or ROOT files (see the Disks wiki page).
- **Data area** (`/exp/mu2e/data/users/<user>/`) or `/pnfs/mu2e/scratch/users/<user>/`:
  ntuples, art files, plots, logs, test outputs. This applies to small test runs
  too.

The `/exp` areas are CephFS with per-directory quotas. Hitting one does not
produce a clear error: art aborts on a ROOT write, driver scripts lose their
exit lines, and the job looks like an external kill. Before a multi-GB run,
compare free quota with the expected output size; if a job dies with nothing in
its log, check quota first.

```bash
d=/exp/mu2e/data/users/<user>
getfattr --only-values -n ceph.dir.rbytes "$d"          # bytes used
getfattr --only-values -n ceph.quota.max_bytes "$d"     # quota
```

`mu2eQuota` (see the Disks wiki page) prints the same summary. Use these rather
than `du`, which is slow on CephFS. Usage accounting lags
deletes by a few tens of seconds. Intermediate `.art` files accumulate quickly
when a test driver does not delete them; remove them once the ntuples they
produced are done.

## 6. Which machine

Per the ComputingLogin wiki page, the `mu2egpvm` nodes are shared quad-core
interactive machines for developing, compiling and short to moderate jobs; large
jobs belong on the grid (see [submitting-grid-jobs](../submitting-grid-jobs/SKILL.md)).
`mu2ebuild02` is only for building code and running a few simple tests on the
build, not for analysis or anything that takes more than a few minutes.
`mu2egpvm01` also runs infrastructure monitoring, nightly validation and
production submission.

## Checklist for a job that "started but did nothing"

1. Is it still running, or already gone? (`pgrep -af <name>` on the node)
2. Does the log exist and have bytes? A 0-byte log suggests a job killed when
   the login closed (section 2).
3. Was `set -u` placed before setup, or (with older Muse) `muse setup` run under
   `set -e` without `|| true`?
4. In a tmux session, does `klist` show a valid ticket? `Key has expired` points
   at a stale session (section 3).
5. Free space in the target quota (section 5).
6. Wrapper FCL includes use relative names with `FHICL_FILE_PATH` set
   (see [coding-with-fhicl](../coding-with-fhicl/SKILL.md)).
