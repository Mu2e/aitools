"""Read-only MCP server exposing Mu2e DQM metrics to a model.

All database access lives in `dqm.reader`, part of the `mu2e-dqm` distribution
in the Mu2e/DQM repository, shared with the display applications and with bulk
analysis work. This module is only the narrow window onto it: it caps every
result at something that fits in a context window, converts frames to JSON,
and tells the model when a result was cut short and what it can ask for
instead.

That division is the point. The reader is built to pull the whole table -- a
million rows as a typed DataFrame -- while nothing here should ever return
more than a few hundred rows. When a query matches more than that, the answer
is not to page through it but to ask for `summarize=True` or `downsample_to`,
which reduce the data in pandas and return the shape rather than the rows.
"""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import logging
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

LOGGER = logging.getLogger("dqm_mcp")


def _import_reader():
    """Import the dqm reader, honouring a checkout override.

    Normally `mu2e-dqm` is simply installed in this venv -- it is a pinned git
    dependency in pyproject.toml, so `uv pip install` brings it along. Setting
    DQM_READER_PATH to a DQM/python directory takes precedence over that, for
    developing the reader and this server together against a working tree
    without reinstalling between edits. It has to be an environment variable
    rather than a CLI flag: the import happens at module load, before argparse
    ever runs.
    """
    override = os.environ.get("DQM_READER_PATH")
    if override and override not in sys.path:
        # Ahead of site-packages, so the checkout wins over any installed copy.
        sys.path.insert(0, override)
    try:
        import dqm
        from dqm import reader as dqm_reader
    except ImportError as exc:  # pragma: no cover - deployment error path
        raise ImportError(
            "cannot import dqm.reader, which holds every database query this "
            "server makes. It should have been installed as a dependency "
            "(mu2e-dqm @ git+https://github.com/Mu2e/DQM#subdirectory=python); "
            "install it into this venv, or set DQM_READER_PATH to a DQM "
            f"checkout's python/ directory. (import failed: {exc})"
        ) from exc
    return dqm, dqm_reader


dqm, dqm_reader = _import_reader()
DqmReader = dqm_reader.DqmReader
to_records = dqm_reader.to_records
CODE_VALUES = dqm_reader.CODE_VALUES
ALARM_VALUES = dqm_reader.ALARM_VALUES


