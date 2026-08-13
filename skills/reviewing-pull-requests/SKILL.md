---
name: reviewing-pull-requests
description: Perform effective code review for Mu2e pull requests. Use when reviewing PRs, assessing risk, checking cross-repo impacts, validating tests/builds, and producing actionable reviewer feedback with severity and evidence.
compatibility: Requires git access, Mu2e offline context, and ability to run targeted checks when needed
metadata:
  version: "2.0.0"
  last-updated: "2026-08-13"
---

# Reviewing Pull Requests

## Scope

Review proportionally to risk — deep where it matters, light where it is
safe. These conventions are intended for Mu2e **offline** repositories only.

Apply this skill's conventions to:

- `Offline`
- `Production`
- `mu2e-trig-config`
- `EventNtuple`
- `EventDisplay`
- `DQM`
- `Tutorial`
- `PassN`
- `RefAna`
- `ArtAnalysis`

For other repositories, treat these conventions as out of scope unless the PR explicitly asks to apply them. Other repos may be online, personal, or minor projects with different standards.

---

## Context Packet

Assemble this yourself from the PR before judging anything — do not stall
waiting for a human to supply it, which would break a scheduled sweep.

1. **Intent**: what behavior is changing and why — PR body and commits.
2. **Scope**: files/modules affected; what is explicitly out-of-scope.
3. **Risk areas**: physics behavior, data products, config compatibility,
   performance, memory, threading.
4. **Validation evidence**: CI status at the current head, plus any commands
   the author reports running.
5. **Environment**: branch, platform, build mode (prof/debug).
6. **Cross-repo links**: companion PRs named in the body — see the
   companion-PR rule under Evidence Rules.

Ask the author only for what the PR genuinely cannot supply — validation
evidence for a physics change, acceptance criteria, a rollback path:

- "What exact user-visible behavior should change?"
- "Which files/repos are intentionally not touched in this PR?"
- "What commands did you run to validate and what were the outcomes?"
- "Any expected downstream config/data-product impacts?"
- "What rollback path exists if this regresses in production?"
- "Best practice reminder: could you keep this PR focused on a single topic,
  or split unrelated changes into follow-up PRs?"
- "Best practice reminder: please add a meaningful PR description including
  intent, scope, and validation evidence."

**Reading the description.** Where descriptions are written carefully, their
length tracks blast radius rather than diff size — a long one is itself a
risk signal. Take an author's own hedge ("this part should be reviewed
carefully, I am not sure ...") as the instruction it is: that is where to
spend the effort.

---

## Review Workflow

0. **Get the diff right, and load prior reviews.**
   `gh pr diff <N> --repo Mu2e/<repo>` (three-dot / merge-base semantics),
   plus `gh pr view <N> --json title,body,headRefOid,reviews,comments`.
   **Never `git diff main..head`** — a two-dot diff includes commits that
   landed on `main` since the branch point and manufactures findings about
   code the author never touched. If prior reviews exist, follow
   "Re-Reviews and Carry-Forward" before continuing.
1. **Restate intent**, and check PR hygiene — single topic, meaningful
   description; use the reminders in Context Packet if either is missing.
2. **Scan changed files** for high-risk categories (interfaces, config, data
   products, paths), then read the conventions matching the subsystem.
3. **Check contracts** (code ↔ FHiCL ↔ job config) and cross-repo impact.
4. **Verify evidence.** If no CI result exists at the current head, trigger
   one per "Triggering a CI Build".
5. **Record findings** with severity and evidence, and summarize residual
   risk — per Severity Levels, Evidence Rules and the Output Template above.
6. **Publish** per "Publishing the Review": posted where auto-post is
   enabled, staged and reported where it is not.

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

**A 🟢 is a claim, held to the same evidence bar as a finding.** Writing
"matches the sibling convention exactly" means you diffed it line by
line, not that you eyeballed the shape and it rhymed. A wrong 🟢 is worse
than a missing one: it tells the next reviewer the item is cleared, so
nobody looks again. When you cannot fully verify, say "not checked"
rather than promoting a skim to green.

