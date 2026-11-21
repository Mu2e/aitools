"""agentlog-mcp: a log of agent investigations, backed by Postgres.

An agent is woken by an alarm (or a cron, or a human), works the problem with
whatever tools it has -- dqm, runs, metacat, ecl, kb -- and then files a report
here. Displays, digests, Slack summaries and later agents read those reports
back out.

Three things to know before reading the code:

1. IT WRITES, APPEND-ONLY. The database role is granted INSERT and SELECT and
   nothing else. A report is never edited and never hidden; a correction, a
   follow-up or a human's comment is a NEW report pointing at the old one. So
   there is no update_report tool and there never should be -- the grants
   would refuse it anyway. See sql/create_tables.sql for the admin-only
   retraction path.

2. REFS ARE AN INDEX, NOT A PROVENANCE RECORD. A ref answers one question:
   "which reports are about this run / this night / this subsystem?" What a
   report *consulted* while being written is prose in its body. A refs row is
   shared between reports and can never be updated, so it can only hold facts
   about the thing itself -- see decision 1 in the schema for the three-step
   bug that taught us this.

3. CANONICALIZATION IS THIS SERVER'S JOB, and it is the only interesting code
   in the write path. It all lives in canon.py, which imports nothing outside
   the standard library so the rules can be tested without a database --
   scripts/test_canon.py does exactly that.

Unlike memory-mcp there is no owner boundary: every report is readable by
anyone who can read the schema. `author` is the authenticated key's name and
is accountability, not access control -- never a tool argument. mikey auth is
still mandatory, because a report nobody can be held to is not worth filing.

No database endpoint is baked into this package. Host, port and database name
come from the environment or the command line; the server refuses to start
without them.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import importlib.metadata
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mikey import build_auth_kwargs
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from agentlog_mcp import canon
from agentlog_mcp.canon import REF_ROLES, REF_TYPES, RELATIONS, SEVERITIES, TRIGGERS

LOGGER = logging.getLogger("agentlog_mcp")

# Bind address, not a site address: 0.0.0.0 means "every interface on whatever
# host this happens to run on". The PORT is deliberately not defaulted here --
# see _parse_args.
DEFAULT_HOST = "0.0.0.0"

# The schema name is part of this package's own DDL (sql/create_tables.sql),
# so it belongs here. The write role is part of the same DDL's grant design.
DEFAULT_DB_SCHEMA = "agentlog"
DEFAULT_WRITE_ROLE = "update_role"
DEFAULT_DB_TIMEOUT_SECONDS = 30
DEFAULT_DB_POOL_MAX = 4

# Pinned, not configurable. psycopg warns (RuntimeWarning, at every
# connection) that libpq's default is not reliably 'prefer' in
# psycopg[binary] builds -- some behave as 'disable'. The connection
# authenticates with GSSAPI either way; this decides whether the TRANSPORT is
# GSSAPI-encrypted, which should not vary silently with how the wheel was
# built.
#
# 'require' rather than libpq's documented 'prefer' default: the database
# offers GSSAPI encryption, so there is no reason to accept a plain-text
# fallback that nothing would report. Verified 2026-11-20 against the
# deployed release (PGGSSENCMODE=require, `--check` -> database ok) -- which
# is the test that matters, because the server supporting gss encryption is
# not enough on its own: psycopg[binary] bundles its OWN libpq, and that is
# what has to be able to negotiate it.
#
# No bespoke flag or AGENTLOG_MCP_* variable: 'disable' is never wanted, and
# this is a hardening choice to take once for every MCP rather than vary per
# install. The standard libpq PGGSSENCMODE is honored, which is the recovery
# path if the database ever stops offering gss encryption -- set
# PGGSSENCMODE=prefer in the unit's EnvironmentFile and restart, no redeploy.
GSSENCMODE = os.environ.get("PGGSSENCMODE") or "require"

# Ceiling on one report body. Not a Postgres limit -- a limit on how much any
# single agent can dump into a shared, undeletable log.
MAX_BODY_BYTES = 1_048_576  # 1 MiB

# get_report refuses to return a body larger than this inline, so a caller
# cannot blow its own context by asking for a report without meaning to. Same
# reasoning as memory-mcp's get_document and runs-mcp's get_config_blob.
DEFAULT_MAX_INLINE_BYTES = 100_000

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_ORDER_OPTIONS = ("recent", "severity", "relevance")

INSTRUCTIONS = (
    "A log of agent investigations. File a report here when you have "
    "investigated something and the conclusion is worth keeping: an alarm you "
    "chased down, a nightly validation you checked, a question a human asked "
    "that took real work to answer. Displays, digests and later agents read "
    "these back.\n\n"
    "APPEND-ONLY. Nothing can edit or delete a report, including you. To "
    "correct or add to one, file a NEW report with comment_on_report -- the "
    "original stays readable, with your correction linked to it. The whole "
    "trail is exposed on purpose, wrong reports included.\n\n"
    "THE REPORT BODY IS WHERE THE WORK GOES: what fired, how you checked it, "
    "which tools you called and what they returned, what you concluded and "
    "how confident you are. There are no columns for any of that, by design "
    "-- a reader wants your reasoning with its justification attached, not a "
    "bare enum. Markdown by convention.\n\n"
    "SUBJECTS ARE THE INDEX. `subjects` is what the report is ABOUT, as "
    "'<type>:<key>' entries, one per line (or comma-separated): "
    "'run:10542', 'system:trk', 'metric:trk/track/meanChi2', "
    "'time:2026-11-11T20:00:00Z/2026-11-12T08:00:00Z'. They are what makes a "
    "report findable later -- a report filed with no subjects is nearly "
    "unfindable, so always give at least the run or the time window and the "
    "subsystem. Keys are canonicalized for you ('Run 10542' -> '10542').\n\n"
    "SUBJECTS ARE NOT A TOOL LIST. 'I called dqm.list_alarms' belongs in the "
    "body as prose, not in subjects; full-text search covers it. An "
    "'mcp:<tool>' subject means the report is ABOUT that tool (it is too "
    "slow, it returns wrong data), not that you used it.\n\n"
    "READING: list_reports filters the log (by run, time window, subject, "
    "severity, author, tags, or full text) and never returns bodies -- check "
    "body_size, then get_report for the one you want, which also returns its "
    "references and the correction/comment trail around it. list_refs browses "
    "the index itself: which runs, nights or subsystems have reports at all.\n\n"
    "TIMES: every timestamp returned is UTC. A time you supply without a "
    "timezone is read as UTC, and a bare date means midnight at the START of "
    "that day."
)


@dataclass
class _Config:
    # No defaults for the endpoint: an agentlog that silently connected to the
    # wrong database would be worse than one that refuses to start, and this
    # repository deliberately records no host, port or database name.
    db_host: str = os.environ.get("AGENTLOG_MCP_DB_HOST", "")
    db_port: int = int(os.environ.get("AGENTLOG_MCP_DB_PORT", "0") or 0)
    db_name: str = os.environ.get("AGENTLOG_MCP_DB_NAME", "")
    db_schema: str = os.environ.get("AGENTLOG_MCP_DB_SCHEMA", DEFAULT_DB_SCHEMA)
    write_role: str = os.environ.get("AGENTLOG_MCP_WRITE_ROLE", DEFAULT_WRITE_ROLE)
    db_timeout_seconds: int = int(
        os.environ.get("AGENTLOG_MCP_DB_TIMEOUT_SECONDS", str(DEFAULT_DB_TIMEOUT_SECONDS))
    )
    db_pool_max: int = int(os.environ.get("AGENTLOG_MCP_DB_POOL_MAX", str(DEFAULT_DB_POOL_MAX)))
    max_body_bytes: int = int(os.environ.get("AGENTLOG_MCP_MAX_BODY_BYTES", str(MAX_BODY_BYTES)))
    unset: list[str] = field(default_factory=list)


_config = _Config()


def _server_version() -> str:
    try:
        return importlib.metadata.version("agentlog-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


mcp = MCPServer("agentlog", instructions=INSTRUCTIONS, **build_auth_kwargs())


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _author() -> str:
    """The authenticated caller's mikey name, stored as reports.author.

    Fails closed. Unlike memory-mcp this is not an access-control boundary --
    every report is readable by every caller -- but it is the only
    accountability the log has: who stands behind this report. An anonymous
    entry in an undeletable log is worse than no entry.
    """
    token = get_access_token()
    if token is None or not token.client_id:
        raise ToolError(
            "no authenticated identity -- agentlog-mcp requires a mikey bearer token, "
            "because the token's name is recorded as the report's author. Check that "
            "this server was started with MIKEY_KEYS_FILE set and that your client "
            "sends an Authorization: Bearer header."
        )
    return token.client_id


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_pool_lock = threading.Lock()
_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """Lazily-built connection pool.

    Lazy so that importing this module (which happens before main() parses
    args) never touches the network. Pooled rather than connect-per-call
    because each new connection costs a Kerberos handshake; `check` replaces
    connections that died while idle, which matters for a server that may sit
    untouched for hours between alarms.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            conninfo = psycopg.conninfo.make_conninfo(
                host=_config.db_host,
                port=_config.db_port,
                dbname=_config.db_name,
                application_name="agentlog-mcp",
                connect_timeout=_config.db_timeout_seconds,
                gssencmode=GSSENCMODE,
            )
            _pool = ConnectionPool(
                conninfo,
                min_size=1,
                max_size=_config.db_pool_max,
                check=ConnectionPool.check_connection,
                timeout=_config.db_timeout_seconds,
                open=True,
            )
        return _pool


