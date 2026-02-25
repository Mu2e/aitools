---
name: building-with-muse
description: Complete muse build system reference for Mu2e. Use when building with muse, managing envsets, creating tarballs, or understanding Phase 2 architecture with spack dependencies.
compatibility: Requires muse installed, access to spack envsets
metadata:
  version: "1.0.0"
  last-updated: "2026-02-13"
---

# Muse Build System - Complete Reference

## Overview

**Muse** is Mu2e's build orchestration system that wraps SCons and manages multi-repository builds. It allows building small user repos against pre-built Offline releases, or building everything locally together.

### Architecture: Phase 2 Hybrid (UPS → Spack Transition)

**Historical context:**
- Muse originally linked to dependencies via **UPS** (Unix Package System)
- UPS is now deprecated - Fermilab unified on **spack** for package management
- Phase 2 (current): Hybrid approach
  - Build system: muse/SCons (for Big Three and analysis repos)
  - Dependencies: spack (art, root, geant4, kinkal, btrk, etc.)
  - Adapter: envsets (shell scripts that bridge muse→spack)
  
**How it works:**
1. Envset runs `smack setup muse-al9-prof-e29-p090`
2. `smack` performs `spack env activate` on pre-built spack environment
3. This makes all dependencies (art, root, geant4) available via standard paths
4. muse/SCons then builds code against these dependencies
5. Libraries linked via RPATH to spack directory structure

**Future: Phase 3 (Pure Spack)**
- Build system: smack (muse-like wrapper around spack)
- Build system: spack + cmake (instead of muse + scons)
- All code built and managed by spack
- Planned transition: ~within 1 year
- Currently available for some repos (KinKal, BTrk, full Offline builds)

### Key Concepts

- **workDir** - Your project directory containing checked-out repos and the `build/` subdirectory
- **Backing build** - A pre-built release on CVMFS that you link against (avoids rebuilding everything)
- **Envset** (environmental set) - Coordinated versions of art, root, geant4, and other dependencies
- **Musing** - A published Muse build area on CVMFS (e.g., "Offline", "SimJob")
- **Build stub** - Directory name encoding build configuration (e.g., `al9-prof-e29-p090`)

### Design Philosophy

Muse is designed around a **workDir** which:
1. Contains the repos to be built
2. Provides a place to build the code (`build/` subdirectory)
3. Maintains persistent state of what you're building

Only one workDir per process. After `muse setup`, the configuration is locked until you start a new shell.

## Prerequisites

**Always run first:**
```bash
mu2einit
```

This command:
- Sets up the muse UPS product
- Adds `$MUSE_DIR/bin` to PATH
- Creates the `muse` alias (actually sources the script)
- Exports `MUSE_ENVSET_DIR=/cvmfs/mu2e.opensciencegrid.org/DataFiles/Muse`

**To verify mu2einit has been run:**
```bash
printenv MU2E
# Should output: /cvmfs/mu2e.opensciencegrid.org
```
If this returns empty, you need to run `mu2einit`.

## Essential Commands

### muse backing

**Purpose**: Link to a pre-built release to avoid rebuilding large parts of the software locally.

**When to use**: Optional - useful when you only have a small analysis repo and want to link against a pre-built Offline. Not needed if your workDir already contains all repos you're building (e.g., the Big Three).

**When to run**: Before `muse setup`, while organizing your workDir.

**See available builds:**

To just view what's available without creating a link:
```bash
muse list     # Shows all published musings
```

To see backing build options (but only when you want to create a link):
```bash
cd workDir
muse backing     # Lists recent published releases and CI builds
```

Output shows:
- Recent published releases (tagged versions)
- Recent CI builds (continuous integration from main branch)

**Link to a build:**
```bash
# Latest CI build
muse backing HEAD

# Specific release
muse backing Offline v10_15_00

# Specific CI build
muse backing main/a5002688_27b1aba9_15d4fbf4

# Published musing
muse backing SimJob
```

**What it does:**
- Creates a soft link `workDir/backing` pointing to the build area
- During `muse setup`, includes that build in link/include paths
- Allows you to build only your local changes against a complete Offline