The shape this takes: a review 🟢s a new CSV writer as matching its sibling
"exactly, including the trailing comma". The comma does match — but the
sibling sets `setprecision(4)` for translations and `(6)` for rotations,
while the new code uses `(3)` for both, silently truncating rotations to
milliradian. The check stopped at the detail that had been questioned and
never read the surrounding lines.

Lead every finding title with its circle + tag (`🟠 [S1] ...`) and the
Decision line with 🔴 (request changes), 🟡 (comment), or 🟢 (approve).

**The Decision follows the worst open finding.** Any 🔴 → request changes.
🟠 with no 🔴 → request changes if it would produce wrong physics or a
failed job at merge, otherwise comment with the S1 stated first. 🟡/⚪ only
→ approve, since §6 and Recommendations findings never gate. 🟠 is a
finding severity, never a Decision value — leaving it unmapped is what
trips the fail-closed decision gate.

Only raise severity when evidence supports it.

---

## Evidence Rules

- Prefer concrete evidence (code locations, command output, failing scenario) over speculation.
- Distinguish **observed issue** vs **potential risk**.
- If uncertain, label assumptions explicitly.
- Avoid requiring unrelated cleanup for approval.
- **Name what you did not check.** An approval that states its own coverage
  — "I can't verify the content, but the format and counts are valid" — is
  worth more than a blanket LGTM, and a reviewer who never says "not
  checked" is not believable. Same principle as the 🟢-is-a-claim rule.
- **Read the companion PR before calling a cross-repo change missing.**
  Before recording "missing required cross-repo change" as 🔴, read the PR
  body for a named companion and list the author's other open PRs
  (`gh search prs --owner Mu2e --author <login> --state open`). A change
  that ships in a paired PR is a merge-order note, not a blocker — this
  skill has already produced a wrong 🔴 that way.
- **Padding a review with weak findings is legible, and costs credibility.**
  Mu2e reviewers answer automated review comments point by point and grade
  them on merit. Length is not thoroughness. Watch especially for a
  verification pass that restates the finding it was asked to check instead
  of testing it — agreement that adds no evidence is not confirmation.

---

## Output Template

The first two lines are what publish gates 1 and 2 check — a draft without
them cannot be posted.

