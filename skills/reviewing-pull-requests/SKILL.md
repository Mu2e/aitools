---
name: reviewing-pull-requests
description: Perform effective code review for Mu2e pull requests. Use when reviewing PRs, assessing risk, checking cross-repo impacts, validating tests/builds, and producing actionable reviewer feedback with severity and evidence.
compatibility: Requires git access, Mu2e offline context, and ability to run targeted checks when needed
metadata:
  version: "1.7.0"
  last-updated: "2026-08-07"
---

# Reviewing Pull Requests

## Purpose

Use this skill to review pull requests in a way that is:

- Context-aware for Mu2e Offline/Production/mu2e-trig-config workflows
- Actionable for authors (clear findings, impact, and fixes)
- Proportional to risk (deep where needed, light where safe)

## Scope

These conventions are intended for Mu2e **offline** repositories only.

Apply this skill's conventions to:

- `Offline`
- `Production`
- `EventNtuple`
- `EventDisplay`
- `DQM`
- `Tutorial`
- `PassN`
- `RefAna`
- `ArtAnalysis`

For other repositories, treat these conventions as out of scope unless the PR explicitly asks to apply them. Other repos may be online, personal, or minor projects with different standards.

---

## Standard Review Context Packet

When asking an AI reviewer to review a PR, provide this minimum packet first.

1. **Intent**: what behavior is changing and why.
2. **Scope**: files/modules affected; what is explicitly out-of-scope.
3. **Risk areas**: physics behavior, data products, config compatibility, performance, memory, threading.
4. **Validation evidence**: exact commands run, build/test status, representative outputs.
5. **Environment**: branch, platform, build mode (prof/debug), dependency assumptions.
6. **Cross-repo links**: related changes in `Offline`, `Production`, `mu2e-trig-config`.
7. **Acceptance criteria**: what must be true for approval.

If this packet is missing, ask for missing items before issuing strong conclusions.

---

## Mu2e-Specific Review Priorities

### 1) Correctness and science intent

- Does the change preserve intended physics behavior?
- Are defaults and thresholds justified?
- Any silent behavior changes in reconstruction or filtering?

### 2) Configuration contracts (FHiCL)

- If module config changed, does validated FHiCL schema remain coherent?
- For EDProducer modules, expect validated pattern and parameters alias:

```cpp
using Parameters = art::EDProducer::Table<Config>;
```

- Are `.fcl` keys aligned with `Config` names and types?

### 3) Cross-repo consistency

- Do code changes require corresponding `Production` updates?
- Do trigger/config generation changes require `mu2e-trig-config` updates?
- Are referenced module labels and paths still valid end-to-end?

### 4) Build and operational safety

- Will this pass strict compiler settings (`-Werror`) in expected environments?
- Any likely runtime failures due to missing services/modules or config keys?
- Any obvious performance regressions in hot paths?

### 5) Maintainability

- Is the change minimal and focused?
- Is naming clear and consistent with nearby code?
- Are assumptions documented where non-obvious?

### 6) Simplification and efficiency (never gates approval)

Review the changed code through a simplify/optimize lens and report —
do not apply — opportunities as findings:

- **Dead or write-only state**: members, config knobs, or containers
  written but never read; per-event or per-subrun work whose product
  nothing consumes.
- **Duplication with an existing single home**: logic re-implemented
  where a shared helper, accessor, or prolog table already exists —
  grep for the existing home before suggesting a new one.
- **Work at the wrong cadence**: per-event computation of
  subrun/job-constant values; repeated lookups hoistable out of hot
  loops.
- **Complexity without payoff**: a simpler idiom with identical
  behavior — especially one that derives a value from an invariant
  instead of reconstructing it through a fragile chain.

Severity cap: 🟡 [S2] when the complexity hides risk or real cost,
⚪ [S3] otherwise. These findings NEVER gate the decision — "avoid
requiring unrelated cleanup for approval" applies in full; a review
that is otherwise 🟢 approve stays 🟢.

