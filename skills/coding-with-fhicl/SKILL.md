```skill
---
name: coding-with-fhicl
description: Author, review, and troubleshoot Mu2e FHiCL configuration files for Offline, Production, and trigger workflows. Use when editing .fcl files, composing include chains, configuring modules/services, or debugging job configuration failures.
compatibility: Requires Mu2e offline environment, mu2einit, muse setup, and familiarity with Production/Offline repositories
metadata:
  version: "1.3.0"
  last-updated: "2026-02-18"
---

# Coding with FHiCL

## Overview

Use this skill when working on Mu2e configuration logic in `.fcl` files, including:

- Building new job configurations from existing templates
- Adjusting module parameters or service settings
- Creating clean include hierarchies for reusable configuration
- Debugging configuration parse/runtime failures
- Coordinating config changes across `Offline`, `Production`, and `mu2e-trig-config`

This skill prioritizes **safe, incremental config edits** and **fast validation loops**.

## Two Primary Work Categories

All work should be understood as one of these two categories:

### Category 1: C++ module configuration interface (Validated FHiCL)

When writing or updating module code in `Offline`, use **validated FHiCL** patterns (e.g. `fhicl::Atom`, `fhicl::Sequence`, `fhicl::Table`, `fhicl::OptionalAtom`) instead of ad hoc unvalidated parameter access.

For module classes, include a `Config` struct and define the parameters alias so help/description tooling works. For EDProducer modules, require:

```cpp
using Parameters = art::EDProducer::Table<Config>;
```

Reference example:

- `Offline/TrkPatRec/src/TimeClusterFinder_module.cc`

Validated interface benefits:

- Required/optional parameters are explicit
- Parameter names and defaults are documented in code
- `mu2e --print-description <ModuleClassName>` can expose help text
- Typos and schema mistakes are caught early

### Category 2: `.fcl` job configuration files

These files (many examples under `Production/JobConfig`) define runtime values that art resolves and passes into module configuration objects.

General Mu2e pattern to assume by default:

- Top-level job `.fcl` is usually in `Production`.
- That top-level file composes behavior by including module configuration defaults from `Offline/*/fcl/prolog.fcl`.
- Production provides job/campaign wiring; Offline prologs provide subsystem/module baseline settings.

Offline convention to follow:

- For modules in `Offline/<Subsystem>/src/*_module.cc`, there is generally a nominal/default stanza in `Offline/<Subsystem>/fcl/prolog.fcl`.
- Prefer deriving job configs from these prolog defaults, then applying small deltas in top-level job files.
- Some top-level configuration files are also maintained under Offline FHiCL areas (including `Offline.fcl` usage conventions in your environment).

Core mapping to remember:

1. `.fcl` defines module parameter tables and path wiring.
2. art parses/includes/expands the full configuration.
3. art materializes typed config objects from validated definitions.
4. Module constructor receives something like:

```cpp
art::EDProducer::Table<Config>& config
```

5. Module code reads values from `config` (or copied members) during execution.

Practical rule: whenever `.fcl` keys change, confirm matching names/types in the module `Config` struct.

Include/path resolution rule:

- The file passed to `mu2e -c <file.fcl>` can be anywhere (absolute or relative path).
- `#include` targets are resolved via `FHICL_FILE_PATH` (colon-separated search paths), typically configured by `muse setup`.
- If includes fail unexpectedly, inspect `FHICL_FILE_PATH` first.

---

## Quick Start

```bash
mu2einit
muse setup

# Run a configuration
mu2e -c path/to/config.fcl -n 1

# Show effective behavior quickly on a tiny event count
mu2e -c path/to/config.fcl -n 10

# Inspect effective expanded config
mu2e --debug-config expanded.fcl -c path/to/config.fcl --annotate

# Alternative dump/expand tool with source annotations
fhicl-dump -a path/to/config.fcl > expanded_annotated.fcl

# Inspect include search path used by fhicl/art
echo "$FHICL_FILE_PATH" | tr ':' '\n'

# Show validated parameter description for a module/service class
mu2e --print-description ModuleClassName
```

When changing config, prefer this loop:

1. Edit one logical block.
2. Run with `-n 1`.
3. Fix parser/startup errors.
4. Run with `-n 10`.
5. Expand only after basic success.

---

## Mental Model

FHiCL is a hierarchical configuration language used to wire services, producers, filters, analyzers, sources, and output modules.

Typical structure includes:

- `services`
- `source`
- `physics`
- `outputs` (or output module definitions)
- job-level controls (event count, scheduling flags, diagnostics)

Your configuration should answer three questions clearly:

1. **What runs?** (paths/modules)
2. **In what order?** (paths/schedules)
3. **With which parameters?** (psets/module config)

---

## Standard Workflow

### 1) Start from a known-good parent

- Copy a nearby working `.fcl` in `Production` or related area.
- Keep your first edit minimal (single parameter or module block).
- Avoid designing from scratch unless necessary.

### 2) Make include structure explicit

- Use includes to factor shared settings.
- Keep include graph shallow and readable.
- Document non-obvious overrides near the override site.

### 3) Apply local overrides last

- Keep default/shared psets in common includes.
- Put campaign, detector, or test-specific overrides in the top-level job config.
- Group overrides by subsystem to reduce merge conflicts.

### 4) Validate with tiny jobs

- `-n 1` catches parse/startup failures fast.
- `-n 10` catches obvious scheduling/module interaction issues.
- Increase event count only after startup is clean.

### 5) Coordinate cross-repo dependencies

- If config references module labels or parameter names tied to C++ code changes, ensure matching updates in `Offline`.
- If trigger menu generation affects generated FHiCL, coordinate with `mu2e-trig-config`.

### 6) Validate both sides of the contract

- **Code side**: validated `Config` struct includes required parameters and help comments.
- **FHiCL side**: keys and value types match the validated schema.
- **Runtime side**: expanded config (`--debug-config`) matches intent after includes and substitutions.

### 7) Use `fhicl-dump -a` for include/source tracing

For debugging complex include stacks, run:

```bash
fhicl-dump -a Production/Validation/nightly/reco_00.fcl > expanded_annotated.fcl
```

What it gives you:

- Fully expanded FHiCL after include/prolog/reference processing
- Per-line source annotation at end of each line (file and line number)
- Fast way to answer "where did this value come from?"

Interpretation rules:

- `# /full/path/file.fcl:NN` means this expanded value originated from that exact source line.
- `# ""` means same origin as the previous annotated line (annotation compression shorthand).
- If `@local::` / `@table::` are absent in expanded output, substitutions were resolved.

Debug workflow:

1. Generate annotated expansion.
2. Search for suspect key/module label in expanded file.
3. Read trailing annotation to jump to source of truth.
4. Fix in source file (not only in expanded output).
5. Re-run dump and then `mu2e -c ... -n 1`.

---

## Authoring Guidelines

### Naming and labels

- Use stable, descriptive module labels.
- Keep naming patterns consistent across related paths.
- Avoid one-off label conventions unless clearly justified.

### Parameter sets

- Prefer reusable psets for repeated module families.
- Keep pset names subsystem-specific and explicit.
- Avoid giant unstructured top-level blobs.

### Paths and schedules

- Keep path definitions compact and readable.
- Avoid hidden coupling between unrelated paths.
- Group similar processing stages logically.

### Comments

- Comment **why**, not **what**.
- Record assumptions (geometry, detector era, campaign mode).
- Note temporary settings clearly (for cleanup later).

### Diff hygiene

- Separate mechanical formatting edits from behavior changes.
- Prefer one conceptual change per commit.
- Keep unrelated subsystem edits in separate commits.

---

## Common Patterns

### Pattern A: Derive config from baseline

1. Copy baseline file.
2. Rename for purpose.
3. Apply minimal overrides.
4. Validate with `-n 1`.

### Pattern B: Add one module to an existing path

1. Define module block with clear label.
2. Insert module label into target path.
3. Validate startup and path execution.
4. Check downstream modules for expectations.

### Pattern C: Campaign toggle set

1. Keep default behavior in shared include.
2. Add compact override block in campaign file.
3. Validate differences by comparing logs/startup summaries.

### Pattern D: Dotted epilog overrides in top-level files

Mu2e top-level files (often in `Production`) may use two layers:

1. Main included body with nested tables/paths (often using `@table` or `@local` composition).
2. Final dotted assignments near the end for quick customization/override.

Example (`Production/Validation/nightly/reco_00.fcl`):

- `source.fileNames : [...]`
- `outputs.LoopHelixOutput.fileName: "reco_00.art"`

These dotted assignments override previously defined nested values from included files.

Verification recipe:

```bash
fhicl-dump -a Production/Validation/nightly/reco_00.fcl > /tmp/reco_00_annotated.fcl
grep -nE 'fileNames:|LoopHelixOutput:|fileName:' /tmp/reco_00_annotated.fcl | head
```

Expected evidence in annotations:

- `fileName: "reco_00.art"  # ./Production/Validation/nightly/reco_00.fcl:5`
- `fileNames: [ ... ]  # ./Production/Validation/nightly/reco_00.fcl:2`

---

## Troubleshooting Guide

### Validated-FHiCL contract mismatch

Symptoms:

- Startup exception when constructing module from FHiCL
- Missing required parameter errors
- Type conversion failures

Checks:

1. Compare `.fcl` key names to the validated `Config` names exactly.
2. Confirm scalar vs sequence/table expectations align.
3. Verify optional vs required intent (`Optional*` vs required types).
4. Use `mu2e --print-description <ModuleClassName>` to confirm expected interface.

### Parse errors at startup

Symptoms:

- Immediate job failure before event processing
- Messages indicating syntax or key-resolution issues

Checks:

1. Verify braces, commas, and quoting in edited block.
2. Confirm include paths and filenames.
3. Confirm referenced pset/module names exist.
4. Re-run with `-n 1` after each fix.

### Include/override confusion in deep configs

Symptoms:

- Value appears different from what top-level file suggests
- Unexpected module/path content after includes

Checks:

1. Run `fhicl-dump -a <config.fcl> > expanded_annotated.fcl`.
2. Find the resolved key in expanded output.
3. Use source annotation comment to locate defining include/prolog line.
4. Confirm whether later redefinitions intentionally override earlier values.

### Dotted epilog did not take effect

Symptoms:

- Expected custom value still appears as default from included file

Checks:

1. Confirm dotted key path exactly matches nested structure (`a.b.c` spelling/case).
2. Confirm override appears after the include/body it is meant to replace.
3. Run `fhicl-dump -a` and inspect annotation at resolved key.
4. If annotation still points to included file, override key path likely does not match.

### Include file not found / wrong file loaded

Symptoms:

- Startup error about missing include
- Include resolves to unexpected file version

Checks:

1. Confirm `muse setup` was run in the current shell.
2. Print `FHICL_FILE_PATH` and verify expected directories are present and ordered.
3. Use absolute path for the top-level `-c` file if current directory is ambiguous.
4. Re-run `fhicl-dump -a` and inspect annotation origins for the include result.

### Unknown parameter/module label

Symptoms:

- Config loads partially but fails when building module/service

Checks:

1. Confirm label spelling and case.
2. Confirm the referenced module type exists in the current build.
3. Check whether C++ parameter names changed in `Offline`.
4. Align FHiCL keys with current module expectations.

### Path/schedule behavior not as expected

Symptoms:

- Job runs but missing expected outputs
- Unexpected module order/effects

Checks:

1. Verify module labels are present in the intended path.
2. Check path names and schedule wiring.
3. Confirm filters are not dropping events earlier than expected.
4. Inspect output module selectors/routing.

### Environment mismatch issues

Symptoms:

- Config worked previously but now fails after environment changes

Checks:

1. Re-run `mu2einit` and `muse setup` in a fresh shell.
2. Confirm branch/workDir alignment across coordinated repos.
3. Check for old generated files or stale artifacts in test areas.

---

## Collaboration Checklist

Before opening a PR with FHiCL changes:

1. Run representative configs with small event counts.
2. Confirm no unrelated config noise in diff.
3. Note cross-repo dependencies in PR description.
4. Include exact command lines used for validation.
5. Call out any temporary toggles or known caveats.

---

## AI Assistant Behavior for This Skill

When this skill is activated, the assistant should:

1. Ask for the target `.fcl` and intended behavior change.
2. Propose minimal config edits before broad refactors.
3. Preserve existing include hierarchy and naming conventions unless asked to change them.
4. Recommend tiny validation runs (`-n 1`, then `-n 10`).
5. Flag likely cross-repo impacts (Offline/Production/mu2e-trig-config) when module or label contracts change.
6. Treat validated C++ config schema and `.fcl` values as a strict contract and check both sides.

When helping with module code, assistant should prefer patterns that include:

- `struct Config { ... };`
- validated parameter declarations (`Atom`, `Sequence`, `Table`, `Optional*`)
- `using Parameters = art::EDProducer::Table<Config>;` (for EDProducer modules)
- constructor signature and usage consistent with validated config handling

Do not:

- Suggest direct CMake-based build flow for Mu2e offline work.
- Mix unrelated subsystem config changes in one patch.
- Assume generated trigger config details without checking repository sources.

---

## Output Template

Use this template when proposing a FHiCL change:

```markdown
### Proposed FHiCL Change

**Goal**
- <one sentence>

**Files to edit**
- <Offline/..._module.cc>
- <path/to/file1.fcl>
- <path/to/file2.fcl>

**Edits**
1. <validated Config schema update in module code>
2. <matching .fcl key/value update>
3. <path/include/override adjustment>

**Validation commands**
```bash
mu2e --print-description <ModuleClassName>
mu2e --debug-config expanded.fcl -c <config.fcl> --annotate
fhicl-dump -a <config.fcl> > expanded_annotated.fcl
mu2e -c <config.fcl> -n 1
mu2e -c <config.fcl> -n 10
```

**Expected result**
- <startup passes / expected module in path / output present>

**Cross-repo impacts**
- <none | list>
```

---

## Suggested Future Reference Files

If this skill grows, split heavy content into:

- `references/fhicl-patterns.md` (idiomatic include/override examples)
- `references/fhicl-troubleshooting.md` (error-message-to-fix mapping)
- `references/fhicl-review-checklist.md` (PR reviewer checklist)

For v1, keep everything in this single file.

```