```markdown
### PR Review Summary — Mu2e/<repo>#<N>

Reviewed at head `<sha>`. <first pass | re-review of <prev-sha>>

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

For PRs touching `.fcl` composition, include checks that:

- top-level `Production` config intent is preserved,
- `Offline/*/fcl/prolog.fcl` defaults are overridden intentionally,
- dotted epilog overrides resolve as expected,
- include resolution via `FHICL_FILE_PATH` is valid,
- `fhicl-dump -a` provenance confirms final values.

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

- **Work whose product nothing consumes**: per-event or per-subrun
  computation feeding nothing downstream. (Dead state itself is under
  Rules.)
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

Typical shapes: a write-only `std::set` rebuilt every subrun after its
only reader was removed; a fragile positional cluster↔MC pairing replaced
by an explicit `diskID()` lookup — simpler *and* it removes the failure
mode.

---

## Subsystem Conventions

Conventions this codebase holds to beyond the coding standard, grouped by
the area they apply to. Read the group matching the PR's subsystem; the
house rules apply repo-wide. **Walk a list to decide what to look at, not
what to write** — an item becomes a finding only when you can quote the
offending line and state the consequence.

### Copied boilerplate: diff against the majority, not the donor

New conditions caches, DB tables, makers and modules are almost always
copy-pasted from an existing sibling. That makes the donor file the
reviewer's blind spot: comparing the PR to the one file it was copied
from confirms only that the copy succeeded.

- **Identify the donor, then survey 4-6 siblings across subsystems.**
  Where the PR agrees with the donor but disagrees with the majority,
  the donor is probably the outlier — and the PR just inherited its bug.
- **Wrong include guards are the tell.** A guard naming another package
  (`TrackerConditions_...` in `CaloConditions/`) proves the file was
  copied and localises the donor for free. Treat it as a prompt to
  survey, not just an S2 to report.
- Boilerplate that "looks like every other cache" is the least-read and
  least-tested code in the PR. Read the call-order contract in the base
  class rather than assuming the pattern is self-evidently right.

The shape this takes: a conditions cache whose `makeIov` reads
`handle->iov()` with no preceding `get(eid)`. `DbHandle::iov()` is a plain
accessor on the live table, which only `get()`/`getPtr()` refresh. Four
sibling caches call `get(eid)` first; only the donor — identified by its
copy-pasted guard — omits it. A review that compares against the donor
finds agreement and misses it.

### Database, conditions, build and release

Applies to `DbTables`, `DbService`, Proditions, `dbTool` and the
build/release system. Walk this list before writing findings.

Ordered by how often each actually comes up, because the weighting is the
point: the first items are routine, the last are rare. A rare one is still
worth fixing, but must not headline a review.

1. **Naming — a generic name is a defect.** A name encodes what the value
   *means and its constraints*, not its type: `index` becomes
   `sequentialStrawIndex` if it must be dense, ordered and unique.
   Variables start lowercase; capitals mean class names. Schema names are
   effectively permanent — renaming a table is merely inconvenient now and
   near-impossible once data exists, so get it right in the first commit.
2. **Dead code, leftovers, duplicates** — see Rules. Flag them even when
   they are harmless.
3. **Never construct an expensive handle inside a loop** — hoist
   `ProditionsHandle`/`DbHandle` to class members.
4. **Production error handling: no `assert`, no bare `try`/`catch`, never
   `print`.** `cet::exception` with a category unique to that call site,
   reported through message facility; algorithm failures return a code.
5. **Logging severity is a contract with the user.** Info and warning differ
   by one letter on screen, so teaching users to skip twenty info lines
   teaches them to skip the warning too. Message-facility categories must be
   globally unique, not derived from the source file name.
6. **const-correctness and interface hygiene** — pass the specific conditions
   object rather than `art::Event`; return a `const&` and let the caller
   decide whether to copy.
7. **Modern-C++ idiom nudges, always hedged** — C++ casts over C casts,
   `emplace_back`, `unique_ptr` over raw `new`, no subclassing STL containers.
8. **Don't derive behaviour from incidental context** — deconstructing a file
   name in code to recover run conditions. Prefer self-describing data, or an
   explicit fcl parameter determined in the shell.
9. **Build files must reflect real dependencies.** An untested trim of a link
   list is not acceptable.
10. **Intentional precision in output** — `std::setprecision` chosen per
    quantity and commented, never copied across quantities of different
    scale. That copy is exactly the mistake in the 🟢-is-a-claim example
    under Severity Levels.
11. **Don't commit table data to the repo** — fcl can silently override it, so
    it can never be trusted in production.

**Non-negotiable in this layer:**

- **A uniform Proditions interface.** Every entity structured and interfaced
  identically (`fromDb`/`fromFcl`). The *contents* belong to the subsystem
  group; only the shape is fixed, because divergence is what makes
  maintenance expensive.
- **DB `Row`/`DbTable` classes never leak into user code.** Wrap them with
  accessors; `CRVOrdinal` and `CaloDAQMap` are the precedents.
- **Never cache a quantity derived from a DB quantity** — the time dependence
  is too easy to lose.
- **Conditions accessed in event methods only**, never `beginRun`/`beginSubrun`.
  This is on the CodingStandards wiki — cite the standard.
- **`get()` before `iov()`/`cid()`** in a conditions cache — see the
  copied-boilerplate note above.
- **Index columns dense, ordered, unique, and validated at load.**
  `TstCalib1.hh` is the reference implementation.
- **Class name mirrors the SQL name** (`CalSomething` ↔ `cal.something`,
  lowercase in Postgres) so a reader moves between them without a lookup.
  One class → one table, committed complete in one commit.
- **Minimise the number of table reads, not table size** — the cost is the
  network round-trip, so compute an inverse map rather than storing one.
- **Proditions only for genuine run dependence.** A constant does not belong
  in the database.

Unsettled, so raise it as a question and never as a defect: what `fromFcl`
should do — zeros and fcl values, or a text path that exercises the DB code.

### Simulation, geometry and job config

Applies to `Mu2eG4`, `EventMixing`, `EventGenerator`, `Sources`,
`JobConfig`, `Compression`, ExtMon, and the production/file-name tooling.
Walk this list before writing findings; each item is a defect here.

1. **No silent degradation.** Detecting a bad input and continuing is a
   defect, not robustness. A warning is not error handling — nobody reads
   the warnings from a million grid jobs, but failures get investigated.
   Flag: returning a default on malformed input, "returns null when
   undefined" as a convention, skipping bad records inside the class that
   should have rejected them. A constructor must either throw or leave a
   self-consistent object.
2. **Unused anything** — see Rules. Read it widely here: typedefs used only
   by their own dictionary, link dependencies nothing needs.
3. **Unsourced numbers.** Any constant, spectrum table or material
   composition needs its origin in a comment — paper, datasheet, chemical
   formula, or the assumptions behind a mixture. A new data file with no
   citation is a finding.
4. **FHiCL that permits an invalid configuration.** Invalid combinations
   must fail validation, not documentation:
   - a `bool` flag plus loose atoms it governs → `OptionalTable<Config>`,
     so the block is either absent or complete;
   - mutually exclusive parameters → `use_if`;
   - a sentinel value meaning "unset" → `OptionalAtom`;
   - a plausible-but-wrong default in a committed job config → `@nil`.
5. **Redundant state.** Two representations of one fact can disagree:
   numerator + denominator + efficiency; three typed members plus a
   `datatype` field. Store one, derive the rest.
6. **Mixed-topic PR.** Unrelated changes in one PR are a blocking objection
   here regardless of whether each change is individually correct.
   Whitespace-only edits belong in their own PR.
7. **fcl epilogs.** A forgotten prolog fails the job; a forgotten epilog
   silently does not. Defaults belong in prologs that a job config can
   override.
8. **Irreproducibility.** Anything that makes a re-run of an old job give a
   different answer — most often an interface referencing "now".
9. **A shared class growing to serve one more caller.** Detector-specific
   fields added to `StepPointMC`, physics cases added to `BinnedSpectrum`.
   The new consumer defines its own type.
10. **One configurable module where several small ones belong.** A config
    field selecting a code path should usually be art's tool/plugin
    dispatch instead. Instance names only when one module produces more
    than one collection of the same type.
11. **Naming, const-correctness and copies, setters, uninitialized members,
    C++ defaults on physics parameters** — the general rules below, enforced
    consistently here.

### House rules, anywhere in Offline

Not subsystem-scoped. Most restate one principle — **one authoritative
place for every fact** — so when a finding does not obviously fit an item
below, ask how many places would need editing to change this one thing.

1. **A numeric literal, and where the number belongs.** `constexpr` with a
   real name is the floor, not the answer. The answer is one of three homes:
   the geometry service if it is a detector dimension, Proditions/DB if it
   can change with time, or a `constexpr` in `DataProducts/inc` if it is
   fixed for all time. Ask what else needs to know this number — the same
   literal appearing in C++, fcl and python is the finding, not the literal.
2. **Dead code** — see Rules. The test for a dead member: no accessor and no
   constructor argument.
3. **Copies nobody asked for.** Anything larger than a pointer passes by
   `const&`; pass the lowest-level object that does the job (`Straw const&`,
   not `Tracker const&`); dereference a `shared_ptr` once at the top of
   `produce` and pass `T const&` down the call chain rather than the
   pointer; an accessor returning an `int` returns by value, not `const&`.
4. **Members initialized where they are declared.** `double x = 0.;` in the
   header beats the initializer list, which beats leaving it uninitialized.
   The reason is maintenance, not safety: with several constructors it is
   one place, and nothing gets missed when a member is added.
5. **Symbolic names for anything with meaning.** `PDGCode::e_minus`, not
   `11`; ROOT's `kFullCrossX`, not `47`. Flow control on a string comparison
   or on N parallel `bool`s should be one `enum` — `EnumToStringSparse` when
   it also needs to print.
6. **Things living in too large a scope.** A data member that could be a
   function local; a static member function that could be free; a class with
   no state that should have been a function. A function needing no member
   data belongs in an anonymous namespace in the `.cc`.
7. **fcl written by copy instead of by delta.** Job configs start from
   `@local::Services.Sim` (or `.Core`/`.SimAndReco`) and override; repeated
   near-identical blocks become one base table plus `@table::Base` variants.
   For the `@nil`/`OptionalAtom` half of this, see item 4 of the
   simulation/geometry list.
8. **A module touched but left on unvalidated FHiCL.** Converting is the
   standing ask whenever you are in the file anyway.
9. **Data-product paperwork.** An entry in `classes_def.xml` *and*
   `art::Wrapper<T>` if it ever goes into the event singly; a default
   constructor, which ROOT requires; `operator<<` implemented in the `.cc`;
   an addition to `Print/`. Enums that reach a file are append-only —
   inserting a value silently changes the meaning of data already written.
10. **Histograms inside a producer.** They belong in a separate analyzer
    reading the data products, unless they monitor transient state that
    never reaches a product — and then behind a fcl switch that can disable
    creating and filling them.
11. **Fabricated values from bad input.** An invalid handle that yields a
    default, which then feeds a histogram or a sum, produces wrong physics
    with no error. Throw, or skip the event explicitly. Trigger code cannot
    throw, so it logs — that is the only exception.
12. **A PR doing two things.** Split it; that alone is grounds for rejection.

## Rules

Source of truth: `https://mu2ewiki.fnal.gov/wiki/CodingStandards`.
If this skill conflicts with that page, follow the wiki.

Reviewers should enforce these rules. Everything below is on the wiki page
unless marked *(not on the wiki)*.

**Files, headers, naming**

- Use Mu2e file extensions: `.hh` and `.cc`. Strongly avoid `.h`, `.cpp`,
  and other variants. Do not use `.icc` unless an external product forces it.
- Include only headers actually needed; avoid speculative includes. (May be
  relaxed for in-development code with planned work.)
- Do not use `using` directives/declarations in headers or before any
  `#include`; fully qualify types in headers (`std::vector<std::string>`).
  In `.cc` files, write only the ones actually used, after the last `#include`.
- Require header guards on all headers.
- Avoid macros except approved uses (header guards, architecture selection,
  `DEFINE_ART_*`, message-facility macros, debug enabling).
- Keep Mu2e classes in the `mu2e` namespace; sub-namespaces need software-team
  coordination.
- **Never use identifiers of the form single/double underscore followed by a
  capital letter — reserved to the compiler.**
- **Do not choose identifiers easily confused with an underlying product**
  — in particular do not start class names with `G4`.
- **Hard tabs and trailing whitespace — never raise either as a finding of
  your own.** The `mu2e/buildtest` job runs the whitespace check
  (`$MUSE_ENVSET_DIR/pre-commit` against the merge base) and reports the
  result inside FNALbuild's PR comment. There is no separate whitespace
  status context to read, and `mu2e/codechecks` (clang-tidy) runs only when
  someone explicitly triggers it — `DEFAULT_TESTS = ["build"]`. So report
  whitespace as CI status quoting FNALbuild's comment, never as your own
  finding. No rule on indent width; consistency is valued and 2 is preferred.
- Require changes to build under the Mu2e baseline language standard,
  currently `-std=c++20` *(not on the wiki — sourced from the build config;
  verify before citing it as a standards violation)*.

**Framework and data products**

- No inter-module communication outside `art::Event` (except EDFilter
  true/false behavior). Not via services, singletons, or static members.
- **Only get data products from the event when you actually need them** —
  unnecessary gets are CPU-expensive.
- Do not cache `art::Handle`/`art::ValidHandle`/`GeomHandle`/`ConditionsHandle`.
  **You may cache `ServiceHandle` and `DbHandle`** — do not flag those.
- **`ProditionsHandle` is typically a class member, but `get()`/`getPtr()`
  must not be called in `beginRun`/`beginSubrun` — event methods only.**
  (A wiki rule, not merely a reviewer preference: accessing conditions in
  `beginRun` risks caching a result that has subrun dependence, and in
  `beginSubrun` risks unnecessary loads on skimmed files. The run/subrun
  accessors were removed outright to enforce it, so on current code this is
  a compile-time matter — but flag any workaround. See the database and
  conditions list under Subsystem Conventions.)
- Data-product classes must: work with persistency; keep the in-memory
  representation orthogonal to the persistent one and not be `TObject`s; be a
  POD or collection of PODs (may hold `art::Ptr<T>`/`std::vector<art::Ptr<T>>`);
  contain no other pointers unless rebuildable on the fly; have **no public
  data**; not let collection elements hold a pointer to their collection;
  strongly avoid `#include`ing non-data-product Mu2e headers; and move
  complicated functions out of the class.
- `RecoDataProducts` must contain no MC information, directly or indirectly.
- Follow the standard pattern for accessing data products.

**Construction, pointers, lifetime**

- **All constructors must leave the object in a safe, usable state.** Avoid
  two-phase construction — prefer `T t(1.,2.,3.);` over default-construct
  plus setters. Few data products should have setters at all.
- **Initialize built-in variables at declaration** (`double x(7.);`, not
  `double x;` … `x = 7.;`) so a later edit cannot read it uninitialized.
- Use the right kind of pointer; a reference is a kind of pointer. **No bare
  pointers in a public interface**, and avoid them internally (legitimate in
  private time-critical code or generic I/O buffer parsing).
- **Do not use "returns null when undefined" as a return convention** — the
  callers that skip the check will core-dump once it can actually return null.
- Strongly avoid any construct requiring a `delete` unless an external package
  imposes it; automate with a safe pointer.
- Prefer `const` and `override` where applicable. Prefer the rule of three;
  use `=default` where appropriate.
- Do not use exception specifications.

**Errors and output**

- All throws must use `cet::exception` with a meaningful category and message.
- **Throw only for fatal errors** — missing configuration or resource,
  detected memory corruption.
- **Algorithm failures must not throw** — prefer returning a failure code to
  the caller.
- Avoid C++ `assert`; when used, debugging only. Runtime errors throw.
- Avoid `printf` and friends in favor of `cout`/`cerr`.
- All prints in modules and production code must be protected by a verbose
  flag or go through message-facility (`mf`) classes.

**Build**

- All libraries must specify all first-order dependencies at build time.
- Linkage loops are forbidden — including ones that "work by accident"
  because a third library happens to load first.
- Code must compile cleanly at the warning levels set by Mu2e software
  management, modulo a short list of external-product exceptions.

**Dead code — the single home for a rule enforced repo-wide**

- **Delete it rather than keep it.** The full surface: an `#include` whose
  only user is gone; an fcl parameter nothing reads; a link-list entry
  nothing needs; a typedef used only by its own dictionary; a member with no
  accessor and no constructor argument, or written but never read; a
  duplicated line; a commented-out block; a generated file that is not
  user-maintained. "Keeping it for reference" is not a reason — git has it.
  The one exception is code under active development, which the author
  should say is under development.
- **Do not use comments or lexical variations to mark your changes or record
  code history** — that is the code-management system's job. This covers
  non-standard indentation, embedded initials, commented-out obsolete code,
  and history comments.
- **Physics-affecting fcl parameters carry no C++ default.** Only debug knobs
  (`verbosityLevel`, `diagLevel`) may default in code. *(Not on the wiki —
  long-standing Mu2e practice; raise it as convention, not as a standards
  citation.)*

---

## Recommendations

Source of truth: `https://mu2ewiki.fnal.gov/wiki/CodingStandards`.
If this skill conflicts with that page, follow the wiki.

These are ⚪ [S3] and **never gate approval**. Raise one only when the PR
already touches that line, and collapse several into a single finding rather
than listing them separately — see the padding rule under Evidence Rules.

- Comment only what the code cannot say. Do not put long comments in the
  middle of code — move them to the top of the file, another file in git,
  DocDB, or the Mu2e website (never a personal web page).
- Match local conventions when touching existing files, unless correcting
  major violations.
- **Capitalization**: types (classes, structs, typedefs) start with a capital;
  every other identifier starts lowercase or `_`; delimit words with
  `bouncingCapitals`, not `embedded_underscores`. Pick one member-data
  convention and hold it across related classes — current recommendation is a
  single trailing underscore. Exception: classes with deliberately `std::`-like
  behavior follow `std::` conventions.
- Avoid Hungarian notation, except where the same information must be
  addressable two ways (the data-product access pattern).
- **Numeric constants**: `<cmath>` for π; otherwise
  `CLHEP/Units/PhysicalConstants.h` (e.g. `CLHEP::twopi`). Prefer not to take
  these from ROOT. *(The wiki writes this as `std::M_PI`; `M_PI` is not in
  namespace `std` — do not propagate that spelling into a finding.)*
- Use CLHEP units/constants with explicit qualification (`CLHEP::mm`),
  especially short names like `CLHEP::m` that collide with variable names.
- **Static data members**: very few good reasons — consult the software team
  before adding one.
- Prefer private data with accessors for event-data and broadly exposed
  classes, even when the class is "really just a struct".
- **Class layout**: public first, private last; trivial accessors inline in the
  declaration; long functions in the `.cc`; provide `=default`/`=delete` for
  all compiler-writable functions.
- Prefer one statement per line.
- Prefer pre-increment (`++i`) over post-increment when equivalent.
- Avoid `operator<` for types with several meaningful sort orders; provide
  named free comparator functions instead.
- Avoid `std::pair` where a named struct improves readability
  (`x.position_` beats `x.first`).
- Prefer ordered includes: this file's own interface header, other headers from
  the same project, non-standard third-party libs, near-standard libs (Boost),
  C++ stdlib, C headers.
- Commit to the repository as often as is reasonable.
- Prefer straightforward, "vanilla" C++ over clever or compact patterns, and
  favor readability over micro-optimization absent performance data
  *(not on the wiki — local convention for a part-time-maintainer codebase)*.
- Validate FHiCL consistency with `fhicl-dump -a` when config behavior changes
  *(not on the wiki — local practice)*.

### Threaded code

Mu2e has no settled threading policy, so a threading claim in a PR is a
question to ask, not a rule to enforce. When asking, use the wiki's four
terms precisely rather than "thread safe" loosely: **thread hostile**
(mutable global state — much of ROOT), **thread friendly** (separate
instances per thread are safe — ROOT I/O), **const thread safe** (safe if
all threads call only `const` methods), **thread safe** (mutable and
immutable methods concurrently).

---

## Local Conventions

Capture and enforce project-local patterns here, even when they are not universal C++ style rules.

### Include Guard Naming

**In `Offline`**: path words plus file base name, `_hh` suffix, no repo
prefix — `Offline/GeneralUtilities/inc/FooBar.hh` → `GeneralUtilities_FooBar_hh`.

**Elsewhere, match the repo you are in, not this rule.** `Production` and
`mu2e-trig-config` contain no headers at all, and `EventNtuple` — the only
other in-scope repo with any — uses a bare `TrkInfo_HH`. Read a sibling
header before calling a guard wrong.

Default severity 🟡 [S2]; escalate only if a real collision or
multiple-include bug is observed. Per the copied-boilerplate note under
Subsystem Conventions, a guard naming *another package*
is worth more as a signal to survey siblings for inherited copy-paste bugs
than as a finding on its own.

---

## Publishing the Review

A review is drafted to a local file under `$PR_REVIEW_DIR` (fallback
`~/pr_reviews/`), named `<repo-lowercase>_pr<N>_review.md` — repo-qualified
because PR numbers collide across repos. Drafting to a file lets the review
be written, re-read and corrected with ordinary tools before anyone sees it.
Whether that draft is then posted automatically depends on opt-in.

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
   review file does not name.
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

To post a draft staged in an earlier session, or to re-post after edits:
`/post-pr-review <N>`.

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

## Triggering a CI Build

A review that turns on "does it build" is worthless without a build at
the head being reviewed. When there isn't one, ask FNALbuild for it:

```
gh pr comment <N> --repo Mu2e/<repo> --body "@FNALbuild run build test"
```

This is the **only** state-changing command besides the review itself
that this skill authorizes. It does not extend to `gh pr edit`,
`gh pr merge`, `gh pr close`, `git push`, or any other comment.

### Where it works

**`Offline` and `Production` only.** FNALbuild watches neither
`mu2e-trig-config` nor any of the other seven skill-scope repos. Posting
the phrase anywhere else is a no-op comment on someone's PR: noise, not a
trigger. Check the repo before using it.

Triggering needs `Mu2e/write` or `Mu2e/fnalbuild-users` membership, which
FNALbuild states in its own opening comment on each PR. If the comment
posts and no `:hourglass:` reply follows within a few minutes, the
account lacks access — say so in the review and stop. Do not re-post.

### When to trigger

- **No result at the current head.** The head moved after the last run,
  so the green (or red) on the PR describes code that is gone. This is
  the common case and the one worth catching.
- **FNALbuild said the result is stale** — "The HEAD of `main` has
  changed ... Tests are now out of date."
- **The last run failed for a reason unrelated to the diff** —
  a broken `main`, a workspace/merge-conflict abort, an infrastructure
  error. Confirm the failure is not caused by the diff first: read the
  failing target, then check whether the same target is red on `main`
  itself (`gh api repos/Mu2e/<repo>/commits/main/statuses`). A red
  inherited from a broken `main` is not a finding against this PR. Decide
  from the failing target, never re-trigger on a hunch.

### When NOT to trigger

- A run is already queued or in progress at the current head. Wait and
  report it as pending; a second request just queues a duplicate.
- The current head is already green. Re-running to "be sure" burns a
  build slot shared by the whole collaboration.
- The failure is real and caused by the diff. Re-running will reproduce
  it. Report the failing target as a finding instead.
- The invoking instruction forbids state-changing commands. A scheduled
  sweep or a read-only instruction that says the review is the only
  permitted write **wins over this section** — in that case do not
  comment; record "needs a CI run at `<sha8>`" in the review and let a
  human trigger it.

**Once per review pass.** Trigger, then either wait for the result if the
review depends on it, or post the review noting CI is pending at that
head and say you triggered it. Never trigger twice in one pass.

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
   not yet posted; location and filename per "Publishing the Review". Once
   a review is posted, the PR carries the record and the draft is
   disposable.

When another reviewer's comments are part of that record, honour any
ranking they state ("X is important, the rest are style") — do not
promote their explicit style items to blockers, and do not bury the one
they called important. Read the review *state*, not the comment volume: a
long design critique submitted as APPROVED is not a blocked PR, and
carrying it forward as one misreports the record.

Reading another reviewer's record:

- **Severity is usually stated in words.** *"the missing get() is important,
  I think the rest are clarity or style"*, *"targets of opportunity"*, *"for
  a future PR"*, *"I won't hold up this pull"*, *"your call"* — all mean
  non-blocking, and the ranking is meant literally in both directions. Do
  not carry those forward as open.
- **A heavy design critique often ships as APPROVED.** What actually blocks
  is a concrete, checkable omission that breaks something at merge — an
  unregistered table, a stray committed file, a leftover build rule — or an
  unresolved architectural question in a layer someone owns. Style, naming
  preference and food for thought do not block.
- **Blocking items phrased as questions are cleared by an answer on the
  thread.** Check the replies before recording one as UNADDRESSED.
- **An objection to a PR's *scope* is often filed as COMMENT while the
  reviewer stays unconvinced.** That shows as no outstanding request and is
  one.
- **APPROVED carrying open inline comments usually means deferred**, not
  unaddressed — especially when the approver is also the merger.


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

Applies only when you are authoring the patch, not reviewing one.

1. **Implement the finding's full prescription.** "Replace X with Y" is not
   implemented by "add Y" — removing X was part of it. Minimal diff means no
   *unrelated* changes, not dropping in-scope parts that touch adjacent lines.
2. **State deliberate omissions in the PR body** — "deliberately not touching
   X because ..." — so the reviewer sees a decision, not an oversight.
3. **"Established idiom" is not a keep-reason.** That a questionable line
   follows a repo-wide idiom explains its origin, not its necessity. Check
   whether siblings pair the idiom with what your fix adds; if none do, it
   goes. A build-time `configure_file` staging copy left in place beside a
   newly added `install(DIRECTORY data ...)` rule is the standard shape of
   this: the reviewer has to re-request what the review already prescribed.