@contextlib.contextmanager
def _read_cursor():
    """Cursor for reads, running as the plain login role.

    Deliberately does NOT assume the write role. The login role is granted
    SELECT directly, so reads work without elevation -- and an accidental
    INSERT from a code path that was only meant to read fails at the database
    rather than silently succeeding. In an append-only log where a stray write
    is permanent and cannot be deleted, that is worth the extra grant.
    """
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


@contextlib.contextmanager
def _write_cursor():
    """Cursor for writes: one transaction, with the write role assumed for it.

    Atomicity matters more here than in memory-mcp: filing a report inserts
    into reports, refs and report_refs, and a report that committed without
    its subjects would be permanently unfindable with no way to add them
    later. `SET LOCAL ROLE` (not plain `SET ROLE`) means the elevation reverts
    on commit or rollback, so it cannot leak to whichever request picks up
    this pooled connection next.
    """
    with _get_pool().connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(_config.write_role))
                )
                yield cur


def _q(text: str) -> sql.Composed:
    """Compose a query with {s} substituted by the configured schema name."""
    return sql.SQL(text).format(s=sql.Identifier(_config.db_schema))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _wrap(fn):
    """Turn exceptions into a ToolError carrying a readable message.

    Raising rather than returning an error dict: the SDK validates a returned
    value against the tool's declared output schema, so a dict returned from a
    list[...]-annotated tool is rejected as a crash and the real message is
    withheld from the caller. ToolError is the SDK's intended channel for an
    anticipated failure and bypasses output conversion entirely.
    """

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except ValueError as e:
            raise ToolError(f"bad_request: {e}") from e
        except psycopg.Error as e:
            LOGGER.exception("agentlog-mcp tool %s failed (database)", fn.__name__)
            raise ToolError(f"database_error: {type(e).__name__}: {e}") from e
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            LOGGER.exception("agentlog-mcp tool %s failed", fn.__name__)
            raise ToolError(f"tool_failed: {type(e).__name__}: {e}") from e

    return inner