**Notes:**
- Command does NOT select prof/debug - that's determined during `muse setup`
- If backing link already exists, command updates it (requires new shell for new setup)
- Can link to local builds: `muse backing /path/to/other/workDir`

### muse setup

**Purpose**: Configure environment, set paths, and prepare for building.

**When to use**: 
- After organizing repos in workDir (git clone, muse backing, etc.)
- When returning to work in a later shell session

**Basic usage:**
```bash
cd workDir
muse setup

# Or from anywhere:
muse setup /path/to/workDir
```

**With qualifiers:**
```bash
muse setup -q debug                  # Debug build
muse setup -q prof                   # Production build (default)
muse setup -q e21                    # Force specific compiler
muse setup -q e21:trigger:debug      # Multiple qualifiers
muse setup -q p000                   # Force specific envset
```

**What it does:**
1. Sets environment variables (MUSE_WORK_DIR, etc.)
2. Determines machine flavor (sl7, al9, etc.)
3. Parses qualifiers
4. Determines and sets up spack/dependencies via envset
5. Analyzes repos and determines link order
6. Adds workDir to bin, link, include paths
7. Includes backing build areas if present

**Output example:**
```
Build: prof     Core: al9 e29 p090    Options:
```
- `prof` - Build type (prof=production/optimized, debug=debugging)
- `al9` - OS flavor (AlmaLinux 9)
- `e29` - Compiler version (gcc 13.x)
- `p090` - Envset permanent version 90

**Build directory naming:**
All build output goes to `workDir/build/<stub>/` where stub encodes:
- `al9-prof-e29-p090` = AlmaLinux9, prof build, e29 compiler, p090 envset
- `al9-debug-e29-p090` = Same but debug build
- Each stub is independent; can maintain both prof and debug builds

**Inside each stub:**
```
build/al9-prof-e29-p090/
├── Offline/
│   ├── lib/          # Compiled libraries
│   ├── bin/          # Compiled executables
│   └── tmp/          # Build artifacts
├── Production/
├── mu2e-trig-config/
└── .musebuild        # Build timing (start/end times)
```

**Important:**
- Can only run once per shell session
- Cannot be reversed - start new shell to change setup
- Must run from command line (not in scripts) to modify environment

### muse status

**Purpose**: Show current setup state and available builds.

**Before muse setup:**
```bash
cd workDir
muse status
```
Shows:
- Existing builds in `build/` directory
- Build timestamps (start and completion)
- Build errors if any

**After muse setup:**
```bash
muse status          # Can run from anywhere after setup
```
Shows:
- Which build is currently active
- MUSE_WORK_DIR location
- MUSE_REPOS list
- Environment configuration
- Link order

**Verbose mode:**
```bash
muse status -v       # More detailed environment info
```

**Sample output (after setup):**
```
  existing builds:
     al9-prof-e29-p090         ** this is your current setup **
          Build times: 02/07/26 19:55:20 to 20:00:58

  MUSE_WORK_DIR = /exp/mu2e/app/users/rlc/temp
  MUSE_REPOS =  Production mu2e-trig-config Offline
  MUSE_ENVSET =  p090
  MUSE_STUB =  al9-prof-e29-p090
  MU2E_SPACK =  true
```

### muse build

**Purpose**: Compile and link the code using SCons (driven by muse).

**Requirements**: Must run `muse setup` first in same shell.

**Will fail with:** `ERROR - MUSE_WORK_DIR not set - "muse setup" must be run first`

**Basic usage:**
```bash
muse build -j 20 --mu2eCompactPrint
```

**Sample output (incremental build):**
```
Selected class -> art::Wrapper<art::Assns<mu2e::StepPointMC, mu2e::StrawGasStep, void> > for ROOT: ...
Compiling Offline/Mu2eG4/src/constructStoppingTarget.cc
Linking build/al9-prof-e29-p090/Offline/lib/libmu2e_Mu2eG4.so
scons: done building targets.
```

**Common options:**
- `-j N` - Parallel build with N jobs (20 recommended on mu2ebuild02)
- `--mu2eCompactPrint` - Less verbose output
- `-c` - Clean build (remove all build products)
- `--mu2ePyWrap` - Build Python wrappers for select C++ classes

