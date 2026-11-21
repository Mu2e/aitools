#!/usr/bin/env python3
"""Self-test for agentlog_mcp.canon -- the canonicalization rules.

Run with any Python 3.10+, no database, no MCP client, no installed package:

  ./scripts/test_canon.py

Why this exists at all, when no other MCP in this workspace ships tests: a
canonicalization bug is SILENT. Nothing errors, no row is lost, no alarm
fires -- the refs index just grows 'Run 10542', '10542' and '010542' as three
separate things, and a search for one of them misses the reports filed under
the other two. By the time anyone notices, the log is full of near-duplicates
that cannot be merged without an administrator (the MCP has no UPDATE). So
the rules get asserted, not just reviewed.

The assertions that matter most, in the order they matter:
  * the same thing written differently gives ONE key (dedup works)
  * different things never give the same key (dedup does not over-merge)
  * run and time refs always carry BOTH projection columns, which is what the
    refs_run_cols / refs_time_cols CHECKs require
  * a ref carrying one role twice does not become two links
"""

from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentlog_mcp import canon  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{label}\n     got: {got!r}\n    want: {want!r}")


def check_raises(label: str, fn, *args) -> None:
    try:
        fn(*args)
    except ValueError:
        return
    FAILURES.append(f"{label}: expected ValueError, got none")


def key_of(ref_type: str, raw: str) -> str:
    return canon.canon_ref(ref_type, raw, "subject")["ref_key"]


# ---------------------------------------------------------------------------
# run: the case the whole dedup premise was explained with
# ---------------------------------------------------------------------------

for spelling in ("10542", " 10542 ", "run 10542", "Run 10542", "RUN:10542", "010542",
                 "run #10542", "10_542"):
    check(f"run {spelling!r} canonicalizes to '10542'", key_of("run", spelling), "10542")

for spelling in ("10500-10600", "10500 - 10600", "10500..10600", "10500 to 10600",
                 "runs 10500-10600", "10600-10500"):
    check(f"run range {spelling!r}", key_of("run", spelling), "10500-10600")

row = canon.canon_ref("run", "Run 10542", "subject")
check("single run projects both columns", (row["run_lo"], row["run_hi"]), (10542, 10542))
check("single run has no time columns", (row["time_lo"], row["time_hi"]), (None, None))
check("single run label", row["label"], "run 10542")

row = canon.canon_ref("run", "10500-10600", "subject")
check("run range projects both columns", (row["run_lo"], row["run_hi"]), (10500, 10600))
check("run range label", row["label"], "runs 10500-10600")

check("different runs stay different", key_of("run", "10542") != key_of("run", "10543"), True)
check(
    "a run and a range containing it stay different",
    key_of("run", "10542") != key_of("run", "10542-10543"),
    True,
)
check_raises("non-numeric run rejected", key_of, "run", "ten thousand")
check_raises("empty run rejected", key_of, "run", "   ")
check_raises("absurd run rejected", key_of, "run", "99999999999")

# ---------------------------------------------------------------------------
# time: instants, intervals, offsets, sub-second noise
# ---------------------------------------------------------------------------

check(
    "UTC instant",
    key_of("time", "2026-11-12T03:14:00Z"),
    "2026-11-12T03:14:00Z",
)
check(
    "naive instant is read as UTC",
    key_of("time", "2026-11-12T03:14:00"),
    "2026-11-12T03:14:00Z",
)
check(
    "offset instant folds to the same UTC key",
    key_of("time", "2026-11-11T21:14:00-06:00"),
    "2026-11-12T03:14:00Z",
)
check(
    "microseconds are truncated, so two spellings of one instant agree",
    key_of("time", "2026-11-12T03:14:00.123456Z"),
    key_of("time", "2026-11-12T03:14:00Z"),
)
check(
    "bare date means midnight at the START of the day",
    key_of("time", "2026-11-12"),
    "2026-11-12T00:00:00Z",
)
check(
    "interval",
    key_of("time", "2026-11-11T20:00:00Z/2026-11-12T08:00:00Z"),
    "2026-11-11T20:00:00Z/2026-11-12T08:00:00Z",
)
check(
    "reversed interval is ordered, not rejected",
    key_of("time", "2026-11-12T08:00:00Z/2026-11-11T20:00:00Z"),
    "2026-11-11T20:00:00Z/2026-11-12T08:00:00Z",
)

