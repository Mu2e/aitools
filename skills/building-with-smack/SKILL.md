```skill
---
name: building-with-smack
description: Smack build system reference for Phase 3 Mu2e workflow with pure spack and cmake. Use when working with future smack-based builds or transitioning from muse.
compatibility: Future Phase 3 system, not yet in production
metadata:
  version: "1.0.0"
  last-updated: "2026-02-13"
---

# Smack/Spack Build System - Reference

## Overview

**Smack** is the Mu2e wrapper around **spack** (Mu2e spack). It provides a muse-like interface for managing spack environments and building code with CMake. It is the **Phase 3** target build system and will replace muse/SCons over time.

### Phase Context

- **Phase 2 (current default)**: muse + SCons builds, dependencies from spack envsets
- **Phase 3 (transition in progress)**: smack + spack + CMake (pure spack builds)

## Prerequisites

Always run:
```bash
mu2einit
```

Verify:
```bash
printenv MU2E
# /cvmfs/mu2e.opensciencegrid.org
```

## Core Smack Commands

### Listing available builds

```bash
smack list
```
Shows available spack-based builds on CVMFS (e.g., simjob, muse envs, ops).

### Setup a published build

```bash
smack setup simjob
smack setup simjob-mdc2020ap
smack setup ops
```

These commands activate pre-built spack environments from CVMFS. Use these for running code without local builds.

### Create a local build area

```bash
export MYSS=myproject
export MYENV=prof

mu2einit
smack local $MYSS/$MYENV
```

This creates:
- A local spack working area
- A base spack environment (default: muse)

### Activate the local environment

```bash
cd $MYSS
source ./setup-env.sh
spack env activate $MYENV
```

### Add and develop packages

```bash
spack add Offline@main +g4
spack develop Offline@main

spack add production@main
spack develop production@main

spack add mu2e-trig-config@main
spack develop mu2e-trig-config@main
```

### Concretize and build

```bash
spack concretize -f 2>&1 | tee c_$(date +%F-%H-%M-%S).log
spack install       2>&1 | tee i_$(date +%F-%H-%M-%S).log
```

### Return to a local env later

```bash
mu2einit
cd $MYSS
source ./setup-env.sh
spack env activate $MYENV
```

## Common Workflows

### 1. Use a published build (no local build)

```bash
mu2einit
smack setup simjob
mu2e -n 100 -c Production/Validation/ceSimReco.fcl
```

### 2. Local build of Offline + Production + mtc

```bash
export MYSS=myproject
export MYENV=prof

mu2einit
smack local $MYSS/$MYENV
cd $MYSS
source ./setup-env.sh
spack env activate $MYENV

spack add Offline@main +g4
spack develop Offline@main
spack add production@main
spack develop production@main
spack add mu2e-trig-config@main
spack develop mu2e-trig-config@main

spack concretize -f
spack install
```

### 3. Multi-env (prof + debug) using same source

```bash
mu2einit
MYSS=myproject
smack subspack $MYSS
cd $MYSS

smack subenv prof
smack subenv debug

# prof window
source setup-env.sh
spack env activate prof
# add packages, concretize, install

# debug window
source setup-env.sh
spack env activate debug
# add packages with build_type=Debug
```

### 4. Local build with custom dependency (example: KinKal)

```bash
spack rm kinkal
spack add kinkal@main
spack develop kinkal@main
spack concretize -f
spack install
```

## Key Notes

- Smack is a thin wrapper around spack. It mainly automates env creation and activation.
- In spack, **builds are driven by CMake**, not SCons.
- `spack develop` checks out repo sources into the environment directory.
- Use `spack install` after edits (incremental builds are supported).
- Spack errors can be non-obvious; use `spack clean -a` if builds behave oddly.

## Troubleshooting

**Environment view out of sync:**
```bash
rm -rf $SPACK_ENV/.spack-env/view $SPACK_ENV/.spack-env/._view
spack env view regenerate $MYENV
```

**Conflicting GCC runtime in view:**
Edit `$MYENV/spack.yaml`:
```yaml
view:
  default:
    root: ".spack-env/view"
    exclude:
    - gcc-runtime@11.4.1
```

## References

- **Spack wiki**: https://mu2ewiki.fnal.gov/wiki/Spack
- **Muse wiki**: https://mu2ewiki.fnal.gov/wiki/Muse

---

**Note**: This guide is based on current Phase 3 documentation and may evolve. Always verify with actual command output and current Mu2e computing guidance.

````
