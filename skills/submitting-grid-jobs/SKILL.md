---
name: submitting-grid-jobs
description: Reference for pitfalls Mu2e users hit when submitting their own grid jobs directly with jobsub_submit (jobsub_lite). Use when helping a user write or debug a jobsub_submit script, when ifdh cp / gfal-copy back to /pnfs scratch fails with 403 Forbidden or Permission refused, when deciding --need-storage-modify, --tar-file-name, --disk or the number of jobs, when a muse tarball is rebuilt or re-uploaded on every submit, when a submit seems to hang after "Submitting N jobs", or when deciding between the grid and interactive GPVM nodes. Not for mu2eprodsys production submission.
compatibility: Describes submission from a Mu2e interactive node with the user's own Kerberos ticket and Fermilab token, jobsub_lite, and a muse build (muse tarball)
metadata:
  version: "1.0.0"
  last-updated: "2026-10-14"
---

# Submitting Grid Jobs with jobsub_submit

Pitfalls that cost real submissions when sending your own analysis or reco jobs to the
grid with `jobsub_submit`. The `mu2eprodsys` wrapper (mu2egrid) handles most of this for
you; see [building-with-muse](../building-with-muse/SKILL.md) for that route. This skill
covers the direct `jobsub_submit` route. Dataset/file inputs via SAM are in
[finding-data-sam](../finding-data-sam/SKILL.md).

Check flag spellings against `jobsub_submit --help` on your installed version.

**Scope.** This is a reference for users submitting their own jobs. It does not
cover an AI assistant submitting jobs or using grid credentials itself. When using
this skill, an assistant reviews scripts, explains failures and proposes commands
and flags; the user builds the tarball and submits.

## Checklist before every submission

1. Is the work worth the grid at all? (see "Grid or interactive node?")
2. Tarball built **once**, path passed to the submit script.
3. `--need-storage-modify=/mu2e/...` present if any job writes to `/pnfs`.
4. Jobs sized from a measured seconds/file to a chosen job length; `--disk` scaled to match.
5. Worker/driver scripts survive `set -e` (see "Shell gotchas").

## 1. Build the muse tarball once

`muse tarball` is expensive: it compresses the whole build into a file of several hundred
MB. Do not call it from inside a submit script that you run repeatedly.

```bash
cd /path/to/your/muse/workspace      # directory with the muse build
muse setup
muse tarball                         # prints the Code.tar.bz2 path
```

- Rebuild only when the Offline code, FHiCL, or bundled scripts change.
- Record the printed path and pass it to the submit script as an argument (for example
  `submit.sh /path/to/Code.tar.bz2 ...`). Submit scripts should require an existing
  tarball path rather than build one.
- Extra files can be bundled: `muse tarball *.txt data/*.root`
  (see [building-with-muse](../building-with-muse/SKILL.md)).
- By default the tarball is written under `/exp/mu2e/data/users/$USER/museTarball`
  (`museTarball.sh`), so old tarballs count against your data-area quota; delete stale ones.
- Run `jobsub_submit` from a scratch/data directory, not from your app area or a source
  checkout: jobsub writes temporary tarball copies into the current directory.

## 2. Grant write scope: `--need-storage-modify`

Jobs submitted with `jobsub_submit` get a reduced token. By default it is read-only for
`/mu2e` storage (plus job staging). Copy-out of outputs to dCache scratch then fails with
errors such as `HTTP 403 Permission refused (destination)` from `gfal-copy`, or
`403 Forbidden` / `File exists` from `ifdh cp`, even though the directory exists and has
correct permissions.

Request write scope explicitly, using the **logical `/mu2e/...` path**:

```bash
jobsub_submit -G mu2e --role=Analysis \
  --need-storage-modify=/mu2e/scratch/users/<user>/<outdir> \
  ...
```

Notes:

- jobsub_lite always drops `storage.modify` from the job token and adds back only the
  paths given with `--need-storage-modify`. It never reads output paths from the job
  script, so always pass the flag on the command line.
- The directory must cover everything the job writes; a copy-out outside it still gets 403.
- A `/pnfs/mu2e/...` path is converted to `/mu2e/...` by jobsub_lite with a warning
  (`lib/token_mods.py`); write the logical form to keep the output clean.
- A path outside the `storage.modify` scope of your own token fails at submit time with
  `PermissionError: Unable to add 'storage.modify:...' scope`, not later in the job.
- When adapting someone else's `jobsub_submit` command (for example a production
  validation script run under a production role), add the flag if your jobs write to
  scratch.
- Diagnose a 403 by running one small test job that prints the scopes of the job token
  (decode the token file named by `$BEARER_TOKEN_FILE` inside the job), not by resubmitting
  the full campaign.

## 3. Size jobs from a measured rate

Each job pays fixed overhead: the tarball upload (minutes, first submit only), queue wait,
and setup on the worker (`source setupmu2e-art.sh`, `setup ifdhc`; tens of seconds).
Very short jobs spend a large share of their time on overhead, while very long jobs lose
more work when one fails. Choose a target execution time per job (`T_job`, in seconds)
that balances the two.

Size from a **measured** rate, not a round number. Run a handful of files interactively
and time them:

```
n_jobs = ceil(n_files * seconds_per_file / T_job)
files_per_job = ceil(n_files / n_jobs)
```