row = canon.canon_ref("time", "2026-11-11T20:00:00Z/2026-11-12T08:00:00Z", "subject")
check(
    "interval projects both columns",
    (row["time_lo"].astimezone(timezone.utc).isoformat(),
     row["time_hi"].astimezone(timezone.utc).isoformat()),
    ("2026-11-11T20:00:00+00:00", "2026-11-12T08:00:00+00:00"),
)
check("time ref has no run columns", (row["run_lo"], row["run_hi"]), (None, None))
row = canon.canon_ref("time", "2026-11-12T03:14:00Z", "subject")
check("an instant still fills both time columns", row["time_lo"] == row["time_hi"], True)
check("instant time_lo is not None", row["time_lo"] is not None, True)
check_raises("unparseable time rejected", key_of, "time", "last night")
check_raises("three-part interval rejected", key_of, "time", "a/b/c")

# Every ref_type other than run and time must leave all four projection
# columns NULL -- that is the other half of the refs_run_cols/refs_time_cols
# CHECKs, and the reason migration 001 exists.
for ref_type, raw in (
    ("system", "trk"), ("metric", "trk/track/meanChi2"), ("alarm", "x@y"),
    ("dataset", "mu2e:nts.x.root"), ("file", "f.root"), ("document", "docdb:1"),
    ("url", "https://h/p"), ("report", "418"), ("mcp", "dqm.list_alarms"),
    ("free", "anything"),
):
    projected = canon.canon_ref(ref_type, raw, "subject")
    check(
        f"{ref_type} leaves all four projection columns NULL",
        (projected["run_lo"], projected["run_hi"],
         projected["time_lo"], projected["time_hi"]),
        (None, None, None, None),
    )

# ---------------------------------------------------------------------------
# text-keyed types: what gets folded and what deliberately does not
# ---------------------------------------------------------------------------

check("system is lower-cased", key_of("system", "TRK"), "trk")
check("system whitespace collapses", key_of("system", "  trk  "), "trk")
check(
    "metric keeps camelCase (namex is case-sensitive in dqm.values)",
    key_of("metric", "trk/track/meanChi2"),
    "trk/track/meanChi2",
)
check(
    "metric spaces around slashes are removed",
    key_of("metric", "trk / track / meanChi2"),
    "trk/track/meanChi2",
)
check(
    "metric case is NOT folded, so these stay two things",
    key_of("metric", "trk/track/meanChi2") != key_of("metric", "trk/track/meanchi2"),
    True,
)
check(
    "dataset case is preserved",
    key_of("dataset", "mu2e:mcs.mu2e.CeEndpoint.art"),
    "mu2e:mcs.mu2e.CeEndpoint.art",
)
check("docdb spelling 1", key_of("document", "docdb:12345"), "docdb:12345")
check("docdb spelling 2", key_of("document", "DocDB 12345"), "docdb:12345")
check("docdb spelling 3", key_of("document", "DOCDB:012345"), "docdb:12345")
check("ecl document", key_of("document", "ECL 67890"), "ecl:67890")
check("wiki document keeps its id case", key_of("document", "wiki:TrackerHV"), "wiki:TrackerHV")
check("docdb label", canon.canon_ref("document", "docdb:12345", "subject")["label"], "DocDB 12345")
check_raises("document without a source rejected", key_of, "document", "12345")

check(
    "url host is lower-cased, path is not",
    key_of("url", "HTTPS://Example.ORG/Path/To?q=A"),
    "https://example.org/Path/To?q=A",
)
check_raises("relative url rejected", key_of, "url", "/just/a/path")

check("report id spelling 1", key_of("report", "418"), "418")
check("report id spelling 2", key_of("report", "report 418"), "418")
check("report id spelling 3", key_of("report", "0418"), "418")
check_raises("non-numeric report rejected", key_of, "report", "the one about the tracker")

check("free text collapses whitespace", key_of("free", "  two   words "), "two words")
check_raises("over-long key rejected", key_of, "free", "x" * (canon.MAX_REF_KEY_CHARS + 1))

# ---------------------------------------------------------------------------
# parse_ref_lines: the '<type>:<key>' mini-syntax
# ---------------------------------------------------------------------------

