"""Canonicalization, vocabularies and input validation for agentlog-mcp.

THE WHOLE DEDUP PREMISE RESTS ON THIS MODULE. agentlog.refs is a shared,
write-once catalog keyed by (ref_type, ref_key): one row per distinct thing,
however many reports point at it. That only works if two agents describing the
same thing two ways produce the same key. Run 10542 must always become
('run', '10542') -- not 'Run 10542', not '010542' -- or the index quietly
grows three entries for one run and a search for any of them misses the other
two. Nothing fails loudly when this is wrong, which is why it is concentrated
here instead of spread through the write path.

Deliberately imports nothing outside the standard library, so the rules can be
tested without a database, a server or an MCP client -- see
scripts/test_canon.py. Everything here is pure: text in, text out.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# These four mirror the CHECK constraints in sql/create_tables.sql. Validating
# in Python as well is not redundant: a CHECK violation reaches the agent as a
# wall of Postgres text, while these produce a message naming the allowed
# values. If you extend a vocabulary in the schema, extend it here too.
SEVERITIES = ("info", "warning", "error", "critical")
TRIGGERS = ("alarm", "schedule", "human", "agent", "other")
BODY_FORMATS = ("markdown", "text", "json")
REF_TYPES = (
    "run", "time", "system", "metric", "alarm", "dataset",
    "file", "document", "url", "report", "mcp", "free",
)
# Order matters: this is significance order, used by merge_refs to decide
# which role wins when the same thing arrives twice.
REF_ROLES = ("subject", "mentions", "supersedes", "followup_of", "comments_on")
# The report-to-report relations comment_on_report can write. 'subject' and
# 'mentions' are not relations between reports, so they are not offered.
RELATIONS = ("comments_on", "supersedes", "followup_of")

MAX_TITLE_CHARS = 300
MAX_SUMMARY_CHARS = 4_000

# A btree index tuple caps out around 2704 bytes, and UNIQUE (ref_type,
# ref_key) is a btree index. Rejecting a long key here turns what would be an
# opaque index error into a clear message.
MAX_REF_KEY_CHARS = 500

# Per report. A report with more subjects than this is almost certainly
# mis-using refs as a list of everything it consulted, which is exactly the
# concept the schema removed (decision 1).
MAX_REFS_PER_REPORT = 50

# refs.run_lo / run_hi are INTEGER; a run number past this is a typo, and
# letting it through would surface as an integer-overflow error from the
# database instead of a readable message.
MAX_RUN = 2_147_483_647

_RUN_RANGE_RE = re.compile(r"^(\d+)\s*(?:-|--|\.\.|\.\.\.|to|through)\s*(\d+)$", re.IGNORECASE)
_RUN_PREFIX_RE = re.compile(r"^runs?\b[\s:#]*", re.IGNORECASE)
_REPORT_PREFIX_RE = re.compile(r"^report\b[\s:#]*", re.IGNORECASE)
_DOCUMENT_RE = re.compile(r"^([A-Za-z][\w.-]*)\s*[:#\s]\s*(.+)$")
_DIGITS_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Scalar input helpers
# ---------------------------------------------------------------------------


def require(value: str, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required and cannot be empty")
    return text


def choice(value: str, allowed: tuple[str, ...], field_name: str, default: str = "") -> str:
    text = (value or "").strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise ValueError(f"{field_name} must be one of {', '.join(allowed)} -- got {value!r}")
    return text


def parse_tags(raw: str) -> list[str] | None:
    """Comma-separated -> normalized list, lower-cased so that matching is
    case-insensitive. Empty means no tags rather than an empty array."""
    tags = [t.strip().lower() for t in (raw or "").split(",") if t.strip()]
    return tags or None


def parse_attrs(raw: str, field_name: str = "attrs") -> dict[str, Any] | None:
    """Parse a JSON object from a string parameter.

    A string rather than an object parameter because every tool in this
    workspace takes scalars only -- some clients mangle nested schemas, and a
    JSON string survives all of them.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{field_name}: not valid JSON ({e})") from None
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{field_name}: must be a JSON object, e.g. '{{\"alarmcode\": 2}}' -- "
            f"got {type(parsed).__name__}"
        )
    return parsed


def parse_time(raw: str | None, field_name: str) -> datetime | None:
    """Parse an ISO 8601 timestamp. Input without a timezone is taken as UTC.

    A bare date means midnight UTC at the START of that day, which is a stated
    rule rather than a guess -- supply an explicit time to mean anything else.
    Deliberately not "helpfully" shifted to end-of-day, because an implicit
    adjustment is worse than a documented convention for a caller that has to
    reason about it.
    """
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{field_name}: could not parse {raw!r} -- use ISO 8601, e.g. '2026-11-12' "
            "(midnight UTC starting that day), '2026-11-12T03:14:00Z' or "
            "'2026-11-11T21:14:00-06:00'"
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso_z(value: datetime) -> str:
    """Canonical timestamp text: UTC, whole seconds, trailing Z.

    Truncated to the second so that two agents describing the same alarm
    interval -- one of them carrying microseconds out of a database row --
    produce the same key. Sub-second precision has no meaning for anything
    this log indexes.
    """
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def body_size(text: str) -> int:
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Per-type canonicalization. Each returns the pieces of one refs row.
# ---------------------------------------------------------------------------