def _iso(value: Any) -> Any:
    """Serialize a timestamp as ISO 8601 in UTC.

    Every timestamp column is TIMESTAMPTZ, which stores an absolute instant
    and renders in the session's timezone on the way out -- so without this
    conversion a time supplied as UTC comes back rendered in the database
    server's local zone. Same instant, but it reads as though the time moved.
    See canon.parse_time for the input half of the same convention.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _row(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in record.items()}


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def _insert_report(
    cur,
    author: str,
    agent: str,
    triggered_by: str,
    severity: str,
    title: str,
    summary: str,
    body: str,
    body_format: str,
    tags: list[str] | None,
    attrs: dict[str, Any] | None,
) -> dict[str, Any]:
    return cur.execute(
        _q(
            "INSERT INTO {s}.reports "
            "    (author, agent, triggered_by, severity, title, summary, body, "
            "     body_format, tags, attrs) "
            "VALUES (%(author)s, %(agent)s, %(triggered_by)s, %(severity)s, %(title)s, "
            "        %(summary)s, %(body)s, %(body_format)s, %(tags)s, %(attrs)s) "
            "RETURNING report_id, create_time"
        ),
        {
            "author": author,
            "agent": agent or None,
            "triggered_by": triggered_by,
            "severity": severity,
            "title": title,
            "summary": summary or None,
            "body": body,
            "body_format": body_format,
            "tags": tags,
            "attrs": Jsonb(attrs) if attrs is not None else None,
        },
    ).fetchone()


def _link_refs(cur, report_id: int, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup each ref, then link it to the report.

    INSERT ... ON CONFLICT DO NOTHING followed by a SELECT, never ON CONFLICT
    DO UPDATE: the write role has no UPDATE privilege anywhere in this schema
    (decision 6), so DO UPDATE would be refused at runtime. The RETURNING
    clause doubles as the "was it new" flag -- a conflicting insert returns no
    row -- which saves a round trip and tells the caller whether the label it
    supplied is the one that stuck.
    """
    linked: list[dict[str, Any]] = []
    for entry in refs:
        row = cur.execute(
            _q(
                "INSERT INTO {s}.refs (ref_type, ref_key, label, run_lo, run_hi, "
                "                      time_lo, time_hi) "
                "VALUES (%(ref_type)s, %(ref_key)s, %(label)s, %(run_lo)s, %(run_hi)s, "
                "        %(time_lo)s, %(time_hi)s) "
                "ON CONFLICT (ref_type, ref_key) DO NOTHING "
                "RETURNING ref_id"
            ),
            entry,
        ).fetchone()
        if row is not None:
            ref_id, is_new = row["ref_id"], True
        else:
            existing = cur.execute(
                _q("SELECT ref_id FROM {s}.refs WHERE ref_type=%s AND ref_key=%s"),
                (entry["ref_type"], entry["ref_key"]),
            ).fetchone()
            ref_id, is_new = existing["ref_id"], False

        cur.execute(
            _q(
                "INSERT INTO {s}.report_refs (report_id, ref_id, role) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING"
            ),
            (report_id, ref_id, entry["role"]),
        )
        linked.append(
            {
                "ref_id": ref_id,
                "ref_type": entry["ref_type"],
                "ref_key": entry["ref_key"],
                "role": entry["role"],
                "new_ref": is_new,
            }
        )
    return linked