def _server_version() -> str:
    """Resolved from the installed package's metadata -- itself derived from
    the nearest git tag by setuptools_scm at build/install time (see
    pyproject.toml). Falls back gracefully for a raw checkout run without an
    install (e.g. `python -m dqm_mcp.server` against source directly)."""
    try:
        return importlib.metadata.version("dqm-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


# Sized for a context window, not for the database. A few hundred rows of JSON
# is already several thousand tokens, so MAX_LIMIT is a hard ceiling no
# argument can raise -- a caller that wants more than this wants the reader
# directly, or wants summarize/downsample_to instead.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
DEFAULT_RECENT_DAYS = 10
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001

READ_ONLY_INSTRUCTIONS = (
    "Read-only MCP server for Mu2e DQM metrics, backed by the shared dqm.reader "
    "library over the Query Engine. Returns structured JSON rows from "
    "dqm.sources, dqm.values, dqm.intervals, dqm.numbers, dqm.limits, "
    "dqm.files, dqm.golden, dqm.alarms and dqm.alarm_causes.\n\n"
    "DATA MODEL (9 tables: 3 catalogs, 2 measurement tables, 2 file tables, "
    "2 alarm tables):\n"
    "- dqm.sources (sid): one monitored source, uniquely identified by "
    "(process, stream, aggregation, version) -- e.g. process='valNightly', "
    "stream='reco', aggregation='day'. list_sources / list_versions read this.\n"
    "- dqm.values (vid): the catalog of named metrics that CAN be tracked, uniquely "
    "identified by (groupx, subgroup, namex) -- groupx is typically a detector "
    "subsystem (e.g. 'cal', 'trk', 'crv'), subgroup a processing/reconstruction stage "
    "(e.g. 'digi', 'track', 'cluster'), namex the specific statistic (e.g. 'meanEnergy', "
    "'rmsEnergy'). This catalog is source-independent: it doesn't say which sources "
    "actually report a given vid, only that the name exists. list_values reads this.\n"
    "- dqm.intervals (iid): one time/run bucket FOR ONE SOURCE (sid is a foreign key "
    "here) -- a run/subrun range and/or a start/end timestamp range. A 'day' "
    "aggregation source gets roughly one interval per calendar day; a 'file' "
    "aggregation source gets one per file. list_intervals reads this.\n"
    "- dqm.numbers (nid): the actual measurement -- one row per (sid, vid, iid) "
    "triple, holding valuex (the measured value), sigma (uncertainty; 0.0 means not "
    "applicable/not computed, not literally zero error), and code -- a FIXED enum "
    "(DqmNumber::codeValues in Mu2e/DQM's DQM/inc/DqmNumber.hh, not source-defined): "
    "OK=0, lowStats=1, missing=2, error=3.\n"
    "- dqm.limits (lid): the acceptable-range definition for that SAME (sid, vid, iid) "
    "triple. Structurally it mirrors dqm.numbers; the only difference is payload: "
    "llimit/ulimit (the bounds the metric was expected to fall within) and alarmcode "
    "instead of valuex and code. alarmcode is also a fixed enum (DqmLimit::alarmValues "
    "in DQM/inc/DqmLimit.hh): ignore=0, warnOnly=1, standard=2, critical=3. To check "
    "whether a measurement was in-spec, look up the same (sid, vid, iid) in dqm.limits "
    "-- but the two tables are populated independently with no foreign key between "
    "them, so a numbers row and a limits row for the same key are NOT guaranteed to "
    "both exist. An interval can legitimately hold limits and no numbers.\n\n"
    "query_metrics(metric_table=...) queries dqm.numbers (default) or dqm.limits -- "
    "the same sid/vid/interval filtering applies to both, since they share a key shape.\n\n"
    "- dqm.files (fid): the file one source's metrics for one interval were "
    "derived from -- the back pointer from a timeline point to the histogram "
    "file that produced it. Keyed by (sid, iid), and a pair may have SEVERAL "
    "files. Its interval is the one the metrics were MEASURED in. The usual "
    "question is 'this metric looks wrong at this (sid, iid), which file do I "
    "open?' -- list_files, which also takes a run or a time and resolves the "
    "interval itself.\n"
    "- dqm.golden (gid): the reference file a source's metrics are to be "
    "compared against. Its interval is one of VALIDITY, exactly like a "
    "dqm.limits row's and unlike everything else here: a row declares a "
    "reference from some run or time onwards until a newer declaration "
    "supersedes it, so several rows per source are normal and only one applies "
    "at a given point -- of the covering rows, the most recently committed "
    "(highest gid). Keyed by source and range only: one reference covers every "
    "metric the source reports, so there is no vid. Ask get_golden_file for "
    "the one in force at a run or time (it does that resolution); ask "
    "list_golden for the declaration history. Reading a covering row off a "
    "listing is wrong whenever a reference has been replaced.\n"
    "- The two file tables point in OPPOSITE directions and are easy to "
    "conflate: dqm.files is provenance (where a number came from), dqm.golden "
    "is validity (what a number should be judged against). Neither file is "
    "fetched or parsed by this server; both tables record names as committed "
    "and do not check that the file exists.\n"
    "- A NAME IS NOT A PATH. These tables hold file names; the metacat MCP "
    "turns one into a path. Production DQM names follow the Mu2e convention, "
    "so dropping the sequencer field gives the dataset to ask metacat for, "
    "with include_url=true and no location= so it probes (disk is where these "
    "normally are, but probed beats presumed). Metacat's run filters do not "
    "work on DQM datasets -- they carry no run metadata -- so resolve run to "
    "file here and name to path there. A hand-made golden file may be in no "
    "catalog at all.\n\n"
    "THE NATURAL QUERY is one sid + one vid: that returns a timeline of one variable "
    "across intervals, suitable for trending. A bare sid with no vid mixes every "
    "variable that source tracks into one pile, on unrelated scales -- rarely what you "
    "want. Typical flow: list_sources to find sid, list_values to find vid, then "
    "query_metrics(sid=..., vid=...).\n\n"
    "SIZE. This server is a limited window onto a database holding hundreds of "
    "thousands of measurements, heading for millions. Results are capped at "
    f"{MAX_LIMIT} rows. Every response carries counts.truncated; when it is true, "
    "- dqm.alarms (aid): one alarm EPISODE, identified by "
    "(algo, config, subject, sid) and deliberately NOT by interval. A condition "
    "seen again extends the existing episode, so a single row can span many runs "
    "and many days; its run/time range is a hull and does not claim every run "
    "inside it fired. Two independent lifecycles: `open` (is it still being "
    "reported, maintained by the evaluators) and `status` (what a human decided "
    "-- active, acknowledged, silenced, retired, deleted). These are orthogonal: "
    "an acknowledged problem that is still occurring stays open, and an episode "
    "nobody has looked at stays status='active'. A terminal status also closes "
    "the episode; closing one does not change its status. list_alarms reads "
    "this.\n"
    "- dqm.alarm_causes (cid): the evidence for an episode -- which interval "
    "(iid) and which metric (vid) was seen, at what level. A NULL vid is a "
    "global test, about the interval rather than one metric. This is the ground "
    "truth for what actually fired, as against the episode's hull. "
    "get_alarm_causes reads this.\n"
    "- LEVELS: an alarm or cause `level` is 0-3 = message, warn, alarm, severe. "
    "This is NOT the `alarmcode` on a dqm.limits row (0-3 = ignore, warnOnly, "
    "standard, critical). Both are small integers and they mean different "
    "things; each tool returns the enum that applies to its own rows.\n\n"
    "counts.total_rows gives the exact number of matching rows, so report "
    "'showing N of TOTAL' rather than implying the result is complete. Do NOT try to "
    "page through a large result -- use query_metrics(summarize=True) for statistics "
    "and range, or downsample_to=N for a timeline reduced to about N points that "
    "keeps its shape. For genuine bulk work (machine learning, retrospective analysis "
    "over the whole table) this server is the wrong tool: import dqm.reader from the "
    "Mu2e/DQM repository and query it directly.\n\n"
    "An empty result with truncated=false is authoritative -- there really is no "
    "matching data. Filters are pushed into the database, so an empty result is not "
    "an artifact of a scan window."
)

mcp = MCPServer("dqm", instructions=READ_ONLY_INSTRUCTIONS)

# Set once by main() from CLI args (env vars as fallback defaults) before
# mcp.run(); read per-call by the tools below. The default here matches the
# argparse default so importing this module without calling main() still gets
# a working reader.
_state: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "reader": None,
}


def _reader():
    reader = _state.get("reader")
    if reader is None:
        reader = DqmReader()
        _state["reader"] = reader
    return reader


def _wrap(fn):
    """Turn an exception into a ToolError carrying a readable message.

    Without this the SDK reports any non-ToolError exception to the caller as
    a bare "Error executing tool <name>" and keeps the text to itself, so
    every guard in this module -- "a source is required", "11 sources match:
    sids [...]", "bump the mu2e-dqm pin" -- arrived as that one useless
    sentence. The messages are the whole point of those guards: they tell an
    agent what to pass instead. ToolError is the SDK's channel for an
    anticipated failure and its message does reach the caller, which is why
    memory-mcp and agentlog-mcp both do this.

    ValueError is the bad-argument case and is reported as such. Anything
    else is logged here with a traceback, since the caller gets only one
    line and an unanticipated failure is worth having server-side.
    """

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except ValueError as e:
            raise ToolError(f"bad_request: {e}") from e
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            LOGGER.exception("dqm-mcp tool %s failed", fn.__name__)
            raise ToolError(f"tool_failed: {type(e).__name__}: {e}") from e

    return inner


def _clamp_limit(limit: int) -> int:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    return min(limit, MAX_LIMIT)


