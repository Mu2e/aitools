from __future__ import annotations

import argparse
import csv
import importlib.metadata
import io
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from mcp.server.mcpserver import MCPServer

LOGGER = logging.getLogger("dqm_mcp")


def _server_version() -> str:
    """Resolved from the installed package's metadata -- itself derived from
    the nearest git tag by setuptools_scm at build/install time (see
    pyproject.toml). Falls back gracefully for a raw checkout run without an
    install (e.g. `python -m dqm_mcp.server` against source directly)."""
    try:
        return importlib.metadata.version("dqm-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"

DEFAULT_DBNAME = "mu2e_dqm_prd"
DEFAULT_QE_NOCACHE_URL = "https://dbdata0vm.fnal.gov:9443/QE/mu2e/prod/app/SQ/query?"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_LIMIT = 100
DEFAULT_RECENT_DAYS = 10
DEFAULT_SCAN_LIMIT = 2000
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001

READ_ONLY_INSTRUCTIONS = (
    "Read-only MCP server for Mu2e DQM metrics via Query Engine over HTTP. "
    "Always use nocache endpoint access semantics. "
    "Return structured JSON rows from dqm.sources, dqm.values, dqm.intervals, dqm.numbers, and dqm.limits.\n\n"
    "DATA MODEL: a source (sid, from dqm.sources) is one monitored stream/process "
    "(e.g. valNightly/reco); a value (vid, from dqm.values) is one named variable that "
    "stream tracks (e.g. CPU time, a fit chi2, an occupancy); an interval (iid, from "
    "dqm.intervals) is one time bucket for a source (e.g. one day, one run). Rows in "
    "dqm.numbers/dqm.limits are keyed by all three (sid, vid, iid). "
    "The natural, meaningful query is one sid + one vid: that returns a timeline of one "
    "variable across intervals, suitable for trending/plotting. A bare sid with no vid "
    "mixes every variable that source tracks into one undifferentiated pile -- rarely "
    "what you want, and easy to misread as 'this stream has no recent data' when really "
    "the results just contain a jumble of unrelated variables (or, if scan_complete is "
    "false, an incomplete scan -- see query_metrics). Typical flow: list_sources to find "
    "sid, list_values to find vid, then query_metrics(sid=..., vid=...)."
)


@dataclass
class QEClient:
    base_url: str
    dbname: str
    timeout_seconds: int

    def query_csv(
        self,
        table: str,
        columns: str,
        where: list[str] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        params: list[tuple[str, str]] = [
            ("dbname", self.dbname),
            ("t", table),
            ("c", columns),
            ("f", "csv"),
        ]

        if where:
            for clause in where:
                params.append(("w", clause))
        if order:
            params.append(("o", order))
        if limit is not None:
            params.append(("l", str(limit)))

        response = requests.get(self.base_url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()

        payload = response.text.strip()
        if not payload:
            return []

        reader = csv.DictReader(io.StringIO(payload))
        return list(reader)


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("DQM_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_dt_user(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_limit(limit: int) -> int:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    return limit


def _apply_offset(rows: list[dict[str, Any]], limit: int, offset: int) -> list[dict[str, Any]]:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    return rows[offset : offset + limit]


def _build_qe_client(base_url: str, dbname: str, timeout_seconds: int) -> QEClient:
    if ":8444/" in base_url:
        raise ValueError("DQM_QE_BASE_URL points to cache endpoint (:8444); nocache (:9443) is required")
    return QEClient(base_url=base_url, dbname=dbname, timeout_seconds=timeout_seconds)


def _sources_by_id(client: QEClient, sids: set[int]) -> dict[int, dict[str, Any]]:
    if not sids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for sid in sids:
        rows = client.query_csv(
            "dqm.sources",
            columns="sid,process,stream,aggregation,version",
            where=[f"sid:eq:{sid}"],
            limit=1,
        )
        if rows:
            row = rows[0]
            out[sid] = {
                "sid": sid,
                "process": row.get("process"),
                "stream": row.get("stream"),
                "aggregation": row.get("aggregation"),
                "version": row.get("version"),
            }
    return out


def _values_by_id(client: QEClient, vids: set[int]) -> dict[int, dict[str, Any]]:
    if not vids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for vid in vids:
        rows = client.query_csv(
            "dqm.values",
            columns="vid,groupx,subgroup,namex",
            where=[f"vid:eq:{vid}"],
            limit=1,
        )
        if rows:
            row = rows[0]
            out[vid] = {
                "vid": vid,
                "groupx": row.get("groupx"),
                "subgroup": row.get("subgroup"),
                "namex": row.get("namex"),
            }
    return out


def _intervals_by_id(client: QEClient, iids: set[int]) -> dict[int, dict[str, Any]]:
    if not iids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for iid in iids:
        rows = client.query_csv(
            "dqm.intervals",
            columns="iid,sid,start_run,start_subrun,end_run,end_subrun,start_time,end_time",
            where=[f"iid:eq:{iid}"],
            limit=1,
        )
        if rows:
            row = rows[0]
            out[iid] = {
                "iid": iid,
                "sid": _parse_int(row.get("sid")),
                "start_run": _parse_int(row.get("start_run")),
                "start_subrun": _parse_int(row.get("start_subrun")),
                "end_run": _parse_int(row.get("end_run")),
                "end_subrun": _parse_int(row.get("end_subrun")),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
            }
    return out


# mcp>=2.0 renamed FastMCP -> MCPServer; created at module scope (registry-mcp
# pattern) rather than inside a factory function, so tools are decorated once
# at import time and read their runtime config from `_state` per call.
mcp = MCPServer("dqm", instructions=READ_ONLY_INSTRUCTIONS)

# Set once by main() from CLI args (env vars as fallback defaults, same
# precedence as registry-mcp) before mcp.run(); read per-call by the tools
# below. The defaults here match the argparse defaults so importing this
# module without calling main() (e.g. --check, or ad-hoc tooling) still gets
# a working client.
_state: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "client": QEClient(
        base_url=DEFAULT_QE_NOCACHE_URL,
        dbname=DEFAULT_DBNAME,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    ),
}


@mcp.tool(description="Get DQM MCP configuration and defaults.")
def get_server_info() -> dict[str, Any]:
    client = _state["client"]
    return {
        "name": "dqm",
        "version": _server_version(),
        "read_only": True,
        "transport": "streamable-http",
        "qe": {
            "base_url": client.base_url,
            "dbname": client.dbname,
            "timeout_seconds": client.timeout_seconds,
            "nocache_required": True,
        },
        "defaults": {
            "query_limit": DEFAULT_LIMIT,
            "recent_days": DEFAULT_RECENT_DAYS,
            "scan_limit": DEFAULT_SCAN_LIMIT,
        },
    }


@mcp.tool(description="List DQM metric sources from dqm.sources.")
def list_sources(
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    version: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    client = _state["client"]
    limit = _normalize_limit(limit)

    where: list[str] = []
    if process:
        where.append(f"process:eq:{process}")
    if stream:
        where.append(f"stream:eq:{stream}")
    if aggregation:
        where.append(f"aggregation:eq:{aggregation}")
    if version:
        where.append(f"version:eq:{version}")

    rows = client.query_csv(
        "dqm.sources",
        columns="sid,process,stream,aggregation,version",
        where=where or None,
        order="sid",
        limit=max(limit + offset, DEFAULT_LIMIT),
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "sid": _parse_int(row.get("sid")),
                "process": row.get("process"),
                "stream": row.get("stream"),
                "aggregation": row.get("aggregation"),
                "version": row.get("version"),
            }
        )

    paged = _apply_offset(out, limit, offset)
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
        "results": paged,
    }


@mcp.tool(description="List unique source versions, optionally filtered by process/stream/aggregation.")
def list_versions(
    process: str | None = None,
    stream: str | None = None,
    aggregation: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    client = _state["client"]
    limit = _normalize_limit(limit)

    where: list[str] = []
    if process:
        where.append(f"process:eq:{process}")
    if stream:
        where.append(f"stream:eq:{stream}")
    if aggregation:
        where.append(f"aggregation:eq:{aggregation}")

    rows = client.query_csv(
        "dqm.sources",
        columns="sid,process,stream,aggregation,version",
        where=where or None,
        order="sid",
        limit=max(limit * 3, DEFAULT_LIMIT),
    )

    versions = sorted({row.get("version") for row in rows if row.get("version") is not None})
    return {
        "filters": {
            "process": process,
            "stream": stream,
            "aggregation": aggregation,
        },
        "version_count": len(versions),
        "versions": versions[:limit],
        "sources_examined": len(rows),
    }


@mcp.tool(description="List DQM value names from dqm.values.")
def list_values(
    groupx: str | None = None,
    subgroup: str | None = None,
    namex: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    client = _state["client"]
    limit = _normalize_limit(limit)

    where: list[str] = []
    if groupx:
        where.append(f"groupx:eq:{groupx}")
    if subgroup:
        where.append(f"subgroup:eq:{subgroup}")
    if namex:
        where.append(f"namex:eq:{namex}")

    rows = client.query_csv(
        "dqm.values",
        columns="vid,groupx,subgroup,namex",
        where=where or None,
        order="vid",
        limit=max(limit + offset, DEFAULT_LIMIT),
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "vid": _parse_int(row.get("vid")),
                "groupx": row.get("groupx"),
                "subgroup": row.get("subgroup"),
                "namex": row.get("namex"),
            }
        )

    paged = _apply_offset(out, limit, offset)
    return {
        "filters": {
            "groupx": groupx,
            "subgroup": subgroup,
            "namex": namex,
            "limit": limit,
            "offset": offset,
        },
        "returned": len(paged),
        "results": paged,
    }


@mcp.tool(description="List DQM intervals with run/subrun or time filters.")
def list_intervals(
    sid: int | None = None,
    run: int | None = None,
    subrun: int | None = None,
    start_time_after_iso_utc: str | None = None,
    end_time_before_iso_utc: str | None = None,
    recent_days: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    newest_first: bool = True,
) -> dict[str, Any]:
    client = _state["client"]
    limit = _normalize_limit(limit)

    where: list[str] = []
    if sid is not None:
        where.append(f"sid:eq:{sid}")
    if run is not None:
        where.append(f"start_run:le:{run}")
        where.append(f"end_run:ge:{run}")
    if subrun is not None:
        where.append(f"start_subrun:le:{subrun}")
        where.append(f"end_subrun:ge:{subrun}")

    if recent_days is not None:
        recent_start = datetime.now(timezone.utc) - timedelta(days=recent_days)
        where.append(f"end_time:ge:{recent_start.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    if start_time_after_iso_utc:
        dt = _parse_dt_user(start_time_after_iso_utc)
        where.append(f"end_time:ge:{dt.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    if end_time_before_iso_utc:
        dt = _parse_dt_user(end_time_before_iso_utc)
        where.append(f"start_time:le:{dt.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    rows = client.query_csv(
        "dqm.intervals",
        columns="iid,sid,start_run,start_subrun,end_run,end_subrun,start_time,end_time",
        where=where or None,
        order="-iid" if newest_first else "iid",
        limit=max(limit + offset, DEFAULT_LIMIT),
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "iid": _parse_int(row.get("iid")),
                "sid": _parse_int(row.get("sid")),
                "start_run": _parse_int(row.get("start_run")),
                "start_subrun": _parse_int(row.get("start_subrun")),
                "end_run": _parse_int(row.get("end_run")),
                "end_subrun": _parse_int(row.get("end_subrun")),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
            }
        )

    paged = _apply_offset(out, limit, offset)
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
        "results": paged,
    }


@mcp.tool(
    description=(
        "Query DQM metrics from dqm.numbers (default) or dqm.limits with optional source/value expansion. "
        "Defaults to recent_days=10 and limit=100. "
        "Typical call is sid + vid together -- that's a timeline of one named variable "
        "for one source across intervals (a trend/plot-ready series). sid alone (no vid) "
        "returns every variable that source tracks interleaved in one list -- rarely "
        "useful, since results from different variables have unrelated valuex scales and "
        "meanings; prefer list_values to find the vid you want first. When sid and/or vid "
        "resolve to exactly one value (explicitly, or via process/stream/... narrowing to "
        "one source), that filter is pushed down to the query itself; broader/unresolved "
        "filters fall back to scanning the most recent scan_limit rows and filtering "
        "client-side, which can be truncated -- always check counts.scan_complete before "
        "treating matched_rows=0 as a confirmed absence of data."
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
    recent_days: int = DEFAULT_RECENT_DAYS,
    sort_by: str = "end_time",
    sort_order: str = "desc",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    expand_ids: bool = True,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    client = _state["client"]
    metric_table = metric_table.strip().lower()
    if metric_table not in {"numbers", "limits"}:
        raise ValueError("metric_table must be 'numbers' or 'limits'")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be 'asc' or 'desc'")

    allowed_sort = {
        "numbers": {"nid", "valuex", "sigma", "code", "start_run", "end_run", "start_subrun", "end_subrun", "start_time", "end_time"},
        "limits": {"lid", "llimit", "ulimit", "sigma", "alarmcode", "start_run", "end_run", "start_subrun", "end_subrun", "start_time", "end_time"},
    }
    if sort_by not in allowed_sort[metric_table]:
        raise ValueError(f"sort_by is not valid for {metric_table}")

    limit = _normalize_limit(limit)
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if scan_limit <= 0:
        raise ValueError("scan_limit must be > 0")

    source_where: list[str] = []
    if sid is not None:
        source_where.append(f"sid:eq:{sid}")
    if process:
        source_where.append(f"process:eq:{process}")
    if stream:
        source_where.append(f"stream:eq:{stream}")
    if aggregation:
        source_where.append(f"aggregation:eq:{aggregation}")
    if version:
        source_where.append(f"version:eq:{version}")

    source_rows = client.query_csv(
        "dqm.sources",
        columns="sid,process,stream,aggregation,version",
        where=source_where or None,
        order="sid",
        limit=scan_limit,
    )
    allowed_sids = {_parse_int(r.get("sid")) for r in source_rows}
    allowed_sids.discard(None)

    # A single resolved sid/vid -- given explicitly, or narrowed to exactly one
    # via process/stream/aggregation/version (resp. groupx/subgroup/namex) --
    # gets pushed down as a real where= clause on dqm.intervals/dqm.numbers/
    # dqm.limits below, instead of only being applied as a client-side filter
    # after an unscoped top-scan_limit-by-pk fetch. Without this, a source
    # with a low insertion rate relative to others sharing the same table
    # (e.g. one daily valNightly source alongside ~90 higher-volume CRV
    # sources) can have its rows fall outside any bounded scan window no
    # matter how large scan_limit is set -- raising scan_limit doesn't help
    # because the required window depends on everyone else's insertion rate,
    # not this query's own selectivity. When sid/vid can't be resolved to a
    # single value (e.g. an aggregation filter matching several sources),
    # this falls back to the previous unscoped-scan-plus-client-filter
    # behavior -- see the scan_complete flag below for that case.
    resolved_sid = sid if sid is not None else (next(iter(allowed_sids)) if len(allowed_sids) == 1 else None)

    value_where: list[str] = []
    if vid is not None:
        value_where.append(f"vid:eq:{vid}")
    if groupx:
        value_where.append(f"groupx:eq:{groupx}")
    if subgroup:
        value_where.append(f"subgroup:eq:{subgroup}")
    if namex:
        value_where.append(f"namex:eq:{namex}")

    value_rows = client.query_csv(
        "dqm.values",
        columns="vid,groupx,subgroup,namex",
        where=value_where or None,
        order="vid",
        limit=scan_limit,
    )
    allowed_vids = {_parse_int(r.get("vid")) for r in value_rows}
    allowed_vids.discard(None)

    resolved_vid = vid if vid is not None else (next(iter(allowed_vids)) if len(allowed_vids) == 1 else None)

    interval_where: list[str] = []
    if resolved_sid is not None:
        interval_where.append(f"sid:eq:{resolved_sid}")
    if run is not None:
        interval_where.append(f"start_run:le:{run}")
        interval_where.append(f"end_run:ge:{run}")
    if subrun is not None:
        interval_where.append(f"start_subrun:le:{subrun}")
        interval_where.append(f"end_subrun:ge:{subrun}")

    if recent_days is not None:
        recent_start = datetime.now(timezone.utc) - timedelta(days=recent_days)
        interval_where.append(f"end_time:ge:{recent_start.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    if start_time_after_iso_utc:
        dt = _parse_dt_user(start_time_after_iso_utc)
        interval_where.append(f"end_time:ge:{dt.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    if end_time_before_iso_utc:
        dt = _parse_dt_user(end_time_before_iso_utc)
        interval_where.append(f"start_time:le:{dt.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    interval_rows = client.query_csv(
        "dqm.intervals",
        columns="iid,sid,start_run,start_subrun,end_run,end_subrun,start_time,end_time",
        where=interval_where or None,
        order="-iid",
        limit=scan_limit,
    )

    allowed_iids = {_parse_int(r.get("iid")) for r in interval_rows}
    allowed_iids.discard(None)

    metric_cols = "nid,sid,iid,vid,valuex,sigma,code" if metric_table == "numbers" else "lid,sid,iid,vid,llimit,ulimit,sigma,alarmcode"
    metric_pk = "nid" if metric_table == "numbers" else "lid"

    metric_where: list[str] = []
    if resolved_sid is not None:
        metric_where.append(f"sid:eq:{resolved_sid}")
    if resolved_vid is not None:
        metric_where.append(f"vid:eq:{resolved_vid}")

    metric_rows = client.query_csv(
        f"dqm.{metric_table}",
        columns=metric_cols,
        where=metric_where or None,
        order=f"-{metric_pk}",
        limit=scan_limit,
    )

    interval_map: dict[int, dict[str, Any]] = {}
    for row in interval_rows:
        iid_val = _parse_int(row.get("iid"))
        if iid_val is None:
            continue
        interval_map[iid_val] = {
            "iid": iid_val,
            "sid": _parse_int(row.get("sid")),
            "start_run": _parse_int(row.get("start_run")),
            "start_subrun": _parse_int(row.get("start_subrun")),
            "end_run": _parse_int(row.get("end_run")),
            "end_subrun": _parse_int(row.get("end_subrun")),
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
        }

    source_map = {sidv: item for sidv, item in _sources_by_id(client, {s for s in allowed_sids if s is not None}).items()}
    value_map = {vidv: item for vidv, item in _values_by_id(client, {v for v in allowed_vids if v is not None}).items()}

    results: list[dict[str, Any]] = []
    for row in metric_rows:
        sid_val = _parse_int(row.get("sid"))
        vid_val = _parse_int(row.get("vid"))
        iid_val = _parse_int(row.get("iid"))

        if sid_val is None or vid_val is None or iid_val is None:
            continue
        if sid_val not in allowed_sids:
            continue
        if vid_val not in allowed_vids:
            continue
        if iid_val not in allowed_iids:
            continue

        interval = interval_map.get(iid_val)
        if interval is None:
            interval = _intervals_by_id(client, {iid_val}).get(iid_val)
            if interval is not None:
                interval_map[iid_val] = interval

        entry: dict[str, Any] = {
            "sid": sid_val,
            "vid": vid_val,
            "iid": iid_val,
        }

        if metric_table == "numbers":
            entry["nid"] = _parse_int(row.get("nid"))
            entry["valuex"] = _parse_float(row.get("valuex"))
            entry["sigma"] = _parse_float(row.get("sigma"))
            entry["code"] = _parse_int(row.get("code"))
        else:
            entry["lid"] = _parse_int(row.get("lid"))
            entry["llimit"] = _parse_float(row.get("llimit"))
            entry["ulimit"] = _parse_float(row.get("ulimit"))
            entry["sigma"] = _parse_float(row.get("sigma"))
            entry["alarmcode"] = _parse_int(row.get("alarmcode"))

        if interval is not None:
            entry["interval"] = interval
        if expand_ids:
            if sid_val in source_map:
                entry["source"] = source_map[sid_val]
            if vid_val in value_map:
                entry["value"] = value_map[vid_val]

        results.append(entry)

    reverse = sort_order == "desc"

    def sort_key(item: dict[str, Any]) -> Any:
        if sort_by in item:
            return item.get(sort_by)
        interval = item.get("interval", {})
        v = interval.get(sort_by)
        if sort_by in {"start_time", "end_time"}:
            return _parse_dt(v) or datetime.fromtimestamp(0, tz=timezone.utc)
        return v

    results.sort(key=sort_key, reverse=reverse)
    paged = _apply_offset(results, limit, offset)

    # scan_complete is the structural signal: matched_rows == 0 alone is
    # ambiguous between "confirmed no data" and "scan stopped before finding
    # any" -- a caller (human or agent) must check this flag before reporting
    # an empty result as authoritative. True whenever the metric_rows fetch
    # was not truncated, i.e. every row satisfying metric_where was examined.
    scan_complete = len(metric_rows) < scan_limit

    warnings: list[str] = []
    if not scan_complete and not results:
        warnings.append(
            f"INCOMPLETE SCAN, ZERO MATCHES: scan_limit was reached scanning dqm.{metric_table} "
            "before finding any matching rows. This does NOT confirm no data exists for these "
            "filters -- it means the scan stopped before reaching any that might match. Do not "
            "report this as 'no data'; see scan_complete=false in counts. Pass an explicit sid "
            "and/or vid (or narrow process/stream/aggregation/version to resolve to one source) "
            "so the filter can be pushed down to the query itself, or raise scan_limit."
        )
    elif not scan_complete:
        warnings.append(
            "scan_limit reached; more matching rows may exist beyond the scanned window "
            "(see scan_complete=false in counts). Narrow the query or raise scan_limit for "
            "a complete result."
        )

    return {
        "metric_table": metric_table,
        "filters": {
            "sid": sid,
            "vid": vid,
            "process": process,
            "stream": stream,
            "aggregation": aggregation,
            "version": version,
            "groupx": groupx,
            "subgroup": subgroup,
            "namex": namex,
            "run": run,
            "subrun": subrun,
            "start_time_after_iso_utc": start_time_after_iso_utc,
            "end_time_before_iso_utc": end_time_before_iso_utc,
            "recent_days": recent_days,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
            "expand_ids": expand_ids,
            "scan_limit": scan_limit,
        },
        "counts": {
            "candidate_sources": len(allowed_sids),
            "candidate_values": len(allowed_vids),
            "candidate_intervals": len(allowed_iids),
            "scanned_metric_rows": len(metric_rows),
            "matched_rows": len(results),
            "returned_rows": len(paged),
            "scan_complete": scan_complete,
        },
        "warnings": warnings,
        "results": paged,
    }


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
        default=os.environ.get("DQM_QE_BASE_URL", DEFAULT_QE_NOCACHE_URL),
        help="Query Engine base URL -- must be the nocache endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--qe-dbname",
        default=os.environ.get("DQM_QE_DBNAME", DEFAULT_DBNAME),
        help="Query Engine dbname (default: %(default)s)",
    )
    parser.add_argument(
        "--qe-timeout-seconds",
        type=int,
        default=int(os.environ.get("DQM_QE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
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
    _state["client"] = _build_qe_client(args.qe_base_url, args.qe_dbname, args.qe_timeout_seconds)

    if args.check:
        print(
            f"OK: dqm_mcp constructed "
            f"(host={args.host} port={args.port} qe_base_url={args.qe_base_url} qe_dbname={args.qe_dbname})"
        )
        return

    LOGGER.info("Starting dqm MCP server over streamable-http on %s:%s", args.host, args.port)
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