def _ref_exists_clause(index: int, condition: str, role_param: str | None) -> str:
    """One EXISTS subquery against report_refs/refs, aliased per filter.

    EXISTS rather than a join so that two ref filters mean "a report carrying
    BOTH" without multiplying rows -- and so that a report with twelve
    subjects is still one row in the result.
    """
    role_sql = f" AND rr{index}.role = %({role_param})s" if role_param else ""
    return (
        f"EXISTS (SELECT 1 FROM {{s}}.report_refs rr{index} "
        f"          JOIN {{s}}.refs f{index} ON f{index}.ref_id = rr{index}.ref_id "
        f"         WHERE rr{index}.report_id = r.report_id{role_sql} AND {condition})"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(description="Get agentlog-mcp configuration, version, and the calling author.")
@_wrap
def get_server_info() -> dict[str, Any]:
    return {
        "name": "agentlog",
        "version": _server_version(),
        "transport": "streamable-http",
        "auth": "mikey" if mcp.settings.auth else "disabled",
        "author": _author(),
        "database": f"{_config.db_host}:{_config.db_port}/{_config.db_name}",
        "schema": _config.db_schema,
        "append_only": True,
        "ref_types": list(REF_TYPES),
        "severities": list(SEVERITIES),
        "triggers": list(TRIGGERS),
        "relations": list(RELATIONS),
        "max_body_bytes": _config.max_body_bytes,
        "max_refs_per_report": canon.MAX_REFS_PER_REPORT,
        "default_max_inline_bytes": DEFAULT_MAX_INLINE_BYTES,
    }


@mcp.tool(name="file_report")
@_wrap
def file_report(
    title: str,
    body: str,
    summary: str = "",
    severity: str = "info",
    triggered_by: str = "other",
    agent: str = "",
    subjects: str = "",
    mentions: str = "",
    tags: str = "",
    attrs: str = "",
    body_format: str = "markdown",
) -> dict[str, Any]:
    """File a report of an investigation. Permanent -- it cannot be edited or deleted.

    Write the reasoning into `body`: what you were asked or what fired, how you
    checked it, which tools you called and what they returned, what you
    concluded, and how sure you are. There are no columns for those things on
    purpose -- a reader wants them with their justification attached.

    To correct or extend a report later, use comment_on_report. There is no way
    to change this one once it is filed.

    Args:
        title: one line, for a list display or a Slack notification (<=300 chars)
        body: the full report. Markdown by convention.
        summary: a short paragraph for a digest or hover card. Omit and
              displays fall back to the title.
        severity: info (default), warning, error, or critical. What a digest
              filters on -- 'critical' should mean someone needs to look now.
        triggered_by: alarm, schedule, human, agent, or other (default). Why
              you were investigating.
        agent: the role that wrote this ('dqm-alarm-watch',
              'nightly-validation'). The author is taken from your key; this
              says which procedure ran, which a shared key cannot tell anyone.
        subjects: what the report is ABOUT -- the index that makes it findable.
              '<type>:<key>' entries, one per line (or comma-separated):
                  run:10542                 a run, or a range: run:10500-10600
                  time:2026-11-11T20:00:00Z/2026-11-12T08:00:00Z
                  system:trk
                  metric:trk/track/meanChi2
                  alarm:trk/track/meanChi2@valNightly
                  dataset:mu2e:mcs.mu2e.CeEndpoint...
                  file:nts.mu2e....root
                  document:docdb:12345      also ecl:67890, wiki:TrackerHV
                  url:https://...
                  report:418                another agentlog report
                  mcp:dqm.list_alarms       the report is about the TOOL
                  free:anything else
              Keys are canonicalized ('Run 10542' -> '10542'), so give them
              however you have them. Always give at least the run or the time
              window plus the subsystem: a report with no subjects is nearly
              unfindable.
        mentions: same syntax, for a passing reference rather than the subject.
        tags: comma-separated free labels, stored lower-cased ('trk,dqm')
        attrs: a JSON object for anything a downstream procedure would rather
              parse than regex out of the body -- the alarm payload, the metric
              values the conclusion rests on. Example:
              '{"alarmcode": 2, "nevents": 40}'
        body_format: markdown (default), text, or json

    NOT for subjects: the tools you used. 'I called dqm.list_alarms' is prose
    in the body; full-text search finds it. 'mcp:dqm.list_alarms' means the
    report is about that tool being slow or wrong.

    Returns report_id, create_time, author, the linked refs (with new_ref
    telling you which ones you created), and any warnings.
    """
    author = _author()
    title, body, summary, severity, triggered_by, body_format = canon.validate_report_content(
        title, body, summary, severity, triggered_by, body_format, _config.max_body_bytes
    )
    refs = canon.merge_refs(
        canon.parse_ref_lines(subjects, "subject", "subjects"),
        canon.parse_ref_lines(mentions, "mentions", "mentions"),
    )
    tag_list = canon.parse_tags(tags)
    attr_obj = canon.parse_attrs(attrs)

    with _write_cursor() as cur:
        created = _insert_report(
            cur, author, agent.strip(), triggered_by, severity, title, summary,
            body, body_format, tag_list, attr_obj,
        )
        report_id = created["report_id"]
        linked = _link_refs(cur, report_id, refs)

    warnings: list[str] = []
    if not any(r["role"] == "subject" for r in linked):
        warnings.append(
            "no subjects given, so this report will not come back from any run, time "
            "or subsystem search -- only from full-text. Consider filing a follow-up "
            "with subjects, since this one cannot be edited."
        )
    return {
        "report_id": report_id,
        "create_time": _iso(created["create_time"]),
        "author": author,
        "severity": severity,
        "refs": linked,
        "warnings": warnings,
    }


@mcp.tool(name="comment_on_report")
@_wrap
def comment_on_report(
    report_id: int,
    body: str,
    relation: str = "comments_on",
    title: str = "",
    summary: str = "",
    severity: str = "info",
    triggered_by: str = "human",
    agent: str = "",
    subjects: str = "",
    mentions: str = "",
    tags: str = "",
    attrs: str = "",
    inherit_subjects: bool = True,
) -> dict[str, Any]:
    """Comment on, correct, or continue an existing report.

    This is the only way to change what the log says about something, and it
    does not change anything: the original report stays exactly as filed and
    stays in every listing. Your new report is linked to it, and displays show
    the trail (get_report returns both directions; list_reports flags a
    corrected report with corrected_by).

    Args:
        report_id: the report being commented on. Must exist.
        body: what you have to say. Markdown by convention.
        relation: how this report relates to that one --
              comments_on (default) you are adding a human or agent remark;
              supersedes  you are correcting it: your conclusion replaces its
                          conclusion. A claim recorded in the trail, not a
                          delete -- the original is still served and still read.
              followup_of you are continuing the investigation.
        title: one line. Generated from the relation if you omit it.
        summary, severity, agent, tags, attrs, mentions: as in file_report.
        triggered_by: defaults to 'human' here, since that is the usual case
              for a comment. Pass 'agent' when a procedure filed it.
        subjects: extra subjects for this report, same syntax as file_report.
        inherit_subjects: also link this report to everything the target report
              had as a SUBJECT (default true). That is what makes a correction
              about run 10542 come back from a search for run 10542 -- without
              it only the original would, and the correction would be the thing
              nobody finds. Pass false to index it on its own subjects only.

    Returns the new report_id, the link that was written, and the refs linked.
    """
    author = _author()
    relation = canon.choice(relation, RELATIONS, "relation", "comments_on")
    if report_id <= 0:
        raise ValueError(f"report_id must be a positive report id, got {report_id}")

    default_titles = {
        "comments_on": f"Comment on report {report_id}",
        "supersedes": f"Correction of report {report_id}",
        "followup_of": f"Follow-up to report {report_id}",
    }
    title, body, summary, severity, triggered_by, body_format = canon.validate_report_content(
        title or default_titles[relation], body, summary, severity, triggered_by,
        "markdown", _config.max_body_bytes,
    )
    extra_refs = canon.parse_ref_lines(subjects, "subject", "subjects") + canon.parse_ref_lines(
        mentions, "mentions", "mentions"
    )
    tag_list = canon.parse_tags(tags)
    attr_obj = canon.parse_attrs(attrs)

    with _write_cursor() as cur:
        # Checked inside the write transaction: the point is that the link
        # cannot be written against a report that does not exist, and there is
        # no foreign key on ref_type='report' to enforce it (decision 7).
        # Reports are never deleted, so a target that exists now exists
        # forever -- no race to worry about on the other side.
        target = cur.execute(
            _q("SELECT report_id, title FROM {s}.reports WHERE report_id = %s"),
            (report_id,),
        ).fetchone()
        if target is None:
            raise ToolError(
                f"not_found: no report {report_id} (use list_reports to find the right id)"
            )

        inherited: list[dict[str, Any]] = []
        if inherit_subjects:
            # Taken straight out of refs, already canonical, and passed
            # through rather than re-canonicalized: canon_ref is for caller
            # input, and running it here would turn a key this version does
            # not know how to re-derive (a ref_type added later) into an
            # error on an otherwise valid comment. The projection columns are
            # copied for the same reason -- re-deriving them must not drift
            # from what the original row says.
            rows = cur.execute(
                _q(
                    "SELECT f.ref_type, f.ref_key, f.run_lo, f.run_hi, f.time_lo, f.time_hi "
                    "  FROM {s}.report_refs rr "
                    "  JOIN {s}.refs f ON f.ref_id = rr.ref_id "
                    " WHERE rr.report_id = %s AND rr.role = 'subject' "
                    "   AND f.ref_type <> 'report'"
                ),
                (report_id,),
            ).fetchall()
            inherited = [{**dict(row), "label": None, "role": "subject"} for row in rows]

        # The link to the target report is the one ref that must keep its
        # role: if the caller also listed 'report:<id>' as a subject,
        # merge_refs would prefer 'subject' (the stronger role) and the
        # comment/correction edge would silently not be recorded. Drop any
        # duplicate of the target first, then let the relation stand.
        link_ref = canon.canon_ref("report", str(report_id), relation)
        target_key = (link_ref["ref_type"], link_ref["ref_key"])
        extra_refs = [e for e in extra_refs if (e["ref_type"], e["ref_key"]) != target_key]
        refs = canon.merge_refs(extra_refs, inherited, [link_ref])

        created = _insert_report(
            cur, author, agent.strip(), triggered_by, severity, title, summary,
            body, body_format, tag_list, attr_obj,
        )
        new_id = created["report_id"]
        linked = _link_refs(cur, new_id, refs)

    return {
        "report_id": new_id,
        "create_time": _iso(created["create_time"]),
        "author": author,
        "link": {"from_report": new_id, "to_report": report_id, "role": relation},
        "commented_on": {"report_id": report_id, "title": target["title"]},
        "refs": linked,
        "inherited_subjects": len(inherited),
    }


@mcp.tool(name="list_reports")
@_wrap
def list_reports(
    run: int | None = None,
    run_lo: int | None = None,
    run_hi: int | None = None,
    time_from: str = "",
    time_to: str = "",
    ref: str = "",
    ref_role: str = "",
    text: str = "",
    severity: str = "",
    min_severity: str = "",
    triggered_by: str = "",
    author: str = "",
    agent: str = "",
    tags: str = "",
    since: str = "",
    until: str = "",
    order_by: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Find reports. Returns one row per report WITHOUT the body.

    This is the "decide what is worth reading" call: check body_size, then
    get_report for the one you want. Nothing is ever filtered out of the log
    for being wrong or superseded -- a corrected report still appears, with
    corrected_by naming the report that corrected it, so you see the trail
    rather than a silently edited history.

    Args:
        run: reports whose run refs COVER this run (a '10500-10600' range ref
              matches run 10542). The usual way to ask "what do we know about
              this run".
        run_lo, run_hi: reports whose run refs OVERLAP this range. Either one
              alone is open-ended.
        time_from, time_to: reports whose time refs overlap this window. ISO
              8601; read as UTC without a timezone, and a bare date means
              midnight at the START of that day.
        ref: one exact subject, '<type>:<key>' -- 'system:trk',
              'metric:trk/track/meanChi2', 'document:docdb:12345'. Canonicalized
              the same way as when it was filed, so give it however you have it.
        ref_role: restrict the run/time/ref matching to one role, usually
              'subject' to exclude passing mentions. Default: any role.
        text: full-text search over title, summary and body. This is how you
              find "which reports discussed low statistics", or which ones
              mention a tool, since tool use is prose rather than a ref.
        severity: exactly one of info, warning, error, critical.
        min_severity: that severity and anything worse -- 'warning' excludes
              info. The usual filter for a digest.
        triggered_by: alarm, schedule, human, agent, other.
        author: the key that filed it (exact match).
        agent: the procedure that filed it (exact match).
        tags: comma-separated; matches a report carrying ANY of them.
        since, until: when the report was FILED -- not what it is about, which
              is time_from/time_to. Same ISO rules.
        order_by: recent (default), severity (worst first), or relevance
              (default when `text` is given; only meaningful then).
        limit: max rows (default 50, capped at 500).

    Each row: report_id, create_time, author, agent, triggered_by, severity,
    title, summary, tags, body_size, n_refs, subjects, corrected_by,
    commented_by -- and rank when ordered by relevance.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    order_by = canon.choice(
        order_by, _ORDER_OPTIONS, "order_by", "relevance" if text.strip() else "recent"
    )
    if order_by == "relevance" and not text.strip():
        raise ValueError("order_by='relevance' needs a `text` search to rank against")

    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    role_param: str | None = None

    if ref_role.strip():
        params["ref_role"] = canon.choice(ref_role, REF_ROLES, "ref_role")
        role_param = "ref_role"

    ref_index = 0
    if run is not None:
        ref_index += 1
        clauses.append(
            _ref_exists_clause(
                ref_index,
                f"f{ref_index}.ref_type = 'run' "
                f"AND %(run)s BETWEEN f{ref_index}.run_lo AND f{ref_index}.run_hi",
                role_param,
            )
        )
        params["run"] = run
    if run_lo is not None or run_hi is not None:
        ref_index += 1
        bounds = [f"f{ref_index}.ref_type = 'run'"]
        if run_hi is not None:
            bounds.append(f"f{ref_index}.run_lo <= %(run_hi)s")
            params["run_hi"] = run_hi
        if run_lo is not None:
            bounds.append(f"f{ref_index}.run_hi >= %(run_lo)s")
            params["run_lo"] = run_lo
        clauses.append(_ref_exists_clause(ref_index, " AND ".join(bounds), role_param))
    if time_from.strip() or time_to.strip():
        ref_index += 1
        bounds = [f"f{ref_index}.ref_type = 'time'"]
        if time_to.strip():
            bounds.append(f"f{ref_index}.time_lo <= %(time_to)s")
            params["time_to"] = canon.parse_time(time_to, "time_to")
        if time_from.strip():
            bounds.append(f"f{ref_index}.time_hi >= %(time_from)s")
            params["time_from"] = canon.parse_time(time_from, "time_from")
        clauses.append(_ref_exists_clause(ref_index, " AND ".join(bounds), role_param))
    if ref.strip():
        if ":" not in ref:
            raise ValueError(
                f"ref: {ref!r} is not '<type>:<key>' -- e.g. 'system:trk'. Types: "
                f"{', '.join(REF_TYPES)}"
            )
        raw_type, raw_key = ref.split(":", 1)
        canonical = canon.canon_ref(
            canon.choice(raw_type, REF_TYPES, "ref type"), raw_key, "subject"
        )
        ref_index += 1
        clauses.append(
            _ref_exists_clause(
                ref_index,
                f"f{ref_index}.ref_type = %(ref_type)s AND f{ref_index}.ref_key = %(ref_key)s",
                role_param,
            )
        )
        params["ref_type"] = canonical["ref_type"]
        params["ref_key"] = canonical["ref_key"]

    if text.strip():
        clauses.append("r.search_vec @@ websearch_to_tsquery('english', %(text)s)")
        params["text"] = text.strip()
    if severity.strip():
        clauses.append("r.severity = %(severity)s")
        params["severity"] = canon.choice(severity, SEVERITIES, "severity")
    if min_severity.strip():
        clauses.append(
            "array_position(%(severity_order)s::text[], r.severity) >= "
            "array_position(%(severity_order)s::text[], %(min_severity)s)"
        )
        params["severity_order"] = list(SEVERITIES)
        params["min_severity"] = canon.choice(min_severity, SEVERITIES, "min_severity")
    if triggered_by.strip():
        clauses.append("r.triggered_by = %(triggered_by)s")
        params["triggered_by"] = canon.choice(triggered_by, TRIGGERS, "triggered_by")
    if author.strip():
        clauses.append("r.author = %(author)s")
        params["author"] = author.strip()
    if agent.strip():
        clauses.append("r.agent = %(agent)s")
        params["agent"] = agent.strip()
    if tags.strip():
        clauses.append("r.tags && %(tags)s::text[]")
        params["tags"] = canon.parse_tags(tags)
    if since.strip():
        clauses.append("r.create_time >= %(since)s")
        params["since"] = canon.parse_time(since, "since")
    if until.strip():
        clauses.append("r.create_time <= %(until)s")
        params["until"] = canon.parse_time(until, "until")

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rank_select = (
        "ts_rank(r.search_vec, websearch_to_tsquery('english', %(text)s))"
        if text.strip()
        else "NULL::real"
    )
    severity_order_sql = "array_position(ARRAY['info','warning','error','critical']::text[], "
    orders = {
        "recent": (
            "r.create_time DESC, r.report_id DESC",
            "i.create_time DESC, i.report_id DESC",
        ),
        "severity": (
            severity_order_sql + "r.severity) DESC, r.create_time DESC",
            severity_order_sql + "i.severity) DESC, i.create_time DESC",
        ),
        "relevance": ("rank DESC, r.create_time DESC", "s.rank DESC, i.create_time DESC"),
    }
    inner_order, outer_order = orders[order_by]

    # Two steps on purpose. report_index carries a per-row subquery for each
    # of corrected_by and commented_by, so it must not be scanned whole: the
    # inner query picks the ids off the plain table, and only those rows are
    # expanded through the view.
    query = _q(
        "WITH sel AS ("
        "  SELECT r.report_id, " + rank_select + " AS rank "
        "    FROM {s}.reports r"
        + where +
        "   ORDER BY " + inner_order +
        "   LIMIT %(limit)s"
        ") "
        "SELECT i.*, s.rank FROM sel s "
        "  JOIN {s}.report_index i ON i.report_id = s.report_id "
        " ORDER BY " + outer_order
    )

    with _read_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    out = [_row(r) for r in rows]
    if order_by != "relevance":
        for item in out:
            item.pop("rank", None)
    return out


@mcp.tool(name="get_report")
@_wrap
def get_report(
    report_id: int,
    include_body: bool = True,
    max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES,
) -> dict[str, Any]:
    """Read one report in full, with its references and its trail.

    Args:
        report_id: from list_reports
        include_body: pass false for the metadata, references and trail only --
              useful when you already know the body is large.
        max_inline_bytes: refuse to return a body larger than this (default
              100000). The error reports the actual size, so you can decide to
              raise the limit or re-call with include_body=false.

    Returns the report's own columns, `refs` (what it is about, each with
    ref_type/ref_key/role/label and any run or time range), and the trail:
      incoming_links -- reports pointing AT this one. A 'supersedes' here means
            a later report claims to correct this one; read both. This report
            is NOT hidden or edited by it.
      outgoing_links -- reports this one points at.
    """
    if report_id <= 0:
        raise ValueError(f"report_id must be a positive report id, got {report_id}")

    with _read_cursor() as cur:
        row = cur.execute(
            _q(
                "SELECT report_id, create_time, author, agent, triggered_by, severity, "
                "       title, summary, body_format, tags, attrs, "
                "       octet_length(body) AS body_size"
                + (", body " if include_body else " ") +
                "  FROM {s}.reports WHERE report_id = %s"
            ),
            (report_id,),
        ).fetchone()
        if row is None:
            raise ToolError(
                f"not_found: no report {report_id} (use list_reports to find the right id)"
            )
        if include_body and row["body_size"] > max_inline_bytes:
            raise ToolError(
                f"report {report_id} has a {row['body_size']} byte body, over "
                f"max_inline_bytes={max_inline_bytes}. Re-call with include_body=false for "
                "everything else, or raise max_inline_bytes if you really want it inline."
            )

        refs = cur.execute(
            _q(
                "SELECT ref_id, ref_type, ref_key, label, role, run_lo, run_hi, "
                "       time_lo, time_hi "
                "  FROM {s}.report_refs_full WHERE report_id = %s "
                " ORDER BY role, ref_type, ref_key"
            ),
            (report_id,),
        ).fetchall()

        incoming = cur.execute(
            _q(
                "SELECT l.role, l.from_report AS report_id, r.title, r.author, r.agent, "
                "       r.severity, r.create_time "
                "  FROM {s}.report_links l "
                "  JOIN {s}.reports r ON r.report_id = l.from_report "
                " WHERE l.to_report = %s ORDER BY r.create_time"
            ),
            (report_id,),
        ).fetchall()

        outgoing = cur.execute(
            _q(
                "SELECT l.role, l.to_report AS report_id, r.title, r.author, r.agent, "
                "       r.severity, r.create_time "
                "  FROM {s}.report_links l "
                "  JOIN {s}.reports r ON r.report_id = l.to_report "
                " WHERE l.from_report = %s ORDER BY r.create_time"
            ),
            (report_id,),
        ).fetchall()

    result = _row(row)
    result["refs"] = [_row(r) for r in refs]
    result["incoming_links"] = [_row(r) for r in incoming]
    result["outgoing_links"] = [_row(r) for r in outgoing]
    return result


@mcp.tool(name="list_refs")
@_wrap
def list_refs(
    ref_type: str = "",
    key_contains: str = "",
    run: int | None = None,
    time_from: str = "",
    time_to: str = "",
    order_by: str = "reports",
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Browse the subject index itself: which things have reports at all.

    The orientation call -- "which runs has anything been filed about", "which
    subsystems show up most" -- before drilling in with list_reports.

    Args:
        ref_type: restrict to one type (run, time, system, metric, alarm,
              dataset, file, document, url, report, mcp, free)
        key_contains: case-insensitive substring match on the key
        run: only run refs covering this run
        time_from, time_to: only time refs overlapping this window
        order_by: reports (default, most-reported first), recent (most recently
              reported), or key (type then key, alphabetical)
        limit: max rows (default 50, capped at 500)

    Each row: ref_id, ref_type, ref_key, label, run/time range, n_reports,
    last_report_time. A ref with n_reports = 0 exists only if a report was
    purged by an administrator.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    if run is not None and (time_from.strip() or time_to.strip()):
        raise ValueError(
            "run and time_from/time_to cannot be combined here -- one ref is either a "
            "run ref or a time ref, so asking for both matches nothing. Use "
            "list_reports(run=..., time_from=...) to find REPORTS carrying both."
        )
    orders = {
        "reports": "n_reports DESC, last_report_time DESC NULLS LAST",
        "recent": "last_report_time DESC NULLS LAST, n_reports DESC",
        "key": "f.ref_type, f.ref_key",
    }
    order_key = (order_by or "reports").strip().lower()
    if order_key not in orders:
        raise ValueError(f"order_by must be one of {', '.join(orders)} -- got {order_by!r}")

    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if ref_type.strip():
        clauses.append("f.ref_type = %(ref_type)s")
        params["ref_type"] = canon.choice(ref_type, REF_TYPES, "ref_type")
    if key_contains.strip():
        clauses.append("f.ref_key ILIKE %(key_like)s")
        params["key_like"] = f"%{key_contains.strip()}%"
    if run is not None:
        clauses.append("f.ref_type = 'run' AND %(run)s BETWEEN f.run_lo AND f.run_hi")
        params["run"] = run
    if time_to.strip():
        clauses.append("f.ref_type = 'time' AND f.time_lo <= %(time_to)s")
        params["time_to"] = canon.parse_time(time_to, "time_to")
    if time_from.strip():
        clauses.append("f.ref_type = 'time' AND f.time_hi >= %(time_from)s")
        params["time_from"] = canon.parse_time(time_from, "time_from")

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    query = _q(
        "SELECT f.ref_id, f.ref_type, f.ref_key, f.label, "
        "       f.run_lo, f.run_hi, f.time_lo, f.time_hi, "
        "       count(rr.report_id)  AS n_reports, "
        "       max(r.create_time)   AS last_report_time "
        "  FROM {s}.refs f "
        "  LEFT JOIN {s}.report_refs rr ON rr.ref_id = f.ref_id "
        "  LEFT JOIN {s}.reports r      ON r.report_id = rr.report_id"
        + where +
        " GROUP BY f.ref_id "
        " ORDER BY " + orders[order_key] +
        " LIMIT %(limit)s"
    )

    with _read_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agentlog-mcp", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("AGENTLOG_MCP_HOST", DEFAULT_HOST),
                        help="bind address (default: %(default)s)")
    # No default port, database host, or database name anywhere in this
    # package: those are deployment facts, and this repository is not the
    # place for them. They come from the systemd unit (rendered by
    # scripts/agentlog-mcp-install-unit.sh) or the environment.
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("AGENTLOG_MCP_PORT", "0") or 0),
                        help="bind port -- REQUIRED, via this flag or AGENTLOG_MCP_PORT")
    parser.add_argument("--db-host", default=_config.db_host,
                        help="Postgres host -- REQUIRED, via this flag or AGENTLOG_MCP_DB_HOST")
    parser.add_argument("--db-port", type=int, default=_config.db_port,
                        help="Postgres port -- REQUIRED, via this flag or AGENTLOG_MCP_DB_PORT")
    parser.add_argument("--db-name", default=_config.db_name,
                        help="Postgres database -- REQUIRED, via this flag or AGENTLOG_MCP_DB_NAME")
    parser.add_argument("--db-schema", default=_config.db_schema,
                        help="schema holding the agentlog tables (default: %(default)s)")
    parser.add_argument("--write-role", default=_config.write_role,
                        help="role assumed for the duration of a write transaction; "
                             "reads run as the login role (default: %(default)s)")
    parser.add_argument("--max-body-bytes", type=int, default=_config.max_body_bytes,
                        help="largest report body accepted (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help=(
                            "verify configuration and exit -- does not bind a socket. "
                            "Makes a real database connection, confirms the agentlog "
                            "schema is present and readable and that the write role can "
                            "be assumed, and requires MIKEY_KEYS_FILE to be set. Files "
                            "nothing: the write check is rolled back."
                        ))
    return parser.parse_args(argv)