def canon_run(raw: str) -> tuple[str, str | None, int, int]:
    """'Run 10542', '010542', '10500 - 10600' -> ('10542'|'10500-10600', label, lo, hi)."""
    text = _RUN_PREFIX_RE.sub("", raw.strip()).replace("_", "").strip()
    match = _RUN_RANGE_RE.match(text)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
    elif _DIGITS_RE.match(text):
        lo = hi = int(text)
    else:
        raise ValueError(
            f"run ref {raw!r}: expected a run number ('10542') or an inclusive range "
            "('10500-10600')"
        )
    if lo > hi:
        # A reversed range is a typo, not a request. Swapping rather than
        # erroring: the intent is unambiguous, and the alternative is failing a
        # whole report over the order of two numbers. It cannot simply be
        # passed through -- the database CHECK would reject run_hi < run_lo.
        lo, hi = hi, lo
    if hi > MAX_RUN:
        raise ValueError(f"run ref {raw!r}: run number above {MAX_RUN} is not a run number")
    key = str(lo) if lo == hi else f"{lo}-{hi}"
    label = f"run {lo}" if lo == hi else f"runs {lo}-{hi}"
    return key, label, lo, hi


def canon_time(raw: str) -> tuple[str, datetime, datetime]:
    """An instant or an ISO interval -> (canonical key, time_lo, time_hi)."""
    text = raw.strip()
    parts = [p for p in text.split("/") if p.strip()]
    if len(parts) == 1:
        lo = hi = parse_time(parts[0], "time ref")
    elif len(parts) == 2:
        lo = parse_time(parts[0], "time ref (start)")
        hi = parse_time(parts[1], "time ref (end)")
    else:
        raise ValueError(
            f"time ref {raw!r}: expected an instant ('2026-11-12T03:14:00Z') or an "
            "interval ('<from>/<to>')"
        )
    if lo is None or hi is None:  # unreachable: parse_time only returns None for empty
        raise ValueError(f"time ref {raw!r}: empty timestamp")
    if lo > hi:
        lo, hi = hi, lo  # same reasoning as canon_run
    key = iso_z(lo) if lo == hi else f"{iso_z(lo)}/{iso_z(hi)}"
    # The key is already the most readable rendering there is; a label would
    # only repeat it.
    return key, lo.replace(microsecond=0), hi.replace(microsecond=0)


def canon_document(raw: str) -> tuple[str, str | None]:
    """'DocDB 12345', 'docdb:012345', 'ECL 67890' -> 'docdb:12345', 'ecl:67890'."""
    text = " ".join(raw.split())
    match = _DOCUMENT_RE.match(text)
    if not match:
        raise ValueError(
            f"document ref {raw!r}: expected '<source>:<id>', e.g. 'docdb:12345', "
            "'ecl:67890' or 'wiki:TrackerHV'"
        )
    source, ident = match.group(1).lower(), match.group(2).strip()
    if _DIGITS_RE.match(ident):
        ident = str(int(ident))  # strip leading zeros
    pretty = {"docdb": "DocDB", "ecl": "ECL", "wiki": "wiki"}.get(source)
    label = f"{pretty} {ident}" if pretty else None
    return f"{source}:{ident}", label


def canon_url(raw: str) -> str:
    """Lower-case scheme and host, leave everything after them untouched.

    Paths and query strings are case-sensitive on plenty of servers, so only
    the two components that are defined to be case-insensitive are folded.
    """
    split = urlsplit(raw.strip())
    if not split.scheme or not split.netloc:
        raise ValueError(
            f"url ref {raw!r}: expected an absolute URL including scheme, "
            "e.g. 'https://host/path'"
        )
    return urlunsplit(
        (split.scheme.lower(), split.netloc.lower(), split.path, split.query, split.fragment)
    )