def _page(df, limit: int, offset: int):
    """Apply an offset to an already-limited frame.

    The reader has no offset concept -- it is a paging idea, and paging is what
    this server tells callers not to do -- so offset is applied here, over a
    fetch widened to cover it.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if offset:
        df = df.iloc[offset : offset + limit]
    elif len(df) > limit:
        df = df.iloc[:limit]
    return df


@mcp.tool(description="Get DQM MCP configuration and defaults.")
@_wrap
def get_server_info() -> dict[str, Any]:
    reader = _reader()
    return {
        "name": "dqm",
        "version": _server_version(),
        "read_only": True,
        "transport": "streamable-http",
        "backend": {
            "library": "dqm.reader",
            "version": getattr(dqm, "__version__", "unknown"),
            "module": getattr(dqm_reader, "__file__", None),
            "base_url": reader.base_url,
            "dbname": reader.dbname,
            "timeout_seconds": reader.timeout_seconds,
            "nocache_required": True,
        },
        "defaults": {
            "query_limit": DEFAULT_LIMIT,
            "max_limit": MAX_LIMIT,
            "recent_days": DEFAULT_RECENT_DAYS,
        },
        "enums": {"code": CODE_VALUES, "alarmcode": ALARM_VALUES},
    }


@mcp.tool(
    description=(
        "List DQM metric sources from dqm.sources. Each row is one monitored source, "
        "keyed by (process, stream, aggregation, version); the returned sid is the id "
        "used to scope list_intervals and query_metrics to one source."
    )
)
@_wrap
def list_sources(
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    df = _reader().sources(
        process=process,
        stream=stream,
        aggregation=aggregation,
        version=version,
        limit=limit + offset,
    )
    truncated = bool(df.attrs.get("truncated"))
    paged = _page(df, limit, offset)
    return {
        "filters": {
            "process": process,
            "stream": stream,
            "aggregation": aggregation,
            "version": version,
            "limit": limit,
            "offset": offset,
        },
        "returned": len(paged),
        "truncated": truncated,
        "results": to_records(paged),
    }


@mcp.tool(
    description=(
        "List unique source versions, optionally filtered by process/stream/aggregation. "
        "version is one of the four fields that identify a dqm.sources row (alongside "
        "process/stream/aggregation) -- use this to see which code versions have "
        "reported for a given process/stream before narrowing list_sources/query_metrics."
    )
)
@_wrap
def list_versions(
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    df = _reader().sources(
        process=process, stream=stream, aggregation=aggregation, limit=None
    )
    versions = sorted({str(v) for v in df["version"].dropna().tolist()})
    return {
        "filters": {"process": process, "stream": stream, "aggregation": aggregation},
        "version_count": len(versions),
        "versions": versions[:limit],
        "sources_examined": int(len(df)),
    }


@mcp.tool(
    description=(
        "List DQM value names from dqm.values -- the catalog of metrics that can be "
        "tracked, keyed by (groupx, subgroup, namex). This catalog is source-independent "
        "(it doesn't say which sources report a given metric); the returned vid is the "
        "id used to scope query_metrics to one variable. Use this to find the vid for a "
        "metric you already know the name of, or to browse what's trackable at all."
    )
)
@_wrap
def list_values(
    groupx: str | None = None,
    subgroup: str | None = None,
    namex: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    df = _reader().values(
        groupx=groupx, subgroup=subgroup, namex=namex, limit=limit + offset
    )
    truncated = bool(df.attrs.get("truncated"))
    paged = _page(df, limit, offset)
    return {
        "filters": {
            "groupx": groupx,
            "subgroup": subgroup,
            "namex": namex,
            "limit": limit,
            "offset": offset,
        },
        "returned": len(paged),
        "truncated": truncated,
        "results": to_records(paged),
    }


@mcp.tool(
    description=(
        "List DQM intervals with run/subrun or time filters. Each interval belongs to "
        "one source (sid) and represents one time/run bucket for it (e.g. roughly one "
        "per day for a 'day' aggregation source); the returned iid is the third key "
        "(alongside sid, vid) that dqm.numbers/dqm.limits rows attach to. Pass sid to "
        "scope to one source -- unscoped, this lists intervals across all sources."
    )
)
@_wrap
def list_intervals(
    sid: int | None = None,
    run: int | None = None,
    subrun: int | None = None,
    start_time_after_iso_utc: str | None = None,
    end_time_before_iso_utc: str | None = None,
    recent_days: float | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    newest_first: bool = True,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    df = _reader().intervals(
        sid=sid,
        run=run,
        subrun=subrun,
        start_time_after=start_time_after_iso_utc,
        end_time_before=end_time_before_iso_utc,
        recent_days=recent_days,
        limit=limit + offset,
        newest_first=newest_first,
    )
    truncated = bool(df.attrs.get("truncated"))
    paged = _page(df, limit, offset)
    return {
        "filters": {
            "sid": sid,
            "run": run,
            "subrun": subrun,
            "start_time_after_iso_utc": start_time_after_iso_utc,
            "end_time_before_iso_utc": end_time_before_iso_utc,
            "recent_days": recent_days,
            "limit": limit,
            "offset": offset,
            "newest_first": newest_first,
        },
        "returned": len(paged),
        "truncated": truncated,
        "results": to_records(paged),
    }


@mcp.tool(
    description=(
        "Query DQM metrics from dqm.numbers (default) or dqm.limits. "
        "metric_table='limits' has the identical (sid, vid, iid) key shape as 'numbers' "
        "-- it's the acceptable llimit/ulimit range + alarmcode for the same "
        "metric/interval, not a separate concept -- so the same filters apply either "
        "way; call it twice with the same sid/vid to compare a measured valuex against "
        "its threshold, keeping in mind a numbers row and a limits row for the same key "
        "are not guaranteed to both exist. "
        "Defaults to recent_days=10 and limit=100. "
        "The typical call is sid + vid together: a timeline of one named variable for "
        "one source across intervals. sid alone returns every variable that source "
        "tracks interleaved, on unrelated scales -- use list_values to find the vid first. "
        "When a result comes back truncated, counts.total_rows is the exact number of "
        "matching rows: say 'showing N of TOTAL'. Do not page through a large result -- "
        "pass summarize=true for count/mean/min/max/range and code tallies, or "
        "downsample_to=N for a timeline reduced to about N points (each carrying the "
        "bin's mean, min and max, so excursions survive the reduction)."
    )
)
@_wrap
def query_metrics(
    metric_table: str = "numbers",
    sid: int | None = None,
    vid: int | None = None,
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    groupx: str | None = None,
    subgroup: str | None = None,
    namex: str | None = None,
    run: int | None = None,
    subrun: int | None = None,
    start_time_after_iso_utc: str | None = None,
    end_time_before_iso_utc: str | None = None,
    recent_days: float | None = DEFAULT_RECENT_DAYS,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    expand_ids: bool = True,
    summarize: bool = False,
    downsample_to: int = 0,
) -> dict[str, Any]:
    reader = _reader()
    limit = _clamp_limit(limit)

    filters = dict(
        sid=sid,
        vid=vid,
        process=process,
        stream=stream,
        aggregation=aggregation,
        version=version,
        groupx=groupx,
        subgroup=subgroup,
        namex=namex,
        run=run,
        subrun=subrun,
        start_time_after=start_time_after_iso_utc,
        end_time_before=end_time_before_iso_utc,
        recent_days=recent_days,
    )

    # summarize and downsample_to describe the whole matching set, so they read
    # past the display limit -- reducing only the first 100 rows would answer a
    # different question than the one asked.
    reducing = summarize or downsample_to > 0
    fetch_limit = None if reducing else limit + offset

    df = reader.metrics(
        table=metric_table,
        limit=fetch_limit,
        decorate=expand_ids,
        **filters,
    )

    truncated = bool(df.attrs.get("truncated"))
    matched = int(len(df))

    response: dict[str, Any] = {
        "metric_table": metric_table,
        "filters": {
            **{k: v for k, v in filters.items()},
            "limit": limit,
            "offset": offset,
            "expand_ids": expand_ids,
            "summarize": summarize,
            "downsample_to": downsample_to,
        },
    }

    if summarize:
        response["summary"] = reader.summarize(df)
        response["counts"] = {"matched_rows": matched, "truncated": truncated}
        return response

    if downsample_to > 0:
        reduced = reader.downsample(df, n=downsample_to)
        response["counts"] = {
            "matched_rows": matched,
            "returned_rows": int(len(reduced)),
            "truncated": truncated,
            "downsampled": matched > len(reduced),
        }
        response["results"] = to_records(reduced)
        return response

    paged = _page(df, limit, offset)
    counts: dict[str, Any] = {
        "returned_rows": int(len(paged)),
        "truncated": truncated,
    }
    if truncated:
        # Only worth the extra scan when the answer would otherwise be
        # unquantified: an exact total is what lets the caller say "100 of
        # 41,203" instead of "100, and an unknown number more".
        try:
            counts["total_rows"] = reader.count_metrics(table=metric_table, **filters)
        except Exception as exc:  # pragma: no cover - counting is best effort
            LOGGER.warning("count_metrics failed: %s", exc)
            counts["total_rows"] = None
    else:
        counts["total_rows"] = matched

    response["counts"] = counts
    response["results"] = to_records(paged)

    if truncated:
        response["warnings"] = [
            f"Result capped at limit={limit}. counts.total_rows is the exact number of "
            "matching rows -- report 'showing N of that total', not as a complete "
            "result. Rather than paging, use summarize=true or downsample_to=N."
        ]
    return response


# --------------------------------------------------------- files and golden
#
# Two tables that both associate a source with a file, in opposite
# directions, and keeping them straight is the whole difficulty here:
#
#   dqm.files  is PROVENANCE. Its interval is the one the metrics were
#              MEASURED in, so a row says "these numbers came out of that
#              file". Looking up (sid, iid) gives the file behind a point.
#   dqm.golden is VALIDITY, exactly like dqm.limits. Its interval is one of
#              validity, so a row says "from here on, until something newer
#              says otherwise, this is the reference". Reading any covering
#              row is wrong when a reference has been replaced -- of the rows
#              covering a position, the one in force is the most recently
#              committed, i.e. the highest gid.
#
# That is why the golden lookup is a resolution (get_golden_file) and the
# files lookup is a filter (list_files): same shape of row, opposite meaning.


def _require_file_support(reader) -> None:
    """Fail with the fix rather than an AttributeError.

    dqm.files and dqm.golden reached the reader after the mu2e-dqm version
    this server pins (see pyproject.toml), so a venv built before that pin
    moved has a reader with no files()/golden() at all. Checking once, here,
    turns "AttributeError: 'DqmReader' object has no attribute 'files'" into
    the sentence that tells an operator what to do.
    """
    missing = [name for name in ("files", "golden") if not hasattr(reader, name)]
    if missing:
        raise RuntimeError(
            "the installed dqm.reader has no "
            + "/".join(f"{m}()" for m in missing)
            + ", so it predates dqm.files and dqm.golden. Bump the mu2e-dqm pin "
            "in this package's pyproject.toml to a DQM tag that includes them and "
            "reinstall, or point DQM_READER_PATH at a checkout that has them."
        )


def _one_source(reader, sid, process, stream, aggregation, version) -> int:
    """Resolve the arguments naming a source to exactly one sid.

    Files and golden declarations are per source, and both of the queries
    below are bounded only by that: without a single source, "every file" is
    a whole-table pull whose result would be capped at a context-window's
    worth of rows and silently misreported. So an ambiguous name is an error
    naming the candidates, not a guess at which one was meant.
    """
    named = {k: v for k, v in (
        ("sid", sid), ("process", process), ("stream", stream),
        ("aggregation", aggregation), ("version", version),
    ) if v is not None}
    if not named:
        raise ValueError(
            "a source is required: pass sid, or process/stream/aggregation/"
            "version naming exactly one. Files and golden declarations belong "
            "to one source, and listing every source's would be capped at a "
            "context window's worth of rows and so misreported. (list_files "
            "also takes filename= on its own, for the reverse lookup.)"
        )
    sids = reader.resolve_sids(
        sid=sid, process=process, stream=stream,
        aggregation=aggregation, version=version,
    )
    if not sids:
        raise ValueError(
            f"no source matches {named} -- use list_sources to see what "
            "exists. Note this is a miss in dqm.sources, before any file was "
            "looked for."
        )
    if len(sids) > 1:
        raise ValueError(
            f"{len(sids)} sources match: sids {sorted(sids)[:20]}. Files and "
            "golden declarations belong to one source, so narrow to a single "
            "one (pass sid=, or add aggregation/version) -- use list_sources "
            "to compare the candidates."
        )
    return int(sids[0])


def _source_exists(reader, sid: int) -> bool:
    """Whether dqm.sources actually holds this sid.

    Consulted only when a result came back empty. A bare sid is not validated
    on the way in -- nothing in dqm.files or dqm.golden joins back to prove it
    -- so without this, sid=999999 and a real source with nothing recorded
    both return the same empty list. This server tells callers that an empty
    result is authoritative, which obliges it to distinguish "no such source"
    from "nothing recorded for it".
    """
    try:
        return not reader.sources(sid=int(sid), limit=None).empty
    except Exception:  # pragma: no cover - verification is best effort
        LOGGER.debug("could not verify sid %s", sid, exc_info=True)
        # Assume it exists: claiming a miss we could not confirm would be the
        # worse of the two errors.
        return True


_NO_SUCH_SOURCE = (
    "no source with sid={sid} exists in dqm.sources, so this empty result is "
    "about the sid, not about what has been recorded -- check it with "
    "list_sources."
)


def _decode_source(reader, df):
    """Add a readable process/stream/aggregation/version to rows carrying sid.

    Same decoration list_alarms does, for the same reason: a bare sid tells a
    reader nothing, and these rows are meant to be shown to a person.
    """
    try:
        sids = [int(x) for x in df["sid"].dropna().unique()]
        if not sids:
            return df
        src = reader.sources(sid=sids, limit=None)
        src["source"] = (src["process"].astype(str) + "/"
                         + src["stream"].astype(str) + "/"
                         + src["aggregation"].astype(str) + "/"
                         + src["version"].astype(str))
        return df.merge(src[["sid", "source"]], on="sid", how="left")
    except Exception:  # pragma: no cover - decoration is a convenience
        LOGGER.debug("could not decode sids", exc_info=True)
        return df


@mcp.tool(
    description=(
        "The files a source's metrics came from, from dqm.files -- the back "
        "pointer from a timeline point to the histogram file that produced it. "
        "THE USUAL CALL: an anomalous metric is at some (sid, iid), so pass "
        "that sid and iid to get the file to go and look at. If you have a run "
        "or a time instead of an iid, pass run= or the time filters and the "
        "interval is resolved for you; the resolved iids come back in "
        "`resolved`. "
        "A run can legitimately map to MANY files: a 'file' aggregation source "
        "gets one interval per file, so a single run of such a source may have "
        "a hundred-odd rows here (one real case: 128). Add subrun= to isolate "
        "one, and do not report the first row as 'the' file for a run. Run "
        "matching is interval containment, so a source's catch-all interval (a "
        "wide range kept to hold limits or a golden declaration) is resolved "
        "too, though it contributes no files. "
        "A single (sid, iid) pair may also have several files -- one row each, "
        "all of which fed that point's metrics. "
        "This is provenance, not validity: the interval here is the one the "
        "metrics were measured in. That is the opposite direction from "
        "dqm.golden, where the interval says over what range a reference "
        "applies -- do not read a row from one as if it came from the other. "
        "Scoping: exactly one source is required, since files are per source "
        "and an unbounded listing would be capped and misleading -- except "
        "when filename= is given, which is the reverse lookup (which timeline "
        "point produced this file) and needs no source. filename is an exact "
        "match: the Query Engine has no LIKE, so there is no substring search. "
        "An empty result distinguishes two cases in `note`: no interval covered "
        "the position, versus intervals that have no file recorded. "
        "TO TURN A NAME INTO A PATH, use the metacat MCP, not a guess: a "
        "production DQM file name follows the Mu2e convention "
        "tier.owner.description.configuration.sequencer.format, and dropping "
        "the sequencer gives its dataset "
        "('dqm.mu2e.cosmics_crv.crvreco-006-001-file.125873_000636-001.root' is "
        "in 'mu2e:dqm.mu2e.cosmics_crv.crvreco-006-001-file.root'). Ask "
        "query_dataset_files for that dataset with include_url=true and let it "
        "resolve the location -- do NOT pass location= or assemble a /pnfs path "
        "by hand. These files are normally on disk, but metacat probes rather "
        "than assumes, which is the difference between a path that exists and "
        "one that looks right. Note metacat's run filters do not work on these "
        "datasets (DQM files carry no run metadata), so go run -> file HERE and "
        "name -> path there."
    )
)
@_wrap
def list_files(
    sid: int | None = None,
    iid: int | None = None,
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    run: int | None = None,
    subrun: int | None = None,
    start_time_after_iso_utc: str | None = None,
    end_time_before_iso_utc: str | None = None,
    recent_days: float | None = None,
    filename: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    reader = _reader()
    _require_file_support(reader)
    limit = _clamp_limit(limit)

    filters = {
        "sid": sid, "iid": iid, "process": process, "stream": stream,
        "aggregation": aggregation, "version": version, "run": run,
        "subrun": subrun,
        "start_time_after_iso_utc": start_time_after_iso_utc,
        "end_time_before_iso_utc": end_time_before_iso_utc,
        "recent_days": recent_days, "filename": filename,
        "limit": limit, "offset": offset,
    }

    # The reverse lookup: a file name is specific enough to need no source,
    # and equality is the one text comparison the Query Engine can push down.
    if filename is not None:
        from dqm.reader.transport import where_eq

        df = reader.read_table(
            "files", clauses=[where_eq("filename", filename)], limit=None
        )
        if not df.empty:
            df = reader.join_intervals(df)
            df = _decode_source(reader, df)
        total = int(len(df))
        paged = _page(df, limit, offset)
        response: dict[str, Any] = {
            "filters": filters,
            "counts": {"total_rows": total},
            "returned": int(len(paged)),
            "truncated": total > len(paged) + offset,
            "results": to_records(paged),
        }
        if total == 0:
            response["note"] = (
                f"no dqm.files row names {filename!r}. The match is exact and "
                "the name is stored as it was committed, so a differing path "
                "prefix or a basename alone will not match."
            )
        return response

    one_sid = _one_source(reader, sid, process, stream, aggregation, version)

    # Resolve a position to intervals first, so the file query is bounded by
    # iid rather than scanning a source's whole history. Which intervals were
    # used is reported, because "no file" and "no interval" are different
    # answers and the caller cannot otherwise tell them apart.
    iids: list[int] | None = None
    positional = any(v is not None for v in (
        run, subrun, start_time_after_iso_utc, end_time_before_iso_utc,
        recent_days,
    ))
    if iid is not None:
        iids = [int(iid)]
    elif positional:
        ivs = reader.intervals(
            sid=one_sid, run=run, subrun=subrun,
            start_time_after=start_time_after_iso_utc,
            end_time_before=end_time_before_iso_utc,
            recent_days=recent_days, limit=None,
        )
        iids = [int(v) for v in ivs["iid"].dropna().tolist()]
        if not iids:
            return {
                "filters": filters,
                "resolved": {"sid": one_sid, "iids": []},
                "counts": {"total_rows": 0},
                "returned": 0,
                "truncated": False,
                "results": [],
                "note": (
                    _NO_SUCH_SOURCE.format(sid=one_sid)
                    if not _source_exists(reader, one_sid) else
                    "no interval of this source covers that position, so there "
                    "is nothing to find a file for. The miss is in "
                    "dqm.intervals, not dqm.files -- check the run or time with "
                    "list_intervals."
                ),
            }

    df = reader.files(sid=one_sid, iid=iids, with_intervals=True, limit=None)
    if not df.empty:
        df = _decode_source(reader, df)
    total = int(len(df))
    paged = _page(df, limit, offset)

    response = {
        "filters": filters,
        "resolved": {"sid": one_sid, "iids": iids},
        "counts": {"total_rows": total},
        "returned": int(len(paged)),
        "truncated": total > len(paged) + offset,
        "results": to_records(paged),
    }
    notes: list[str] = []
    if total == 0:
        notes.append(
            _NO_SUCH_SOURCE.format(sid=one_sid)
            if not _source_exists(reader, one_sid) else
            "no file is recorded against "
            + ("that interval" if iids else "this source")
            + ". Files are committed by whatever produced the metrics, so an "
            "absence means it was not recorded -- not that the metrics have no "
            "origin."
        )
    elif iids is not None and len(iids) == 1 and total > 1:
        notes.append(
            f"{total} files for this one interval -- all of them contributed to "
            "its metrics; there is no 'primary' one."
        )
    elif positional and total > 1:
        # Seen on a real 'file' aggregation source: run 125873 has 128 files,
        # one interval each. Reporting that as "the file behind the run" would
        # be wrong, and the way to narrow it is not obvious from the schema.
        notes.append(
            f"{total} files match that position. A 'file' aggregation source "
            "gets one interval -- and so at least one file -- per file, so a "
            "run with many files has many rows here. Pass subrun= to isolate "
            "one, or read them as the set of files that run produced."
        )
    if positional and iids is not None and len(iids) > max(total, 1):
        notes.append(
            "Some matched intervals hold no file: a run matches an interval by "
            "containment, so a source's catch-all interval (a wide run range "
            "kept to hold limits or a golden declaration) is counted in "
            "resolved.iids while contributing no rows."
        )
    if notes:
        response["note"] = " ".join(notes)
    return response


@mcp.tool(
    description=(
        "The golden (reference) file in force for one source at one run or "
        "time, from dqm.golden. THE USUAL CALL for 'what should this run be "
        "compared against'. "
        "A golden row's interval is one of VALIDITY, exactly as a dqm.limits "
        "row's is: it declares a reference from some run or time onwards until "
        "a newer declaration supersedes it. So several rows per source are "
        "normal, and reading any row whose range covers a point is WRONG once a "
        "reference has been replaced -- of the covering rows, the one in force "
        "is the most recently committed (highest gid). That resolution is what "
        "this tool does, which is why it is a lookup and not a filter; "
        "list_golden shows the whole declaration history. "
        "One row comes back rather than a bare name, so you can see which "
        "declaration it came from, over what range, and its note (how a "
        "hand-made reference was built). Any covering rows it replaced are "
        "returned in `superseded`. "
        "A position is required: run (with optional subrun) or time_iso_utc. "
        "golden=null is a normal answer -- most sources have no reference "
        "declared, and one that does may have declared it only from some run "
        "onwards. The file name is recorded as given and is NOT checked to "
        "exist; it may be one of the source's own files (see list_files) or "
        "something made by hand, an average say. "
        "That distinction decides how to find the file: a name following the "
        "Mu2e convention (tier.owner.description.configuration.sequencer.format) "
        "is a catalogued production file, so drop the sequencer for its dataset "
        "and ask the metacat MCP's query_dataset_files with include_url=true, "
        "letting it resolve the location rather than passing location= or "
        "building a /pnfs path by hand. A hand-made reference may be in no "
        "catalog at all -- golden names are committed where the file is often "
        "not even visible -- so a metacat miss on one of those means 'not "
        "catalogued', not 'wrong name'."
    )
)
@_wrap
def get_golden_file(
    sid: int | None = None,
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    run: int | None = None,
    subrun: int | None = None,
    time_iso_utc: str | None = None,
) -> dict[str, Any]:
    reader = _reader()
    _require_file_support(reader)

    if run is None and time_iso_utc is None:
        raise ValueError(
            "a position is required: pass run (optionally with subrun) or "
            "time_iso_utc. A declaration's range is matched against a point, "
            "so with no point nothing can be shown to apply -- use list_golden "
            "to see every declaration for a source instead."
        )

    one_sid = _one_source(reader, sid, process, stream, aggregation, version)

    from dqm import bands
    from dqm.timeutil import to_utc

    rows = reader.golden(sid=one_sid, with_intervals=True, limit=None)
    # Normalized here because bands.covers compares values, it does not parse
    # them: an ISO string would compare against a Timestamp and raise.
    when = to_utc(time_iso_utc) if time_iso_utc is not None else None
    ordered = bands.newest_first(rows)
    hit = bands.in_force_one(ordered, run=run, subrun=subrun, time=when)

    source = None
    if not rows.empty:
        decoded = _decode_source(reader, rows)
        if "source" in decoded.columns and len(decoded):
            source = decoded["source"].iloc[0]

    position = {"run": run, "subrun": subrun, "time_iso_utc": time_iso_utc}
    response: dict[str, Any] = {
        "sid": one_sid,
        "source": source,
        "position": position,
        "counts": {"declarations_examined": int(len(rows))},
    }

    if hit is None:
        response["golden"] = None
        response["superseded"] = []
        if rows.empty:
            response["note"] = (
                _NO_SUCH_SOURCE.format(sid=one_sid)
                if not _source_exists(reader, one_sid) else
                "this source has no reference file declared at all -- the "
                "common case, and it says nothing about the quality of its "
                "metrics."
            )
        else:
            # Declarations exist and none reach this point, which is a
            # different fact from having none: the range is the thing to look
            # at, not the absence.
            response["note"] = (
                f"{len(rows)} declaration(s) exist for this source but none "
                "covers that position -- a reference declared from a later run "
                "or over a time range does not apply to a run before it. "
                f"list_golden(sid={one_sid}) shows each range. Note a "
                "declaration with a run range is matched by run and one with a "
                "time range by time, so asking by run cannot match a "
                "time-ranged declaration."
            )
        return response

    # The winner, and anything it displaced, taken from the frame so the JSON
    # matches every other tool's rendering of these columns.
    covering = [int(getattr(row, "gid")) for row in ordered
                if bands.covers(row, run=run, subrun=subrun, time=when)]
    winner = int(getattr(hit, "gid"))
    response["golden"] = to_records(rows[rows["gid"] == winner])[0]
    response["superseded"] = to_records(
        rows[rows["gid"].isin([g for g in covering if g != winner])]
        .sort_values("gid", ascending=False)
    )
    if response["superseded"]:
        response["note"] = (
            f"{len(response['superseded'])} older declaration(s) also cover "
            "this position and are superseded by gid "
            f"{winner}; they are kept for history, not applied."
        )
    return response


@mcp.tool(
    description=(
        "Every golden (reference) file declaration for a source, from "
        "dqm.golden, newest commit first -- the declaration history, including "
        "rows that have been superseded. "
        "Use this to see what references exist and over what ranges, or to "
        "audit when a reference was replaced; use get_golden_file to answer "
        "which one actually applies at a run or a time, which is a resolution "
        "and not something to be eyeballed off this list. The order here IS "
        "the supersession order: of the rows covering a position, the first "
        "listed that covers it is the one in force. "
        "A row's interval is a range of VALIDITY, not of measurement: a "
        "declaration from run 125000 with no end is the reference for every "
        "later run until a newer one replaces it. Keyed by source and range "
        "only -- one reference covers every metric the source reports, so "
        "unlike dqm.limits there is no vid here. `note` records how a "
        "hand-made reference was built."
    )
)
@_wrap
def list_golden(
    sid: int | None = None,
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    reader = _reader()
    _require_file_support(reader)
    limit = _clamp_limit(limit)

    one_sid = _one_source(reader, sid, process, stream, aggregation, version)
    df = reader.golden(sid=one_sid, with_intervals=True, limit=None)

    # Newest commit first: the same order the resolution rule walks, so the
    # list reads as the history it is.
    if "gid" in df.columns and not df.empty:
        df = df.sort_values("gid", ascending=False)
        df = _decode_source(reader, df)

    total = int(len(df))
    paged = _page(df, limit, offset)
    response: dict[str, Any] = {
        "filters": {
            "sid": sid, "process": process, "stream": stream,
            "aggregation": aggregation, "version": version,
            "limit": limit, "offset": offset,
        },
        "resolved": {"sid": one_sid},
        "counts": {"total_rows": total},
        "returned": int(len(paged)),
        "truncated": total > len(paged) + offset,
        "results": to_records(paged),
    }
    if total == 0:
        response["note"] = (
            _NO_SUCH_SOURCE.format(sid=one_sid)
            if not _source_exists(reader, one_sid) else
            "no reference file has been declared for this source. That is the "
            "common case and says nothing about the quality of its metrics."
        )
    return response


@mcp.tool(
    description=(
        "List alarm episodes from dqm.alarms. An episode is identified by "
        "(algo, config, subject, sid) -- which evaluator, under which "
        "configuration, found what, on which source -- and NOT by interval: a "
        "condition seen again extends the same episode rather than opening a "
        "new one, so one row can span many runs and many days. `level` is the "
        "severity, escalating as the maximum over the episode's causes. "
        "The run and time spans are a hull of what fired and do NOT claim that "
        "every run inside them alarmed -- get_alarm_causes is the ground truth "
        "for what was actually seen. "
        "Two independent lifecycles live here and must not be conflated: "
        "`open` is whether the condition is still being reported, maintained by "
        "the evaluators; `status` is what a human decided about it (active, "
        "acknowledged, silenced, retired, deleted). An episode can be "
        "status='active' for a week because nobody looked at it, and an "
        "acknowledged problem that is still happening stays open. Setting a "
        "terminal status (retired, deleted) also closes an episode, but closing "
        "one does not change its status. "
        "So: open=true answers 'what is happening now', status answers 'what has "
        "anyone done about it'. Filter on open for a live display, on status for "
        "a review of decisions. "
        "The source each episode concerns is decoded from its sid into a "
        "process/stream/aggregation/version string, since a bare sid is not "
        "readable; use list_sources to go the other way."
    )
)
@_wrap
def list_alarms(
    sid: int | None = None,
    algo: str | None = None,
    config: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    open_only: bool | None = None,
    min_level: int | None = None,
    recent_days: float | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    newest_first: bool = True,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    from dqm import schema

    if status is not None and not schema.valid_alarm_status(status):
        raise ValueError(
            f"status must be one of {sorted(schema.ALARM_STATUSES)}, got {status!r}"
        )
    if min_level is not None and min_level not in range(4):
        raise ValueError(f"min_level must be 0-3, got {min_level}")

    from dqm.reader.transport import where_eq

    clauses = []
    if sid is not None:
        clauses.append(where_eq("sid", int(sid)))
    if algo is not None:
        clauses.append(where_eq("algo", algo))
    if config is not None:
        clauses.append(where_eq("config", config))
    if subject is not None:
        clauses.append(where_eq("subject", subject))
    if status is not None:
        clauses.append(where_eq("status", status))

    df = _reader().read_table("alarms", clauses=clauses or None, limit=None)

    # Filtered here rather than pushed down: these are small tables -- tens of
    # rows -- and a boolean or an inequality is not worth another clause form.
    if open_only is not None and "open" in df.columns:
        df = df[df["open"].fillna(False) == bool(open_only)]
    if min_level is not None and "level" in df.columns:
        df = df[df["level"].fillna(0) >= min_level]
    if recent_days is not None and "mtime" in df.columns:
        from dqm.timeutil import cutoff

        df = df[df["mtime"] >= cutoff(recent_days)]

    if "mtime" in df.columns:
        df = df.sort_values("mtime", ascending=not newest_first)

    total = len(df)

    # An sid alone tells a reader nothing; decode it.
    try:
        sids = [int(x) for x in df["sid"].dropna().unique()]
        if sids:
            src = _reader().sources(sid=sids, limit=None)
            src["source"] = (src["process"].astype(str) + "/"
                             + src["stream"].astype(str) + "/"
                             + src["aggregation"].astype(str) + "/"
                             + src["version"].astype(str))
            df = df.merge(src[["sid", "source"]], on="sid", how="left")
    except Exception:  # pragma: no cover - decoration is a convenience
        LOGGER.debug("could not decode sids", exc_info=True)

    paged = _page(df, limit, offset)
    return {
        "filters": {
            "sid": sid, "algo": algo, "config": config, "subject": subject,
            "status": status, "open_only": open_only, "min_level": min_level,
            "recent_days": recent_days, "limit": limit, "offset": offset,
            "newest_first": newest_first,
        },
        "counts": {"total_rows": total},
        "returned": len(paged),
        "truncated": total > len(paged) + offset,
        # level is the episode severity (message/warn/alarm/severe), which is
        # NOT the alarmcode on a dqm.limits row (ignore/warnOnly/standard/
        # critical).  Both are 0-3; conflating them would mislabel every row.
        "enums": {"level": schema.ALARM_LEVELS,
                  "status": sorted(schema.ALARM_STATUSES)},
        "results": to_records(paged),
    }


@mcp.tool(
    description=(
        "The evidence for one alarm episode, from dqm.alarm_causes. Each cause "
        "names the interval it was seen in (iid) and the metric it concerns "
        "(vid), with the level the algorithm assigned. A NULL vid is a global "
        "test -- one about the interval as a whole rather than a single metric. "
        "This is the ground truth for an episode: dqm.alarms carries a run/time "
        "hull that does not claim every run inside it fired, whereas these rows "
        "are exactly what was seen. A cause keeps the level it was first "
        "reported with, while the episode's level escalates above it. "
        "Each cause is decoded into its metric name and its interval's run and "
        "time range, so the answer does not consist of bare ids. Call "
        "list_alarms first to find the aid."
    )
)
@_wrap
def get_alarm_causes(
    aid: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    limit = _clamp_limit(limit)
    from dqm.reader.transport import where_eq

    r = _reader()
    df = r.read_table("alarm_causes",
                      clauses=[where_eq("aid", int(aid))], limit=None)
    total = len(df)
    if total == 0:
        return {
            "aid": aid,
            "counts": {"total_rows": 0},
            "returned": 0,
            "truncated": False,
            "results": [],
            "note": ("no causes recorded for this aid -- either the episode does "
                     "not exist or it was written without evidence"),
        }

    # Decode the two foreign keys, which is the whole value of this tool: an
    # iid and a vid say nothing on their own.
    try:
        vids = [int(x) for x in df["vid"].dropna().unique()]
        if vids:
            vv = r.values(vid=vids, limit=None)
            vv["metric"] = (vv["groupx"].astype(str) + "/"
                            + vv["subgroup"].astype(str) + "/"
                            + vv["namex"].astype(str))
            df = df.merge(vv[["vid", "metric"]], on="vid", how="left")
    except Exception:  # pragma: no cover
        LOGGER.debug("could not decode vids", exc_info=True)
    try:
        iids = [int(x) for x in df["iid"].dropna().unique()]
        if iids:
            iv = r.intervals(iid=iids, limit=None)
            keep = [c for c in ("iid", "start_run", "start_subrun", "end_run",
                                "end_subrun", "start_time", "end_time")
                    if c in iv.columns]
            df = df.merge(iv[keep], on="iid", how="left")
    except Exception:  # pragma: no cover
        LOGGER.debug("could not decode iids", exc_info=True)

    from dqm import schema

    paged = _page(df, limit, offset)
    return {
        "aid": aid,
        "counts": {"total_rows": total},
        "returned": len(paged),
        "truncated": total > len(paged) + offset,
        "enums": {"level": schema.ALARM_LEVELS},
        "results": to_records(paged),
    }


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("DQM_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dqm-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("DQM_MCP_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DQM_MCP_PORT", str(DEFAULT_PORT))),
        help="bind port (default: %(default)s)",
    )
    parser.add_argument(
        "--qe-base-url",
        default=os.environ.get("DQM_QE_BASE_URL", dqm_reader.DEFAULT_QE_NOCACHE_URL),
        help="Query Engine base URL -- must be the nocache endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--qe-dbname",
        default=os.environ.get("DQM_QE_DBNAME", dqm_reader.DEFAULT_DBNAME),
        help="Query Engine dbname (default: %(default)s)",
    )
    parser.add_argument(
        "--qe-timeout-seconds",
        type=int,
        default=int(
            os.environ.get("DQM_QE_TIMEOUT_SECONDS", str(dqm_reader.DEFAULT_TIMEOUT_SECONDS))
        ),
        help="Query Engine HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="import/construct only, then exit -- does not bind a socket",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    _configure_logging()
    args = _parse_args(argv)

    _state["host"] = args.host
    _state["port"] = args.port
    _state["reader"] = DqmReader(
        base_url=args.qe_base_url,
        dbname=args.qe_dbname,
        timeout_seconds=args.qe_timeout_seconds,
    )

    if args.check:
        print(
            f"OK: dqm_mcp constructed "
            f"(host={args.host} port={args.port} qe_base_url={args.qe_base_url} "
            f"qe_dbname={args.qe_dbname} mu2e-dqm={getattr(dqm, '__version__', '?')})"
        )
        return

    LOGGER.info("Starting dqm MCP server over streamable-http on %s:%s", args.host, args.port)
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