def _apply_args(args: argparse.Namespace) -> None:
    _config.db_host = args.db_host
    _config.db_port = args.db_port
    _config.db_name = args.db_name
    _config.db_schema = args.db_schema
    _config.write_role = args.write_role
    _config.max_body_bytes = args.max_body_bytes
    _config.unset = [
        name
        for name, value in (
            ("--db-host / AGENTLOG_MCP_DB_HOST", args.db_host),
            ("--db-port / AGENTLOG_MCP_DB_PORT", args.db_port),
            ("--db-name / AGENTLOG_MCP_DB_NAME", args.db_name),
        )
        if not value
    ]


def _kerberos_hint() -> str:
    """Explain a GSSAPI failure in terms of the cache actually in use.

    The raw psycopg error for an expired ticket is several screens of
    connection-attempt detail whose one useful phrase ("Ticket expired") is
    buried in the middle -- and it never mentions KRB5CCNAME, which is usually
    the real problem: a `systemd --user` service does not inherit that
    variable, so it reads the DEFAULT cache while a renewal job keeps a
    different one fresh. Naming the cache turns a confusing failure into an
    obvious one.
    """
    ccname = os.environ.get("KRB5CCNAME")
    lines = [
        "",
        "This is a Kerberos problem, not a database problem.",
        f"  KRB5CCNAME: {ccname or '(unset -- using the default cache)'}",
    ]
    try:
        klist = subprocess.run(
            ["klist"] + (["-c", ccname] if ccname else []),
            capture_output=True, text=True, timeout=10,
        )
        detail = (klist.stdout or klist.stderr).strip().splitlines()
        lines += ["  klist:"] + [f"    {ln}" for ln in detail[:6]] if detail else []
    except Exception:  # noqa: BLE001 -- klist absent/hung is not worth failing over
        lines.append("  klist: could not be run")
    lines += [
        "",
        "  If a renewal job maintains a cache somewhere other than the default,",
        "  the service must be told where: pass --krb5-ccname to",
        "  agentlog-mcp-install-unit.sh (it renders Environment=KRB5CCNAME=...),",
        "  then restart. Note that a valid ticket in YOUR shell says nothing",
        "  about what this service can see.",
    ]
    return "\n".join(lines)


