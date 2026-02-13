````markdown
# Mu2e Offline Software - AI Assistant Instructions

## Overview

Mu2e is a high-energy physics experiment at Fermilab searching for muon-to-electron conversion. This document describes the **offline** software ecosystem (simulation, reconstruction, and analysis), which is distinct from the **online** DAQ system.

The offline software comprises ~1 million lines of C++ code across ~60 repositories maintained by ~100 collaborators. This instruction file covers the core coordinated repositories used for offline work.

## Repository Structure

### The "Big Three" Core Repositories

These three repos are tightly coordinated and typically checked out together:

1. **Offline** - Core simulation, reconstruction, and analysis framework
   - Primary executables and libraries
   - Detector geometry and algorithms
   - Data products and analysis tools
   - Location: `https://github.com/Mu2e/Offline`

2. **Production** - Configuration and scripts for production workflows
   - FCL (FHiCL) configuration files for running Offline executables
   - Job submission scripts
   - Grid production procedures
   - Location: `https://github.com/Mu2e/Production`

3. **mu2e-trig-config (mtc)** - Trigger configuration and generation
   - Trigger path FHiCL generation from JSON menus
   - Online reconstruction configuration
   - Data logger configuration
   - Python script: `generateMenuFromJSON.py`
   - Location: `https://github.com/Mu2e/mu2e-trig-config`

**Relationship**: Production and mtc provide configuration/scripts that control executables built in Offline. You typically cannot do meaningful work in Offline without the other two.

### Other Offline Repositories

- **EventNtuple** - Analysis ntuple generation (depends on Offline)
- **DQM** - Data quality monitoring (depends on Offline)
- Others as needed for specific analysis tasks

### Online Repositories (NOT covered here)

The DAQ/online system is independently maintained:
- `otsdaq-*` repos
- `artdaq-*` repos
- Different build systems and conventions
- See online documentation separately

## Dependencies

### Major External Dependencies

Offline depends on standard HEP software and custom collaboration tools:

**Standard HEP packages:**
- `geant4` - Detector simulation
- `root` - Data analysis framework
- `gsl` - GNU Scientific Library
- `boost`, `clhep` - Standard C++ libraries
- `xerces-c`, `sqlite`, `postgresql` - Data handling

