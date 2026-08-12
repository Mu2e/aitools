---
name: pr-digest
description: Landscape view of open pull requests across the Mu2e offline repos — who authored what, review state, CI state at the current head, and which PRs need your attention. Use when asked for a PR summary, PR status, "what's open on Offline", or what needs reviewing. Read-only.
compatibility: Requires an authenticated `gh` CLI and network access to github.com. Makes no writes.
metadata:
  version: "1.0.0"
  last-updated: "2026-08-08"
---

# PR Digest

## Purpose

Answer "what is the state of the PR queue?" — as opposed to
`reviewing-pull-requests`, which answers "is this one PR any good?".

Use it for: a standup-style summary, deciding what to review next,
finding PRs whose head moved after you reviewed them, and spotting CI
that is red or was never run at the current head.

## Read-only

Every call this skill makes is a GET. It never posts a review, never
comments, never triggers a build, never edits or merges anything. That
is the point: a digest is cheap to trust precisely because it cannot
change anything. If a PR in the digest needs action, hand it to
`reviewing-pull-requests` — do not act from here.

## Scope

The nine Mu2e offline repos: `Offline`, `Production`, `EventNtuple`,
`EventDisplay`, `DQM`, `Tutorial`, `PassN`, `RefAna`, `ArtAnalysis`.
Passing any other repo is a hard error, not a silent skip.

Open, non-draft PRs only. Drafts are excluded — they are not asking for
review yet.

## Usage

```
python3 scripts/pr_digest.py               # all nine repos
python3 scripts/pr_digest.py Offline       # one repo
python3 scripts/pr_digest.py Offline Production
python3 scripts/pr_digest.py --json        # machine-readable
```

Roughly 15 s for all nine repos (~20 API calls for ~12 open PRs).
Exit status is 1 if any repo failed to report, 0 on a clean run — so a
caller can distinguish a partial picture from a complete one.

## What it reports

**NEEDS ATTENTION** — only PRs with at least one flag, most-flagged
first. Nothing here means nothing is flagged, which is stated explicitly
rather than left as an empty section.

| flag | meaning | why it matters |
|---|---|---|
| `your review is STALE` | you reviewed at sha X, head is now Y | your findings describe code that no longer exists; the author pushed after you |
| `CI RED at head` | a check reports FAILURE at the current head | names the failing contexts, so you can tell a real break from an infra blip |
| `no CI at head` | Offline/Production PR with no check at this head | the green you see may belong to an older commit — see `reviewing-pull-requests` §Triggering a CI Build |
| `never reviewed by anyone` | no `reviewDecision` and no review by you | the queue's actual backlog |
| `merge CONFLICT` | `mergeable == CONFLICTING` | blocked regardless of review state |

**ALL OPEN** — one row per PR: repo#number, author, age in days, CI
mark, overall review decision, and your own review state.

The `you` column is the one to read: `@head` means your review covers
the current head, a sha8 means your latest review is at that older
commit, `-` means you have not reviewed it.

CI marks: `ok` green, `FAIL` red, `run` pending, `--` no checks.

**INCOMPLETE** — any repo that did not report cleanly is listed by name
with its error, and the totals are labelled a lower bound. A repo that
errors is never silently rendered as zero PRs.

## Interpreting `--` in the CI column

`--` is normal and not a finding on `EventNtuple`, `EventDisplay`,
`DQM`, `Tutorial`, `PassN`, `RefAna` and `ArtAnalysis` — FNALbuild does
not watch those repos, so there is no CI to be missing. The digest only
raises "no CI at head" for `Offline` and `Production`, where the absence
is genuinely informative.

## Implementation notes

Two `gh` quirks drove the call structure. Do not "optimize" them away:

- **`gh pr list --json latestReviews` returns `commit.oid` as an empty
  string.** The whole point of the `you` column is comparing your
  review's commit against the live head, so the digest makes one
  `gh api .../pulls/<N>/reviews` call per PR to get `commit_id`. There is
  no way to get it from the list endpoint.
- **`latestReviews` also carries the full review `body`.** On a PR with
  a long review that is ~15 KB of prose per PR, for a field the digest
  never displays. `latestReviews` is deliberately absent from the
  requested field list.

A PR is treated as covered if *any* of your reviews sits at the current
head, not merely the most recent one — posting a 🟡 comment after an
earlier 🔴 at the same sha should not read as stale.

## What it deliberately does not do

- **No quality judgement.** It reports that CI is red, not why. It
  reports that a PR is unreviewed, not whether it is any good.
- **No unanswered-question detection.** Deciding whether a reviewer's
  inline question was actually answered needs to read the thread and
  judge it — that is review work, and it belongs in
  `reviewing-pull-requests`, not in a digest that must stay cheap.
- **No writes.** See above.