**Build specific target:**
```bash
# Build specific library
muse build -j 20 build/al9-prof-e29-p090/Offline/lib/libmu2e_HelloWorld_HelloWorld_module.so

# Generate GDML geometry files
muse build GDML
```

**Where builds go:**
All build output goes under `workDir/build/<stub>/` where stub encodes build configuration:
- `al9-prof-e29-p090` = AlmaLinux9, prof, e29 compiler, p090 envset
- `al9-debug-e29-p090` = Same but debug build

Example structure after build:
```
build/al9-prof-e29-p090/
├── Offline/
│   ├── lib/           # Compiled .so libraries
│   ├── bin/           # Executables
│   └── tmp/           # Build artifacts
├── Production/        # (may be empty if scripts only)
├── mu2e-trig-config/  # (includes generated FCL)
└── .musebuild         # Records build start/end times
```

**Notes:**
- Can run from any directory after `muse setup`
- Backing builds are NOT rebuilt (only local repos)
- Build records start/stop times visible in `muse status`

**Recommendations:**
- Use mu2ebuild02 for building (NOT gpvm nodes)
- Use `-j 20` on build02
- Avoid building on mu2egpvm01-07 (slow, memory issues)

### muse tarball

**Purpose**: Create tarball for grid job submission.

**Usage:**
```bash
muse setup
muse tarball

# Include additional files
muse tarball *.txt data/*.root
```

**Output:**
```
Tarball: /mu2e/data/users/$USER/museTarball/tmp.XXXXXXX/Code.tar.bz2
```

**Submit to grid:**
```bash
setup mu2egrid
mu2eprodsys ... --code=/path/to/Code.tar.bz2
```

**What's included:**
- Build libraries from workDir
- If backing link points to CVMFS: link included
- If backing link points to local disk: full backing build included
- setup_pre.sh and setup_post.sh if present in workDir

**On the grid:**
Tarball unrolls in default directory, setup with:
```bash
muse setup Code
# or
source Code/setup.sh
```

## Advanced Features

### Musings - Published Builds

**Purpose**: Setup and use pre-built releases directly without local builds.

**View available musings:**
```bash
muse list
```

This shows all published Muse build areas organized by package/purpose.

**Available musing categories include:**
- **Offline** - Core Offline library
- **SimJob** - Production backed by Offline (common for simulation/reconstruction jobs)
- **EventNtuple**, **TrkAna**, **Stntuple** - Analysis packages with pre-built components
- **Analysis**, **DataJob**, **Pass1** - Campaign-specific builds
- **Production** - Production configuration only

**Setup a musing:**
```bash
# Use current version of a musing
muse setup Offline

# Use specific version
muse setup Offline v13_00_11
muse setup SimJob Run1Bac

# Use current version (shorthand)
muse setup TrkAna
```

**After setup:**
Libraries ready to use immediately:
```bash
mu2e -n 100 -c Production/Validation/ceSimReco.fcl
```

**Latest CI build:**
- Listed in `muse list` under each package as `(current)` or with timestamps
- Most recent CI builds also shown in `muse backing` output

### Custom Envsets

**What they are:** Envsets are shell script adapters that configure the muse/SCons build environment by activating a pre-built spack environment containing all required dependencies.

**The key command in every envset:**
```bash
smack setup muse-al9-prof-e29-p090
```
This:
- Activates spack environment for those exact versions
- Sets up library paths, include paths, PKG_CONFIG, etc.
- Makes all dependencies accessible to SCons via standard paths

**Why envsets matter:**
- Decouples version management from muse/SCons build
- Allows testing different dependency versions without rebuilding all repos
- Manages compiler selection (e29 = gcc 13.x)
- Coordinates build profiles (prof = optimized, debug = debugging)

**Example envset contents (p090):**
Specifies exact versions:
- gcc compiler: e29
- art suite: v3_15_00  
- KinKal: v03_05_00
- Root: v6_32_06 (AL9) or v6_28_10a (SL7)
- Geant4: v4_11_3_p02
- Plus 20+ other packages (canvas, cetlib, boost, gsl, tbb, etc.)