Examples from practice: a write-only `std::set` rebuilt every subrun
after its only reader was removed (Offline #1908); replacing a fragile
positional cluster↔MC pairing with `cluster.diskID()` — simpler AND
removes the failure mode (Offline #1911).

---

## Rules

Source of truth: `https://mu2ewiki.fnal.gov/wiki/CodingStandards`.
If this skill conflicts with that page, follow the wiki.

Reviewers should enforce these high-impact rules:

- Use Mu2e file extensions: `.hh` and `.cc` for Mu2e code.
- Require changes to be compatible with the Mu2e baseline language standard: `-std=c++20`.
- No inter-module communication outside `art::Event` (except EDFilter true/false behavior).
- Include only headers actually needed; avoid speculative includes.
- Do not use `using` directives/declarations in headers; fully qualify types in headers.
- Require header guards on all headers.
- Avoid macros except approved uses (header guards, architecture selection, DEFINE_ART_* macros, message facility macros, debug enabling).
- Keep Mu2e classes in the `mu2e` namespace unless coordinated with software team.
- Require explicit first-order library dependencies in build files.
- Forbid linkage loops between libraries.
- Require clean compile at required warning levels (subject to approved external exceptions).
- Avoid raw `new`/`delete` patterns unless forced by external APIs; prefer safe ownership.
- Enforce data-product rules: no public data, no non-rebuildable pointers, and no MC info inside `RecoDataProducts`.
- Do not cache `art::Handle`/`art::ValidHandle`/`GeomHandle`/`ConditionsHandle` across events.
- Prefer `const` and `override` where applicable.
- For runtime errors, use `cet::exception` with meaningful category/message; do not use `assert` for production runtime control flow.
- Protect production prints with a verbosity flag or message facility.

---

## Recommendations

Source of truth: `https://mu2ewiki.fnal.gov/wiki/CodingStandards`.
If this skill conflicts with that page, follow the wiki.

Reviewers should strongly encourage these recommendations:

- Keep comments focused on intent; avoid code-history comments in source.
- Match local conventions when touching existing files, unless correcting major violations.
- Prefer straightforward, "vanilla" C++ constructs over clever or highly compact patterns in long-lived code maintained by part-time contributors.
- Favor readability and maintainability over compactness or micro-optimizations unless performance data shows the optimization is necessary.
- Prefer clear naming and consistent capitalization; avoid Hungarian notation in normal cases.
- Prefer private data with accessors for broadly used/event-data classes.
- Keep class declarations short; move long function bodies to `.cc` unless inlining is justified.
- Prefer one statement per line.
- Prefer pre-increment (`++i`) over post-increment (`i++`) when equivalent.
- Avoid ambiguous `operator<` definitions for types with multiple meaningful sort orders; prefer named comparator functions.
- Avoid `std::pair` where a named struct improves readability.
- Prefer ordered includes: local interface, local project, non-standard libs, near-standard libs, C++ stdlib, C headers.
- Use CLHEP units/constants with explicit qualification (for example `CLHEP::mm`), especially for short names.
- Follow Mu2e data-product access patterns and validate FHiCL consistency (`fhicl-dump -a`) when config behavior changes.

---

## Local Conventions

Capture and enforce project-local patterns here, even when they are not universal C++ style rules.

### Include Guard Naming

- Canonical style uses project/path words plus file base name with `_hh` suffix.
- Current Mu2e convention example:

```cpp
#ifndef GeneralUtilities_FooBar_hh
#define GeneralUtilities_FooBar_hh
// ...
#endif
```

- Repository prefix rule:
- In `Offline`, omit the repo prefix from include guards.
- In other repos, include the repo name as a prefix in the guard token.

Examples:

- `Offline/GeneralUtilities/inc/FooBar.hh` -> `GeneralUtilities_FooBar_hh`
- `Production/.../MyHeader.hh` -> `Production_<Path>_MyHeader_hh`
- `mu2e-trig-config/.../TrigThing.hh` -> `mu2e_trig_config_<Path>_TrigThing_hh`

Reviewer check:

- Flag headers whose include guards do not follow the repository-specific naming convention.
- Default severity: `S2` (raise if collision or multiple-include bugs are observed).

---

## Review Workflow

1. **Read PR intent** and restate expected behavior changes.
2. **Check PR hygiene** and, if needed, provide a polite reminder labeled as best practice:
  - Keep the PR targeted to a single topic.
  - Provide a meaningful PR description (intent, scope, and validation summary).
3. **Scan changed files** for high-risk categories (interfaces, config, data products, paths).
4. **Check contracts** (code <-> FHiCL <-> job config).
5. **Verify evidence** (tests/build commands and outputs).
6. **Report findings** with severity, evidence, and suggested fix.
7. **Summarize residual risk** and approve/request changes accordingly.
8. **Publish the review** per "Publishing the Review" below — posted
   automatically where auto-post is enabled, staged and reported where
   it is not.

---

## Publishing the Review

A review is drafted to a local file under `$PR_REVIEW_DIR` (fallback
`~/pr_reviews/`) so it can be written, re-read and corrected with
ordinary tools before anyone sees it. Whether that draft is then posted
automatically depends on opt-in.

### Enabling auto-post

**Default: draft only.** Publishing a review writes to a colleague's PR
under the invoking user's name, so it is not something a skill update
should switch on for someone. Check once per run:

```
printenv PR_REVIEW_AUTOPOST
```

Auto-post is enabled when that is `1`/`true`, or when the invoking
instruction says to post (a scheduled sweep or an explicit "review and
post" request). Otherwise finish at the staged draft, report its path,
and tell the user `/post-pr-review <N>` publishes it.

To opt in permanently, `export PR_REVIEW_AUTOPOST=1` in your shell
profile. To opt in for one review, ask for "review and post".

### Gates

Posting is **fail-closed**: every gate below must pass before `gh` is
invoked. A failed gate means stop and report, not post-anyway.

1. **Naming gate.** The draft's own header must name the PR being
   posted to, repo included (`Mu2e/<repo>#<N>`). Never post to a PR the
   review file does not name. Draft filenames are repo-qualified —
   `<repo-lowercase>_pr<N>_review.md` — because PR numbers collide
   across repos and a bare `pr7_review.md` is ambiguous.
2. **Staleness gate.** The draft's "Reviewed at head `<sha>`" must equal
   the live head (`gh api repos/<owner>/<repo>/pulls/<N> --jq .head.sha`).
   If the head moved while reviewing, re-verify against the new head and
   rewrite the draft — do not post a review of code that is gone.
3. **Decision mapping.** 🔴 → `--request-changes`, 🟡 → `--comment`,
   🟢 → `--approve`. Ambiguous or missing Decision line → stop and ask.
   Never post an event stronger than the Decision line states.
4. **Duplicate gate.** Search existing reviews and comments for the
   draft's header line and first finding headline. A hit means the
   content is already on the PR — post only the delta, or nothing.

Then:

```
gh pr review <N> --repo <owner>/<repo> --<event> --body-file <file>
```

The body is posted **verbatim**. If it needs changing, edit the file and
re-post; never rewrite the text at post time.

Report the posted review URL, the event used, and any gate that had to
be overridden.

`/post-pr-review` still exists for posting a draft that was staged in an
earlier session, or re-posting after edits. It is no longer a required
second step.

### When NOT to post

Posting is a write to a shared, externally-visible record. It is
suppressed — draft only, report the path — whenever:

- Auto-post is not enabled (see above). This is the default.
- The invoking instruction says GitHub access is read-only, or says not
  to post. An explicit instruction always wins over this skill, in both
  directions.
- The review is of something with no PR to post to — a release tag, a
  branch, a local diff.
- The PR is not the user's to review under the current credentials, or
  `gh` is unauthenticated.

In those cases stage the draft, say where it is, and stop.

---

## Severity Levels

Pair each severity tag with a colored circle so findings scan at a glance
(GitHub renders these natively):

- 🔴 **S0 Blocker**: incorrect behavior, data corruption, crash, invalid configuration, or missing required cross-repo change.
- 🟠 **S1 Major**: high-likelihood bug/regression or incomplete validation for risky change.
- 🟡 **S2 Minor**: maintainability/readability issue with low immediate risk.
- ⚪ **S3 Nit**: style/format/comment-only suggestion.
- 🟢 marks verified-correct material and the approve decision — use it on
  "checked, no action needed" items so green sections read as cleared,
  not skipped.

Lead every finding title with its circle + tag (`🟠 [S1] ...`) and the
Decision line with 🔴 (request changes), 🟡 (comment), or 🟢 (approve).

Only raise severity when evidence supports it.

---

## What to Ask the Author (if missing)

Use these concise prompts:

- "Best practice reminder: could you keep this PR focused on a single topic, or split unrelated changes into follow-up PRs?"
- "Best practice reminder: please add a meaningful PR description including intent, scope, and validation evidence."

- "What exact user-visible behavior should change?"
- "Which files/repos are intentionally not touched in this PR?"
- "What commands did you run to validate and what were outcomes?"
- "Any expected downstream config/data-product impacts?"
- "What rollback path exists if this regresses in production?"

---

## Evidence Rules

- Prefer concrete evidence (code locations, command output, failing scenario) over speculation.
- Distinguish **observed issue** vs **potential risk**.
- If uncertain, label assumptions explicitly.
- Avoid requiring unrelated cleanup for approval.

---

## Output Template

```markdown
### PR Review Summary

**Decision**
- <🟢 approve | 🔴 request changes | 🟡 comment only>

**Scope understood**
- <1-3 bullets>

**Findings**
1. <🔴|🟠|🟡|⚪> [S0|S1|S2|S3] <title>
   - Evidence: <file/behavior/command>
   - Impact: <why it matters>
   - Suggested fix: <concrete change>

2. <circle> [Sx] ...

**Validation check**
- Build/tests run: <yes/no + commands>
- Config contract check: <pass/fail/partial>
- Cross-repo consistency: <pass/fail/needs follow-up>

**Residual risk**
- <short bullets>

**Author follow-ups**
- <numbered actionable requests>
```

---

## Fast Starter Prompt for Copilot Review

```markdown
Review this PR using the `reviewing-pull-requests` skill.

Intent:
<what is changing and why>

Scope:
<files changed + out-of-scope>

Risk areas:
<physics/config/perf/etc>

Validation run:
<commands + outputs>

Cross-repo links:
<related PRs/branches>

Acceptance criteria:
<must-pass conditions>

Return findings with severity (S0-S3), evidence, and suggested fixes.
```

---

## Notes for Mu2e FHiCL-Heavy PRs

For PRs touching `.fcl` composition, include checks that:

- top-level `Production` config intent is preserved,
- `Offline/*/fcl/prolog.fcl` defaults are overridden intentionally,
- dotted epilog overrides resolve as expected,
- include resolution via `FHICL_FILE_PATH` is valid,
- `fhicl-dump -a` provenance confirms final values.

---

## Re-Reviews and Carry-Forward

When a review of this PR already exists, load it BEFORE reviewing and
account for every prior finding in the new review. Prior findings never
silently vanish.

Sources, in order of authority:

1. **Reviews and comments posted on the PR itself** — the canonical
   record. This keeps the loop GitHub-to-GitHub: it works for any
   reviewer on any machine, and it covers other reviewers' change
   requests, not just your own.
2. **A locally staged draft** — consulted only for a review drafted but
   not yet posted. The draft location is a personal scratch convention,
   not part of the shared workflow: default `~/pr_reviews/pr<N>_review.md`,
   overridable with `$PR_REVIEW_DIR`. Once a review is posted, the PR
   carries the record and the local draft is disposable.

Classify each prior finding against the new head:

- **FIXED** — verify the fix at the same evidence bar as a new finding
  (do not trust the commit message; an author's "fixed" commit can
  implement half the prescription — see From Review to Fix). Then move
  it to the verified section as `🟢 [was Sx] ... — FIXED in <sha>,
  verified`.
- **UNADDRESSED** — carry forward at the original severity, marked
  "carried over". Unresolved 🔴/🟠 lead the findings list of the new
  review.
- **PARTIAL** — state exactly which part remains open; keep the
  original severity unless the remaining part is genuinely lower.
- **WITHDRAWN** — only with new evidence; state explicitly what changed
  the assessment.

Scope of the re-review pass: diff `<previously-reviewed-head>..<new
head>` — but re-verify any prior finding whose evidence the delta
touches, and any prior finding in files the delta did NOT touch stays
open by definition. Include other reviewers' requested changes in the
accounting (addressed or not), not just your own.

---

## From Review to Fix

When authoring a patch that implements a finding from one of your own
reviews (yours or a colleague's):

1. **Re-read the finding's full prescription before writing the patch.**
   Implement all of it. A review that says "replace X with Y" is not
   implemented by "add Y" — the removal of X was part of the finding.
2. **State deliberate omissions in the PR body.** If you intentionally
   narrow the fix (hotfix urgency, risk control), write "deliberately
   not touching X because ..." so the reviewer sees a decision, not an
   oversight. Silent deltas between the review and the patch cost a
   review round-trip at best.
3. **"Established idiom" is not a keep-reason.** Discovering that a
   questionable line follows a repo-wide idiom explains its origin, not
   its necessity. Check whether sibling packages pair the idiom with the
   thing your fix adds; if none do, the idiom line is redundant in your
   patch and should go (case study: `configure_file(${CURRENT_BINARY_DIR})`
   staging removed alongside `install(DIRECTORY data ...)` in Offline
   PR #1914 — the reviewer had to request what the original review
   already prescribed).
4. **Minimal diff means no unrelated changes** — it does not mean
   dropping in-scope parts of the prescription that touch adjacent
   lines.
