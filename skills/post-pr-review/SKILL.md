---
name: post-pr-review
description: Publish a locally drafted PR review to GitHub as a formal review or comment. Use after /reviewing-pull-requests has staged a draft and the user asks to post it. Enforces a staleness gate, decision-to-event mapping, and a duplicate check before anything is sent.
compatibility: Requires gh CLI authenticated with review permission on the target repo
metadata:
  version: "1.2.0"
  last-updated: "2026-08-07"
---

# Post PR Review

## Purpose

Publish a review staged by `/reviewing-pull-requests` to the GitHub PR,
fail-closed: every gate must pass before `gh` is invoked. Invoking this
skill IS the user's go-ahead to post — but only if the gates pass.

As of reviewing-pull-requests v1.7.0 that skill can post its own review
at the end of the run, when `PR_REVIEW_AUTOPOST` is set or the request
says to post. That is opt-in, so for most users this skill remains the
normal way a review reaches GitHub. Use it to publish a staged draft, to
post one staged in an earlier session, to re-post after editing, or to
post a review whose automatic publish was suppressed (read-only context,
moved head, duplicate hit).

## Arguments

```
/post-pr-review <PR URL or owner/repo#N> [comment|approve|request-changes] [review-file] [--allow-duplicate] [--force-stale]
```

- Review file defaults to `$PR_REVIEW_DIR/<repo-lowercase>_pr<N>_review.md`,
  where `$PR_REVIEW_DIR` falls back to `~/pr_reviews` if unset. The name is
  repo-qualified because PR numbers collide across repos; a bare
  `pr7_review.md` is ambiguous. For back-compatibility, fall back to
  `pr<N>_review.md` if the qualified name is absent. The directory is a
  personal drafting location, not shared state — see "Why a local draft"
  below.
- The event defaults to the review's own Decision line (see mapping);
  an explicit event argument overrides it.

## Workflow

1. **Locate the review file.** Default
   `$PR_REVIEW_DIR/<repo-lowercase>_pr<N>_review.md`, then the unqualified
   `pr<N>_review.md` (both under `~/pr_reviews/` if `$PR_REVIEW_DIR` is
   unset). Missing file → stop and report (do not synthesize a review here;
   that is `/reviewing-pull-requests`' job).

2. **Staleness gate.** Extract the head SHA from the review's
   "Reviewed at head `<sha>`" line and compare with the live PR head
   (`gh api repos/<owner>/<repo>/pulls/<N> --jq .head.sha`).
   - Mismatch → STOP. Report old vs new head and recommend re-running
     `/reviewing-pull-requests` first. Only `--force-stale` overrides,
     and the posted body must then be edited to say which head it
     reviewed.

3. **Decision mapping.** Read the Decision line:
   - 🔴 / "request changes"  → `gh pr review --request-changes`
   - 🟡 / "comment"          → `gh pr review --comment`
   - 🟢 / "approve"          → `gh pr review --approve`
   Ambiguous or missing Decision line → STOP and ask. An explicit
   event argument overrides the mapping; say so in the report.

4. **Duplicate check.** Fetch existing PR comments and reviews
   (`gh api .../issues/<N>/comments`, `.../pulls/<N>/reviews`) and
   search them for the review's first finding headline and its
   summary-header line.
   - Hit → STOP. Report which comment already carries the content and
     suggest posting only the delta (edit the file first) or
     re-invoking with `--allow-duplicate`.

5. **Post.**
   ```
   gh pr review <N> --repo <owner>/<repo> --<event> --body-file <file>
   ```
   The body is posted verbatim — never rewrite it at post time. If the
   file needs changes, edit and re-stage first, then re-invoke.

6. **Report** the posted review URL, the event used, and which gates
   were overridden (if any).

## Why a local draft at all

Everything durable in this workflow is GitHub-to-GitHub: once posted,
the review on the PR is the canonical record, and re-reviews read prior
findings from the PR itself (see reviewing-pull-requests, "Re-Reviews
and Carry-Forward"). The local file exists only for the drafting stage
— so a human can read and edit the review with ordinary tools before
anything becomes visible on the PR. It is per-user scratch space, never
shared state, and disposable after posting.

## Hard rules

- Never post to a PR the review file does not name.
- Never upgrade the event beyond the review's Decision (a 🟡 review is
  not posted as request-changes without an explicit argument).
- Never post when the staleness or duplicate gate fails, absent the
  matching override flag.
- One post per invocation; no follow-up comments without a new
  invocation.
