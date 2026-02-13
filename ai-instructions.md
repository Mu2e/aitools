````markdown
# AI Assistant Instructions - Mu2e Offline Software

## Quick Start

**Read this file first**, then see [skills/offline-instructions.md](skills/offline-instructions.md) for detailed build procedures, repository structure, and workflows.

## What is This Workspace?

This is the **Mu2e offline software** ecosystem - simulation, reconstruction, and analysis for a high-energy physics experiment at Fermilab. The workspace contains multiple coordinated Git repositories that are built together.

**Important**: This is NOT a single-repo CMake project. Despite the presence of `CMakeLists.txt` files, builds are orchestrated by the **Muse** system (or Smack for newer platforms). Never run `cmake` directly.

### Current Architecture: Phase 2 Hybrid

Mu2e is transitioning from UPS (deprecated Unix Package System) to spack for dependency management:

- **Build system**: muse + SCons (orchestrates builds)
- **Dependencies**: spack (art, root, geant4, kinkal, btrk, etc.)
- **Adapter**: envsets (shell scripts that activate spack and make deps available)
- **Timeline**: Moving to Phase 3 (pure spack + cmake) within ~1 year

This is why you'll see both `CMakeLists.txt` files and `SConscript` files - the codebase is in transition.

## Repository Organization

This workspace may contain several Mu2e repositories:

- **Offline** - Core simulation and reconstruction
- **Production** - Job configuration scripts  
- **mu2e-trig-config** - Trigger configuration
- **Other repos** - Analysis packages, utilities

These are part of a ~60 repository ecosystem maintained by ~100 developers. Not all repos follow the same conventions.

## Universal Guidelines

These apply across all Mu2e offline repositories:

### Code Quality

- **Strict warnings**: Code is compiled with `-Werror` and strict warning flags
- **C++20 standard**: Use modern C++ idioms
- **No warnings**: Even small changes can break builds due to strict compiler settings
- **Test locally**: Always verify builds complete successfully

### Coding Style

- Follow Mu2e coding conventions (see wiki)
- Use art framework patterns for modules
- Configuration via FHiCL files, not hard-coded parameters
- Geometric units: mm, MeV, ns (follow existing patterns)

### Communication

- Be concise and direct
- Reference line numbers and file paths when discussing code
- Post build issues in `#help-bug` or `#offlineSoftware` Slack channels

### Safety

- Never commit credentials or sensitive paths
- Be careful with disk usage (builds are multi-GB)
- Test on appropriate machines (use build nodes, not interactive nodes)

## How to Build

**TL;DR for offline repos:**

```bash
mu2einit                              # Setup environment (always first)
muse setup                            # Setup build (once per shell)
build                                 # If alias exists
# OR
muse build -j 20 --mu2eCompactPrint  # Standard build command (for 16+ core machines)
```

**Parallelism note:** The `-j 20` flag runs 20 parallel compilation jobs. This is appropriate for:
- Dedicated build nodes with 16+ cores (like `build02`)
- Non-shared, high-resource machines

For other systems, adjust based on available cores:
```bash
# Check your core count:
nproc

# Then build with appropriate parallelism:
muse build -j $(nproc) --mu2eCompactPrint    # Use available cores
muse build -j 2 --mu2eCompactPrint           # Conservative for shared systems
```

See [skills/offline-instructions.md](skills/offline-instructions.md) for complete build workflows, environment setup, and troubleshooting.

## Special Cases

### Multi-Repository Awareness

When working in this workspace, changes may affect multiple repositories:
- Modifying data products in Offline affects Production configurations
- Trigger changes in Offline require corresponding mu2e-trig-config updates
- Always consider cross-repository dependencies

### Online vs Offline

There are TWO development communities:
- **Offline** (this workspace): simulation/reconstruction, uses muse/smack
- **Online**: DAQ system, uses different tools and conventions
- These groups have diverged in practices - don't assume online docs apply here

### Grid Submission

Users may need to create tarballs for grid jobs:
```bash
muse tarball  # Creates tarball in /mu2e/data/users/$USER/
```

## Contributing Changes via GitHub

Mu2e uses the **Fork and Pull workflow** for contributions:

1. **Fork** the upstream repo to your personal GitHub account
2. **Clone** your fork locally: `git clone git@github.com:USERNAME/repo.git`
3. **Set up remotes**:
   - `origin` → your fork (SSH, for pushing)
   - `mu2e` → upstream original (HTTPS, for pulling updates)
4. **Create a branch** for your changes: `git checkout -b feature/my-changes`
5. **Commit and push** to your fork: `git push origin feature/my-changes`
6. **Create a Pull Request** on GitHub UI from your fork → upstream
7. **Review and merge** — upstream maintainers handle approval

This workflow keeps the upstream repo clean while enabling distributed contributions.

## For AI Assistants

When helping users:

1. **Always check** [skills/offline-instructions.md](skills/offline-instructions.md) for detailed procedures
2. **Environment**: Assume `mu2einit` has been run before any muse commands
3. **Never suggest cmake directly** - Use muse commands
4. **Check current state**: Run `muse status` to understand setup
5. **Terminal persistence**: Environment persists in shell after `muse setup`
6. **Common aliases**: Users may have shortcuts like `build` or `mu2einit`

## Documentation and Skills

For detailed procedures and reference material, consult the **skills/** directory:

- **Detailed offline procedures**: [skills/offline-instructions.md](skills/offline-instructions.md)
- **Complete Muse reference**: [skills/muse-reference.md](skills/muse-reference.md)
- **Smack/Spack reference**: [skills/smack-reference.md](skills/smack-reference.md)

External references:
- **Mu2e wiki**: https://mu2ewiki.fnal.gov/wiki/Main_Page
- **Muse wiki**: https://mu2ewiki.fnal.gov/wiki/Muse
- **Computing help**: https://mu2ewiki.fnal.gov/wiki/Computing

## Improving These Instructions

These skills are iteratively improved based on real-world usage. If you encounter:
- Commands that fail or produce unexpected results
- Missing details or unclear instructions
- Gaps in workflows

**Please report issues:**

1. **File a GitHub Issue**: https://github.com/Mu2e/aitools/issues
   - Include the command that failed
   - Copy the error message
   - Describe what you were trying to accomplish
   - Share conversation context if helpful

2. **Submit a Pull Request**: If you've figured out a fix or improvement
   - Fork the aitools repository
   - Update the appropriate skill file
   - Submit PR with clear description

Your feedback helps improve the experience for all users.

---

**Note**: Documentation may be out of date. When in doubt, verify with actual command output and current behavior.

````