**Envset selection (priority order):**
1. User forces choice: `muse setup -q p090`
2. File `$MUSE_WORK_DIR/.muse` contains: `ENVSET p090`
3. Offline repo has `.muse` with: `ENVSET p090`
4. Any other local package with `.muse` recommendation
5. `$MUSE_WORK_DIR/muse/uNNN` exists (use highest)
6. Use highest published from `$MUSE_ENVSET_DIR`

**Creating a custom envset (experts only):**
Only if testing new compiler, art version, or other dependency changes:
```bash
mkdir -p muse
cp /cvmfs/mu2e.opensciencegrid.org/DataFiles/Muse/p090 muse/u001
# Edit for different spack environment or compiler flags
vi muse/u001
muse setup -q u001
muse build -j 20
```

**Important notes:**
- Each spack environment must be pre-built on CVMFS (or locally)
- Editing compiler flags requires understanding spack requirements
- Most users should use published envsets (pNNN) - they're tested and stable

### Link Order

**Purpose**: Preserve one-pass linking Mu2e policy (libraries linked in correct dependency order).

**Default link order**: Centrally maintained for all known repos.

**Override for custom repos:**
```bash
cd workDir
mkdir -p muse
echo "Ana1 Ana2" > muse/linkOrder
cat $MUSE_ENVSET_DIR/linkOrder >> muse/linkOrder

muse setup
muse status
# Shows: MUSE_LINK_ORDER = Ana1 Ana2 Tutorial Offline
```

**Rules:**
- Repos not in central link order go first by default
- Backing builds come after local repos
- This allows local repos to override backing builds

## Common Workflows

### 1. Full Local Build (Three Repos)

```bash
cd /path/to/workDir
git clone https://github.com/Mu2e/Offline
git clone https://github.com/Mu2e/Production
git clone https://github.com/Mu2e/mu2e-trig-config

mu2einit
muse setup
muse build -j 20 --mu2eCompactPrint

# Run
mu2e -c Production/JobConfig/beam/beam_g4s1.fcl -n 100
```

### 2. Analysis Repo Against Backing Build

```bash
cd /path/to/workDir
mu2einit

muse backing HEAD
git clone git@github.com:<username>/MyAnalysis

muse setup
muse build -j 20 --mu2eCompactPrint
```

### 3. Partial Offline Build with Backing

```bash
cd /path/to/workDir
mu2einit

muse backing HEAD
# Clone only the Offline subdirectories you need to modify
# (Note: Full partial checkout via mgit is no longer supported)

muse setup
muse build -j 20 --mu2eCompactPrint
```

### 4. Two Builds (Prof and Debug) from Same Source

**Window 1 (prof):**
```bash
cd workDir
mu2einit
muse setup -q prof
muse build -j 20 --mu2eCompactPrint
```

**Window 2 (debug):**
```bash
cd workDir
mu2einit
muse setup -q debug
muse build -j 20
```

Output goes to different directories:
- `build/al9-prof-e29-p090/`
- `build/al9-debug-e29-p090/`

### 5. Return to Existing Build

```bash
# Later session
cd /path/to/workDir
mu2einit
muse setup                          # Re-use existing config
# Now ready to build or run
```

## Troubleshooting

**"MUSE_WORK_DIR not set"**
- Run `muse setup` first
- Must be in same shell session

**"Invalid build stub"**
- Check `muse status` (before setup) to see existing builds
- May need to delete old build: `rm -rf build/al9-old-stub/`

**Build hangs or slow**
- Not on mu2ebuild02? (use it for full builds)
- Too many jobs? Try `-j 10` or `-j 5`
- Check disk space: `df -h`

**Wrong compiler or dependencies**
- Check `muse status` for current envset
- Start new shell to change setup

**Can't find backing build**
- Run `muse list` to see available musings
- Try `muse backing HEAD` for latest CI build

## References

- **Muse wiki**: https://mu2ewiki.fnal.gov/wiki/Muse
- **Link order defaults**: `$MUSE_ENVSET_DIR/linkOrder`
- **Envset locations**: `/cvmfs/mu2e.opensciencegrid.org/DataFiles/Muse/`

---

**Last updated**: 2026-02-07