def canon_ref(ref_type: str, raw_key: str, role: str) -> dict[str, Any]:
    """One raw '<type>:<key>' pair -> the row that goes into refs.

    Case handling is per type and deliberate, not uniform: subsystem names are
    lower-case everywhere in the Mu2e schemas, so they are folded; metric,
    dataset and file names are case-sensitive identifiers that would stop
    matching their source if folded, so they are only whitespace-normalized.
    """
    if ref_type not in REF_TYPES:
        raise ValueError(f"unknown ref type {ref_type!r} -- use one of {', '.join(REF_TYPES)}")
    if role not in REF_ROLES:
        raise ValueError(f"unknown role {role!r} -- use one of {', '.join(REF_ROLES)}")

    key = (raw_key or "").strip()
    if not key:
        raise ValueError(f"{ref_type} ref: key is empty")

    label: str | None = None
    run_lo = run_hi = None
    time_lo = time_hi = None

    if ref_type == "run":
        key, label, run_lo, run_hi = canon_run(key)
    elif ref_type == "time":
        key, time_lo, time_hi = canon_time(key)
    elif ref_type == "system":
        key = " ".join(key.split()).lower()
    elif ref_type in ("metric", "alarm"):
        # 'trk / track / meanChi2' -> 'trk/track/meanChi2'. Case preserved:
        # namex is camelCase in dqm.values, and folding it would stop the key
        # matching its source.
        key = re.sub(r"\s*/\s*", "/", " ".join(key.split()))
    elif ref_type in ("dataset", "file", "mcp"):
        key = " ".join(key.split())
    elif ref_type == "document":
        key, label = canon_document(key)
    elif ref_type == "url":
        key = canon_url(key)
    elif ref_type == "report":
        digits = _REPORT_PREFIX_RE.sub("", key).strip()
        if not _DIGITS_RE.match(digits):
            raise ValueError(f"report ref {raw_key!r}: expected a report_id, e.g. 'report:418'")
        key = str(int(digits))
        label = f"report {key}"
    else:  # 'free'
        key = " ".join(key.split())

    if len(key) > MAX_REF_KEY_CHARS:
        raise ValueError(
            f"{ref_type} ref key is {len(key)} characters, over the {MAX_REF_KEY_CHARS} "
            "limit (it is part of a unique index). Put the detail in the report body."
        )

    return {
        "ref_type": ref_type,
        "ref_key": key,
        "label": label,
        "run_lo": run_lo,
        "run_hi": run_hi,
        "time_lo": time_lo,
        "time_hi": time_hi,
        "role": role,
    }


def parse_ref_lines(raw: str, role: str, field_name: str) -> list[dict[str, Any]]:
    """Parse a '<type>:<key>' list into canonical refs rows.

    One entry per line; commas also work when no key contains one. Only the
    FIRST colon splits, so 'url:https://x/y' and
    'time:2026-11-12T03:00:00Z/2026-11-12T09:00:00Z' survive intact -- but a
    key containing a comma (free text, a URL with a comma) must be given on
    its own line.
    """
    text = (raw or "").strip()
    if not text:
        return []
    chunks = text.splitlines() if "\n" in text else text.split(",")
    entries: list[dict[str, Any]] = []
    for chunk in chunks:
        item = chunk.strip().strip(",").strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(
                f"{field_name}: {item!r} is not '<type>:<key>'. Types: "
                f"{', '.join(REF_TYPES)}. Example: 'run:10542'"
            )
        raw_type, raw_key = item.split(":", 1)
        ref_type = raw_type.strip().lower()
        if ref_type not in REF_TYPES:
            raise ValueError(
                f"{field_name}: unknown ref type {raw_type.strip()!r} in {item!r} -- "
                f"use one of {', '.join(REF_TYPES)}"
            )
        entries.append(canon_ref(ref_type, raw_key, role))
    return entries


def merge_refs(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by (ref_type, ref_key), keeping the most significant role.

    The same run given as both a subject and a mention is one link, not two:
    REF_ROLES is in significance order and 'subject' wins. Without this a
    display would print the thing twice.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            key = (entry["ref_type"], entry["ref_key"])
            existing = merged.get(key)
            if existing is None:
                merged[key] = entry
            elif REF_ROLES.index(entry["role"]) < REF_ROLES.index(existing["role"]):
                merged[key] = entry
    if len(merged) > MAX_REFS_PER_REPORT:
        raise ValueError(
            f"{len(merged)} references on one report, over the {MAX_REFS_PER_REPORT} limit. "
            "Subjects are an index of what the report is ABOUT -- if this is a list of "
            "everything consulted, that belongs in the body as prose."
        )
    return list(merged.values())


def validate_report_content(
    title: str,
    body: str,
    summary: str,
    severity: str,
    triggered_by: str,
    body_format: str,
    max_body_bytes: int,
) -> tuple[str, str, str, str, str, str]:
    """Check and normalize the content fields shared by both write tools."""
    title = require(title, "title")
    body = require(body, "body")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(
            f"title is {len(title)} characters, over the {MAX_TITLE_CHARS} limit -- it is "
            "one line for a list display; put the detail in summary or body"
        )
    if len(summary or "") > MAX_SUMMARY_CHARS:
        raise ValueError(
            f"summary is {len(summary)} characters, over the {MAX_SUMMARY_CHARS} limit -- "
            "it is a short paragraph for a digest; put the detail in body"
        )
    if body_size(body) > max_body_bytes:
        raise ValueError(
            f"body is {body_size(body)} bytes, over the {max_body_bytes} byte limit"
        )
    return (
        title,
        body,
        (summary or "").strip(),
        choice(severity, SEVERITIES, "severity", "info"),
        choice(triggered_by, TRIGGERS, "triggered_by", "other"),
        choice(body_format, BODY_FORMATS, "body_format", "markdown"),
    )