entries = canon.parse_ref_lines("run:10542, system:TRK , metric:trk/track/meanChi2",
                                "subject", "subjects")
check("comma-separated count", len(entries), 3)
check(
    "comma-separated keys",
    [(e["ref_type"], e["ref_key"]) for e in entries],
    [("run", "10542"), ("system", "trk"), ("metric", "trk/track/meanChi2")],
)

entries = canon.parse_ref_lines(
    "run:10542\ntime:2026-11-11T20:00:00Z/2026-11-12T08:00:00Z\n\nurl:https://h/a,b\n",
    "subject", "subjects",
)
check("newline-separated count (comma inside a url survives)", len(entries), 3)
check("url with a comma kept whole", entries[2]["ref_key"], "https://h/a,b")
check(
    "only the first colon splits",
    canon.parse_ref_lines("time:2026-11-12T03:14:00Z", "subject", "s")[0]["ref_key"],
    "2026-11-12T03:14:00Z",
)
check("empty input gives no refs", canon.parse_ref_lines("", "subject", "s"), [])
check("whitespace-only input gives no refs", canon.parse_ref_lines("  \n ", "subject", "s"), [])
check_raises("missing colon rejected", canon.parse_ref_lines, "10542", "subject", "subjects")
check_raises("unknown type rejected", canon.parse_ref_lines, "runn:10542", "subject", "subjects")
check("role is carried onto every entry",
      {e["role"] for e in canon.parse_ref_lines("run:1,run:2", "mentions", "m")},
      {"mentions"})

# ---------------------------------------------------------------------------
# merge_refs: one link per thing, strongest role wins
# ---------------------------------------------------------------------------

merged = canon.merge_refs(
    canon.parse_ref_lines("run:10542, system:trk", "subject", "s"),
    canon.parse_ref_lines("run:Run 10542", "mentions", "m"),
)
check("the same run given twice is one ref", len(merged), 2)
check(
    "subject beats mentions",
    next(m["role"] for m in merged if m["ref_type"] == "run"),
    "subject",
)
merged = canon.merge_refs(
    canon.parse_ref_lines("run:10542", "mentions", "m"),
    canon.parse_ref_lines("run:10542", "subject", "s"),
)
check("subject beats mentions whichever order they arrive in", merged[0]["role"], "subject")
check_raises(
    "too many refs rejected",
    canon.merge_refs,
    [canon.canon_ref("run", str(10000 + i), "subject")
     for i in range(canon.MAX_REFS_PER_REPORT + 1)],
)

# ---------------------------------------------------------------------------
# scalar helpers
# ---------------------------------------------------------------------------

check("tags are lower-cased", canon.parse_tags("TRK, DQM , ,"), ["trk", "dqm"])
check("no tags means None, not []", canon.parse_tags("  "), None)
check("attrs parses an object", canon.parse_attrs('{"alarmcode": 2}'), {"alarmcode": 2})
check("empty attrs is None", canon.parse_attrs(""), None)
check_raises("attrs rejects a JSON array", canon.parse_attrs, "[1,2]")
check_raises("attrs rejects malformed JSON", canon.parse_attrs, "{oops")
check("choice falls back to the default", canon.choice("", canon.SEVERITIES, "sev", "info"), "info")
check("choice is case-insensitive", canon.choice("WARNING", canon.SEVERITIES, "sev"), "warning")
check_raises("choice rejects the unknown", canon.choice, "disaster", canon.SEVERITIES, "sev")
check_raises("empty title rejected", canon.require, "  ", "title")

content = canon.validate_report_content(
    " A title ", "body text", "", "WARNING", "alarm", "", 1_000_000
)
check(
    "validate_report_content normalizes and defaults",
    content,
    ("A title", "body text", "", "warning", "alarm", "markdown"),
)
check_raises(
    "over-long title rejected",
    canon.validate_report_content,
    "x" * (canon.MAX_TITLE_CHARS + 1), "b", "", "info", "other", "markdown", 1_000_000,
)
check_raises(
    "over-large body rejected",
    canon.validate_report_content,
    "t", "x" * 101, "", "info", "other", "markdown", 100,
)

# ---------------------------------------------------------------------------

if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s)\n")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("All canonicalization checks passed.")
