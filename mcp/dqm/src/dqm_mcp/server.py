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
import importlib.metadata
import logging
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

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
    "dqm.alarms and dqm.alarm_causes.\n\n"
    "DATA MODEL (7 tables: 3 catalogs, 2 measurement tables, 2 alarm tables):\n"
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