def _preflight() -> None:
    """Fail loudly on the things that make this server useless if wrong."""
    if _config.unset:
        raise RuntimeError(
            "no database endpoint configured: "
            + ", ".join(_config.unset)
            + ". This package deliberately ships no host, port or database name -- pass "
            "them on the command line or in the environment (the systemd unit rendered "
            "by agentlog-mcp-install-unit.sh is the usual place)."
        )
    if not mcp.settings.auth:
        raise RuntimeError(
            "MIKEY_KEYS_FILE is not set. agentlog-mcp requires mikey auth: the key's "
            "name is recorded as every report's author, which is the only accountability "
            "an append-only log has. Refusing to start without it."
        )

    # The read path and the write path have different privileges, so check
    # them separately -- either one being misconfigured breaks the server in a
    # way that would otherwise only show up on a live call.
    with _read_cursor() as cur:
        login_role = cur.execute("SELECT session_user AS r").fetchone()["r"]
        for relation in ("reports", "refs", "report_refs", "report_index"):
            found = cur.execute(
                "SELECT to_regclass(%s) AS t", (f"{_config.db_schema}.{relation}",)
            ).fetchone()
            if found["t"] is None:
                raise RuntimeError(
                    f"schema {_config.db_schema!r} has no {relation!r} -- has "
                    "sql/create_tables.sql been run against this database?"
                )
        # Actually read, rather than only confirming the relations exist:
        # to_regclass succeeds even with no SELECT privilege, so this is what
        # proves the login role's read grant is in place. The view is the
        # stricter test -- it needs SELECT on everything underneath it.
        try:
            cur.execute(_q("SELECT 1 FROM {s}.report_index LIMIT 1"))
        except psycopg.errors.InsufficientPrivilege as e:
            raise RuntimeError(
                f"login role {login_role!r} cannot SELECT from "
                f"{_config.db_schema}.report_index. Reads deliberately run unelevated, "
                f"so it needs the grant directly:\n"
                f"  GRANT USAGE ON SCHEMA {_config.db_schema} TO {login_role};\n"
                f"  GRANT SELECT ON ALL TABLES IN SCHEMA {_config.db_schema} TO {login_role};"
            ) from e

    # Confirm the write role can be assumed, in a transaction forced to roll
    # back -- so --check verifies the write path without filing anything. That
    # matters more here than in a store that can delete: a report written by a
    # health check would be permanent.
    try:
        with _get_pool().connection() as conn:
            with conn.transaction(force_rollback=True):
                conn.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(_config.write_role))
                )
    except psycopg.Error as e:
        raise RuntimeError(
            f"login role {login_role!r} cannot assume write role "
            f"{_config.write_role!r}: {e}"
        ) from e

    LOGGER.info(
        "database ok: %s:%s/%s schema=%s, login=%s, write_role=%s, gssencmode=%s",
        _config.db_host, _config.db_port, _config.db_name,
        _config.db_schema, login_role, _config.write_role, GSSENCMODE,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("AGENTLOG_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    _apply_args(args)

    try:
        _preflight()
        if not args.check and not args.port:
            raise RuntimeError(
                "no bind port configured: pass --port or set AGENTLOG_MCP_PORT. This "
                "package ships no default port."
            )
    except Exception as e:  # noqa: BLE001 -- report cleanly, never a traceback
        print(f"FAILED: {e}", file=sys.stderr)
        # A GSSAPI/ticket failure is almost always a credential-cache
        # visibility problem rather than anything about the database, and the
        # raw psycopg text does not say so. Add the diagnosis.
        if any(k in str(e) for k in ("GSSAPI", "Kerberos", "Ticket expired", "gss")):
            print(_kerberos_hint(), file=sys.stderr)
        sys.exit(1)

    if args.check:
        print(
            f"OK: agentlog_mcp constructed (host={args.host} port={args.port or '(unset)'} "
            f"db={_config.db_host}:{_config.db_port}/{_config.db_name} "
            f"schema={_config.db_schema} write_role={_config.write_role} auth=mikey)"
        )
        return

    LOGGER.info(
        "Starting agentlog MCP server over streamable-http on %s:%s (db=%s:%s/%s, auth=mikey)",
        args.host, args.port, _config.db_host, _config.db_port, _config.db_name,
    )
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
