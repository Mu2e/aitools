#!/usr/bin/env python3
"""Read-only landscape view of open PRs across Mu2e offline repos.

Makes no writes of any kind. Every gh call is a GET.

Usage:
    pr_digest.py [repo ...]      # default: all nine skill-scope repos
    pr_digest.py --json          # machine-readable, no table
    pr_digest.py Offline         # one repo
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

REPOS = ["Offline", "Production", "EventNtuple", "EventDisplay", "DQM",
         "Tutorial", "PassN", "RefAna", "ArtAnalysis"]

# FNALbuild only watches these two; elsewhere "no CI" is normal, not a finding.
FNALBUILD_REPOS = {"Offline", "Production"}

PR_LIMIT = 100  # if a repo ever exceeds this we say so rather than truncate silently

LIST_FIELDS = ("number,title,author,createdAt,updatedAt,headRefOid,"
               "reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,url,isDraft")


def gh(args):
    """Run a gh command, returning (stdout, error_or_None). Never raises."""
    try:
        p = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", f"{type(e).__name__}: {e}"
    if p.returncode != 0:
        return "", (p.stderr or "").strip().splitlines()[-1] if p.stderr else f"exit {p.returncode}"
    return p.stdout, None


def me():
    out, err = gh(["api", "user", "--jq", ".login"])
    if err:
        sys.exit(f"FATAL: cannot determine gh user ({err}). Is gh authenticated?")
    return out.strip()


def ci_summary(rollup, repo):
    """Collapse statusCheckRollup into (state, detail). State in
    green/red/pending/none."""
    if not rollup:
        return ("none", "no checks at head" if repo in FNALBUILD_REPOS else "n/a")
    fail, pend, ok = [], [], 0
    for c in rollup:
        # StatusContext uses state; CheckRun uses conclusion/status.
        st = (c.get("state") or c.get("conclusion") or c.get("status") or "").upper()
        name = c.get("context") or c.get("name") or "?"
        if st in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"):
            fail.append(name)
        elif st in ("PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", ""):
            pend.append(name)
        else:
            ok += 1
    if fail:
        return ("red", ", ".join(sorted(fail)[:3]) + ("…" if len(fail) > 3 else ""))
    if pend:
        return ("pending", ", ".join(sorted(pend)[:3]) + ("…" if len(pend) > 3 else ""))
    return ("green", f"{ok} checks")


def my_review(repo, num, user, head8):
    """Latest review by `user`: (state, sha8, at_head) or None. One GET."""
    out, err = gh(["api", f"repos/Mu2e/{repo}/pulls/{num}/reviews", "--paginate",
                   "--jq", f'.[] | select(.user.login=="{user}") '
                           '| "\\(.state)\\t\\(.commit_id[0:8])\\t\\(.submitted_at)"'])
    if err:
        return ("ERROR", err[:40], False)
    rows = [r.split("\t") for r in out.strip().splitlines() if r.strip()]
    if not rows:
        return None
    rows.sort(key=lambda r: r[2])          # chronological; last is most recent
    state, sha, _ = rows[-1]
    # Any review at the live head counts as covered, even if an older one was later.
    at_head = any(r[1] == head8 for r in rows)
    return (state, sha, at_head)


def collect(repos, user):
    prs, errors = [], []
    for repo in repos:
        out, err = gh(["pr", "list", "--repo", f"Mu2e/{repo}", "--state", "open",
                       "--limit", str(PR_LIMIT), "--json", LIST_FIELDS])
        if err:
            errors.append(f"{repo}: {err}")
            continue
        try:
            items = json.loads(out or "[]")
        except json.JSONDecodeError as e:
            errors.append(f"{repo}: unparseable gh output ({e})")
            continue
        if len(items) >= PR_LIMIT:
            errors.append(f"{repo}: hit the {PR_LIMIT}-PR cap; list may be truncated")
        for it in items:
            if it.get("isDraft"):
                continue
            head8 = (it.get("headRefOid") or "")[:8]
            ci, ci_detail = ci_summary(it.get("statusCheckRollup"), repo)
            mine = my_review(repo, it["number"], user, head8)
            created = datetime.fromisoformat(it["createdAt"].replace("Z", "+00:00"))
            prs.append({
                "repo": repo, "number": it["number"], "title": it["title"],
                "author": (it.get("author") or {}).get("login", "?"),
                "age_days": (datetime.now(timezone.utc) - created).days,
                "head": head8, "url": it["url"],
                "review_decision": it.get("reviewDecision") or "",
                "mergeable": it.get("mergeable") or "",
                "merge_state": it.get("mergeStateStatus") or "",
                "ci": ci, "ci_detail": ci_detail,
                "my_review_state": mine[0] if mine else "",
                "my_review_sha": mine[1] if mine else "",
                "my_review_at_head": bool(mine and mine[2]),
            })
    return prs, errors


def attention(pr, user):
    """Reasons this PR wants a human. Order matters: most actionable first."""
    out = []
    if pr["my_review_state"] and not pr["my_review_at_head"]:
        out.append(f"your review is STALE (reviewed {pr['my_review_sha']}, head {pr['head']})")
    if pr["ci"] == "red":
        out.append(f"CI RED at head — {pr['ci_detail']}")
    if pr["ci"] == "none" and pr["repo"] in FNALBUILD_REPOS:
        out.append(f"no CI at head {pr['head']}")
    if not pr["review_decision"] and not pr["my_review_state"]:
        out.append("never reviewed by anyone")
    if pr["mergeable"] == "CONFLICTING":
        out.append("merge CONFLICT")
    return out


CI_MARK = {"green": "ok", "red": "FAIL", "pending": "run", "none": "--"}


def render(prs, errors, user, repos):
    W = 78
    print(f"Mu2e PR digest — {datetime.now().strftime('%Y-%m-%d %H:%M')} — as {user}")
    print(f"{len(repos)} repo(s), {len(prs)} open non-draft PR(s)")
    print("=" * W)

    flagged = [(p, attention(p, user)) for p in prs]
    flagged = [(p, r) for p, r in flagged if r]
    if flagged:
        print("\nNEEDS ATTENTION")
        for p, reasons in sorted(flagged, key=lambda x: (-len(x[1]), x[0]["repo"])):
            print(f"  {p['repo']}#{p['number']}  {p['title'][:52]}")
            for r in reasons:
                print(f"      - {r}")
            print(f"      {p['url']}")
    else:
        print("\nNEEDS ATTENTION: nothing flagged.")

    print("\nALL OPEN")
    hdr = f"  {'PR':<20} {'author':<16} {'age':>4} {'CI':<5} {'review':<16} {'you':<10} title"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for p in sorted(prs, key=lambda x: (x["repo"], -x["number"])):
        you = ("@head" if p["my_review_at_head"]
               else (p["my_review_sha"] if p["my_review_state"] else "-"))
        dec = (p["review_decision"] or "-").replace("CHANGES_REQUESTED", "CHANGES_REQ")
        print(f"  {p['repo']+'#'+str(p['number']):<20} {p['author'][:16]:<16} "
              f"{str(p['age_days'])+'d':>4} {CI_MARK[p['ci']]:<5} {dec[:16]:<16} "
              f"{you:<10} {p['title'][:28]}")

    if errors:
        print("\nINCOMPLETE — these repos did not report cleanly:")
        for e in errors:
            print(f"  ! {e}")
        print("  Treat counts above as a lower bound, not a complete picture.")


def main():
    argv = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]
    repos = argv or REPOS
    bad = [r for r in repos if r not in REPOS]
    if bad:
        sys.exit(f"FATAL: not a skill-scope repo: {', '.join(bad)}\n"
                 f"Known: {', '.join(REPOS)}")
    user = me()
    prs, errors = collect(repos, user)
    if as_json:
        print(json.dumps({"user": user, "repos": repos, "prs": prs,
                          "errors": errors}, indent=2))
    else:
        render(prs, errors, user, repos)
    # Exit 1 if any repo failed, so a caller can tell a partial run from a clean one.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