- Re-measure for each input type; data and MC (or different file sizes) differ.
- Scale `--disk` with files per job when inputs are staged to the worker:
  `disk >= files_per_job * MB_per_file` plus output and headroom. A flat `--disk=5GB` is
  fine for a few files and fails outright with ~100 staged files. Jobs that exceed their
  requested disk, memory or lifetime go into a held state.
- Prefer streaming inputs or fewer files per job over very large `--disk` requests; a big
  disk request matches fewer worker slots and the jobs may sit idle in the queue.
- Typical resource flags: `--memory=2500MB --disk=10GB --expected-lifetime=1h` (check the
  defaults of your jobsub_lite version).

## 4. Grid or interactive node?

Decide the machine **before** sizing jobs. If the work is seconds per file (for example a
vectorised Python/ntuple analysis), the interactive GPVMs may finish it sooner: they are
meant for short to moderate jobs (see the ComputingLogin wiki page and
[running-on-gpvms](../running-on-gpvms/SKILL.md)). The grid overhead (tarball, queue,
worker setup) dwarfs such work, and packing it into jobs long enough to amortise that
overhead would force you to stage many files per job.

| Cost per file | Route |
|---|---|
| Seconds | Interactive nodes, within their intended use |
| Minutes, or needs many cores/long jobs | Grid, sized per section 3 |

Warning sign: if the derived `--disk` is above roughly 10 GB, the job is staging a lot to
do little; reconsider the route rather than requesting more disk. Re-ask the question
after speeding up an analysis; the right answer can change.

## 5. Do not re-upload the tarball with `dropbox://`

`--tar-file-name dropbox://<path>/Code.tar.bz2` (the underscore spelling `--tar_file_name`
is also accepted; `tardir://<dir>` tars a directory first) copies the tarball to RCDS/CVMFS and
unpacks it on the worker (location in `$INPUT_TAR_DIR_LOCAL`).

Observed in the jobsub_lite client source (`lib/tarfiles.py`): on **every** submit it first
rewrites the whole tarball to `./<name><pid>.tbz2` in the current directory (single-threaded
bz2, setting modes to at least 0755), hashes that copy, and only then checks whether the
content is already on RCDS. An unchanged tarball is not re-uploaded, but a large muse
tarball (hundreds of MB) can still cost many minutes of apparent hang after
`Submitting N jobs...`.

Once a tarball is published, resubmit with the RCDS directory itself:

```bash
jobsub_submit ... \
  --tar-file-name /cvmfs/fifeuser<N>.opensciencegrid.org/sw/mu2e/<hash> \
  ...
```

- The `<hash>` path is printed in the first job's `.out` file.
- A plain path (no `dropbox://` prefix) is passed through as is, and the worker unpacks
  it the same way.
- For the **first** upload, submit from a data directory (see section 1) and clean up any
  leftover `*.tbz2` after an interrupted submit.
- Do not assume the hash equals the SHA-256 of your original file; the hash is of the
  rewritten copy.
- jobsub_lite refuses a tarball that contains any single file over 1 GiB
  (`RCDS_MAX_FILE_SIZE`); keep large data files out of the tarball.

## 6. Shell gotchas under `set -e`

Driver and worker scripts commonly use `set -e`; setup steps can return non-zero even
though the environment is fine. More interactive-node shell gotchas are in
[running-on-gpvms](../running-on-gpvms/SKILL.md).

```bash
set -e
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh || true
cd /path/to/your/muse/workspace      # directory containing the muse build
muse setup || true
```

- `source setupmu2e-art.sh` can return non-zero (an internal `return 1` when the
  production DB is not configured), which aborts the script before `mu2e` is available.
- Muse versions before v4_16_00 could exit non-zero after `muse setup` in a workspace
  containing `mu2e-trig-config` (`export: 'MU2E-TRIG-CONFIG_INC=...' not a valid
  identifier`); newer versions convert the hyphen. `|| true` is harmless either way.
- Run `muse setup` in the muse build workspace (where the `.muse` file lives), or from
  anywhere as `muse setup <workDir>`. Run from another directory without the argument,
  it does not set up your build and `mu2e` is not added to `PATH`.
- On the worker, after the tarball is unpacked, set up with `source Code/setup.sh` (or
  `muse setup Code`) and use `|| true` there as well.

## Quick diagnosis

| Symptom | Likely cause | Fix |
|---|---|---|
| `403` / `Permission refused (destination)` on copy-out to `/pnfs` | Read-only job token | Add `--need-storage-modify=/mu2e/scratch/users/<user>/<dir>` (section 2) |
| Same 403 despite the flag | Destination outside the flag's directory | Make the flag directory cover every output |
| Submit fails with `PermissionError: Unable to add 'storage.modify:...'` | Path outside your own token scope | Use a directory under `/mu2e/scratch/users/<user>` |
| Submit sits silent after "Submitting N jobs" | `dropbox://` retarring a large tarball | Resubmit with the `/cvmfs` RCDS path (section 5) |
| Script dies right after `source setupmu2e-art.sh` or `muse setup` | `set -e` with non-zero return | Append `|| true` (section 6) |
| `mu2e` not found after `muse setup` | Ran outside the workspace without an argument | `cd` to the workspace or `muse setup <workDir>` |
| Jobs held | Exceeded `--disk`, `--memory` or `--expected-lifetime` | Re-measure and resize (section 3) |
| Jobs idle, never start | Very large `--disk` request | Fewer files per job, stream, or use interactive nodes |