**Art framework** (Fermilab's event processing framework):
- `art` - Core framework
- `canvas` - Data product handling
- `messagefacility` - Logging
- `fhicl-cpp` - Configuration system
- `cetlib`, `cetlib-except` - Utilities

**Collaboration-developed:**
- `kinkal` - Kalman filter track fitting
- `btrk` - Track reconstruction
- `artdaq-core-mu2e` - Raw data format definitions

All dependencies are managed via:
- **UPS** (legacy, SL7)
- **Spack** (current, AL9+)

## Build System

### Current: Muse (SCons-based) - Phase 2 Hybrid Architecture

**Muse** is Mu2e's build orchestration system. It wraps SCons and manages multi-repo builds.

**Note on architecture**: Mu2e is in a transition period (Phase 2):
- SCons builds the code (still using original build system)
- Dependencies come from spack (modern package management replacing deprecated UPS)
- Envsets are adapters that bridge muse/SCons to spack dependencies
- Phase 3 (planned ~2026): Pure spack + cmake throughout

This explains why you see both CMakeLists.txt and SConscript files in the code.

**→ For complete Muse documentation, see [muse-reference.md](muse-reference.md)**

#### Key Muse Concepts

1. **workDir** - Your project directory containing checked-out repos
2. **Backing builds** - Pre-built releases on CVMFS that you link against
3. **Environment sets (envsets)** - Coordinated versions of dependencies (art, root, geant4, etc.)
4. **Build output** - Always under `build/` subdirectory (out-of-source)

#### Essential Muse Commands

```bash
# Initial setup (always required first)
mu2einit

# See available published builds (does not create links)
muse list

# Link to a backing build (before muse setup)
muse backing HEAD                    # Latest CI build
muse backing Offline v13_00_11       # Specific release

# Setup environment (run once per shell session)
muse setup                           # From workDir
muse setup /path/to/workDir         # From anywhere
muse setup -q debug                 # Debug build
muse setup -q prof                  # Production build (default)

# Build (after muse setup)
muse build -j 20 --mu2eCompactPrint # Parallel build with compact output

# Check status
muse status                          # Show current setup and builds

# Create grid tarball
muse tarball                         # For job submission
```

#### Common Workflows

**Full local build (all three repos):**
```bash
cd /path/to/workDir
git clone https://github.com/Mu2e/Offline
git clone https://github.com/Mu2e/Production
git clone https://github.com/Mu2e/mu2e-trig-config
muse setup
muse build -j 20 --mu2eCompactPrint
```

**Build with backing (recommended for development):**
```bash
cd /path/to/workDir
muse backing HEAD                    # Link to latest CI build
git clone git@github.com:<username>/MyAnalysisRepo
muse setup
muse build -j 20 --mu2eCompactPrint
```

**Build with backing build:**
```bash
cd /path/to/workDir
muse backing HEAD
muse setup
muse build -j 20
```

**Return to existing build:****
```bash
cd /path/to/workDir
muse setup                           # Re-establishes environment
# Now ready to build or run
```

#### Important Notes

- **Never run cmake directly** - Despite CMakeLists.txt files, muse drives SCons for builds
- **mu2einit required** - Must be run before any muse commands (sets up UPS/environment)
- **One setup per shell** - Cannot change setup without starting new shell
- **Build machine** - Use `mu2ebuild02` for builds, not gpvm nodes (use `-j 20`)
- **Persistent environment** - Once `muse setup` is run, environment persists for that shell session

### Future: Smack (Spack-based)

**Smack** is the next-generation build system using Spack + CMake:
- Available for AL9+ platforms
- Full transition expected ~2025-2026
- Commands similar to muse but with spack backend
- Currently in testing/limited use

See: https://mu2ewiki.fnal.gov/wiki/Spack

**Smack quickstart (Phase 3 preview):**
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

**→ For complete Smack/Spack documentation, see [smack-reference.md](smack-reference.md)**

## Running Offline Code

After building:

```bash
# Run art executable
mu2e -c Production/Validation/ceSimReco.fcl -n 100

# Generate trigger menu from JSON
python mu2e-trig-config/python/generateMenuFromJSON.py \
  -mf mu2e-trig-config/data/physMenu.json \
  -o gen -evtMode all
```

## Branch Strategy

Branches have semantic meaning in Mu2e workflows:

- `main` - Development head, continuous integration
- `Run1*` - Production branches for Run 1 data taking (e.g., `Run1B`)
- Named branches - Feature development or campaign-specific work

**Current setup** in this workspace:
- Offline: `Run1B`
- Production: `head` (likely detached HEAD or branch alias)
- mu2e-trig-config: `head`

## Common User Aliases

Developers often use aliases for convenience:

```bash
# Standard build alias
alias build='muse build -j 20 --mu2eCompactPrint'

# Standard initialization
alias mu2einit='source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh'
```

## Build Output Locations

```
workDir/
├── Offline/              # Source code
├── Production/           # Source code
├── mu2e-trig-config/     # Source code
└── build/                # ALL build output (can be symlink to other disk)
    └── <stub>/           # e.g., al9-prof-e29-p090
        ├── Offline/
        │   └── lib/      # Compiled libraries
        ├── Production/
        └── mu2e-trig-config/
```

Build output directory is named by: `<OS>-<profile>-<compiler>-<envset>`

## Grid Submission

Prepare tarball for grid jobs:

```bash
cd workDir
muse setup
muse tarball
# Tarball path will be printed
# Use with mu2eprodsys: --code=/path/to/Code.tar.bz2
```

## CVMFS Locations

Pre-built releases and dependencies:

- Releases: `/cvmfs/mu2e.opensciencegrid.org/Musings/`
- CI builds: `/cvmfs/mu2e-development.opensciencegrid.org/museCIBuild/`
- Dependencies: `/cvmfs/mu2e.opensciencegrid.org/DataFiles/`
- Muse envsets: `/cvmfs/mu2e.opensciencegrid.org/DataFiles/Muse/`

Always available via CVMFS (network-mounted filesystem).

## Troubleshooting

**Build fails with memory issues:**
- Don't build on gpvm nodes (mu2egpvm01-07)
- Use mu2ebuild02 with `-j 20`

**Wrong environment/dependencies:**
- Check `muse status` for current setup
- Ensure `mu2einit` was run
- Start new shell if you need different setup

**Muse can't find repos:**
- Each repo needs `.muse` file in root (already present in Big Three)
- Check `muse status` to see recognized repos

**Build doesn't see changes:**
- Ensure you're in the right shell (where `muse setup` was run)
- Check build timestamps: `muse status`
- Try clean build: `muse build -c && muse build -j 20`

## Documentation Links

- **Main wiki**: https://mu2ewiki.fnal.gov/wiki/Main_Page
- **Muse documentation**: https://mu2ewiki.fnal.gov/wiki/Muse
- **Spack/smack**: https://mu2ewiki.fnal.gov/wiki/Spack
- **GitHub workflow**: https://mu2ewiki.fnal.gov/wiki/GitHubWorkflow
- **Computing help**: https://mu2ewiki.fnal.gov/wiki/Computing

## For AI Assistants

When helping with Mu2e offline work:

1. **Environment**: Always assume `mu2einit` has been run before any muse commands
2. **Build system**: Use muse commands, never run cmake/scons directly
3. **Repository context**: Remember these three repos work together
4. **Terminal persistence**: Environment variables persist within a shell session after `muse setup`
5. **Common pattern**: Most development uses backing builds + local modifications
6. **Compilation**: Use alias `build` if available, or `muse build -j 20 --mu2eCompactPrint`
7. **Grid submission**: Users may need tarballs for production jobs

When you execute commands:
- Run `muse status` to understand current setup
- Check git branches with `git status` or `git branch`
- Build output size is significant (~GB) - be aware of disk usage
- Compilation can take 5-30 minutes depending on scope

## Code Style and Practices

- C++20 standard
- Follow Mu2e coding conventions (see wiki)
- Use art framework patterns for modules
- FHiCL for configuration (not hard-coded parameters)
- Follow geometric units conventions (mm, MeV, ns)

---

**Last updated**: 2026-02-07  
**Maintained by**: Offline software coordinators  
**Questions**: Post in #help-bug or #offlineSoftware on Mu2e Slack

````
