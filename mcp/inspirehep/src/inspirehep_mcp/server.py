from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests
from mcp.server.mcpserver import MCPServer

LOGGER = logging.getLogger("inspirehep_mcp")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8004
DEFAULT_BASE_URL = "https://inspirehep.net/api"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RESULTS = 20
# INSPIRE-HEP's documented limit is 15 requests per 5s per source IP (429 on
# exceed) -- https://github.com/inspirehep/rest-api-doc. This default (2/s =
# 10 per 5s) stays comfortably under that with margin, same conservative-
# default philosophy as arxiv-mcp's delay_seconds. Response headers were
# checked live while building this server (CORS declares X-RateLimit-* as
# exposable, but none were actually present on real responses), so pacing
# here is fixed/self-imposed, not adaptive to server-reported remaining quota.
DEFAULT_REQUESTS_PER_SECOND = 2.0
DEFAULT_CACHE_TTL_SECONDS = 3600
DEFAULT_CACHE_MAX_ENTRIES = 512

READ_ONLY_INSTRUCTIONS = (
    "Read-only MCP server for INSPIRE-HEP literature/author search, via INSPIRE-HEP's "
    "public REST API (https://inspirehep.net/api). No authentication needed; no write "
    "tools exposed. This is an independent implementation against INSPIRE-HEP's public "
    "API -- not derived from or affiliated with any third-party inspirehep MCP package.\n\n"
    "DATA MODEL: INSPIRE-HEP is a curated HEP bibliographic/citation database -- unlike "
    "arXiv (a separate MCP, if available), it tracks citation graphs, author profiles, "
    "and collaboration metadata as first-class data, not just preprint metadata. Records "
    "are identified by an INSPIRE recid (integer); most tools also accept a DOI or arXiv "
    "id and resolve it to a recid for you.\n\n"
    "TOOLS: search_papers (general search + named filters), get_paper_details (one "
    "paper's full metadata by recid/DOI/arXiv id), get_author_papers (papers by author "
    "name), get_citations (who cites a paper -- via INSPIRE's own 'refersto' query), "
    "search_by_collaboration (ATLAS/CMS/LHCb/etc.), get_references (what a paper cites, "
    "from its own reference list), get_bibtex (BibTeX/LaTeX text for one paper), "
    "get_paper_figures (best-effort -- see its own description, INSPIRE's public API "
    "does not reliably expose extracted figures), server_stats (cache/rate-limit "
    "counters for this server process), get_server_info (version/config).\n\n"
    "RATE LIMIT: INSPIRE-HEP allows 15 requests per 5s per source IP (documented; 429 "
    "on exceed). This server self-paces its own requests and caches GET responses "
    "in-process (see server_stats), but that pacing only covers requests made through "
    "THIS server -- it cannot protect against other traffic sharing the same outbound "
    "IP (same caveat as the arxiv MCP, if deployed alongside this one)."
)


def _server_version() -> str:
    """Resolved from the installed package's metadata -- itself derived from
    the nearest git tag by setuptools_scm at build/install time (see
    pyproject.toml). Falls back gracefully for a raw checkout run without an
    install (e.g. `python -m inspirehep_mcp.server` against source directly)."""
    try:
        return importlib.metadata.version("inspirehep-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("INSPIREHEP_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class _RateLimiter:
    """Enforces a minimum gap between successive outbound requests made
    through this process. Self-imposed pacing only -- see RATE LIMIT note in
    READ_ONLY_INSTRUCTIONS for what this can't cover."""

    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last_request = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.monotonic()


class _TTLCache:
    """Plain in-process dict cache with TTL expiry and a simple oldest-evict
    cap. Not persistent across restarts (unlike some reference figures'
    optional SQLite cache) -- deliberately kept simple for a first draft."""

    def __init__(self, ttl_seconds: float, max_entries: int):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self._max_entries and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (time.monotonic(), value)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else None,
            }


@dataclass
class InspireClient:
    base_url: str
    timeout_seconds: int
    rate_limiter: _RateLimiter
    cache: _TTLCache
    session: requests.Session = field(default_factory=requests.Session)
    request_count: int = 0

    def _cache_key(self, path: str, params: dict[str, Any] | None) -> str:
        return path + "?" + urlencode(sorted((params or {}).items()))

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        key = self._cache_key(path, params)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        self.rate_limiter.wait()
        self.request_count += 1
        url = f"{self.base_url}{path}"
        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout_seconds,
            headers={"User-Agent": "mu2e-inspirehep-mcp (+https://github.com/Mu2e/aitools)"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        self.cache.set(key, data)
        return data

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str | None:
        key = "TEXT:" + self._cache_key(path, params)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        self.rate_limiter.wait()
        self.request_count += 1
        url = f"{self.base_url}{path}"
        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout_seconds,
            headers={"User-Agent": "mu2e-inspirehep-mcp (+https://github.com/Mu2e/aitools)"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        text = response.text
        self.cache.set(key, text)
        return text


def _title_of(metadata: dict[str, Any]) -> str | None:
    titles = metadata.get("titles") or []
    return titles[0].get("title") if titles else None


def _abstract_of(metadata: dict[str, Any]) -> str | None:
    abstracts = metadata.get("abstracts") or []
    return abstracts[0].get("value") if abstracts else None


def _paper_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalizes either a single-record GET response or one search-hit item
    (both have the same {id, metadata, links} shape) into a flat summary."""
    metadata = record.get("metadata", {})
    arxiv_eprints = metadata.get("arxiv_eprints") or []
    dois = metadata.get("dois") or []
    collaborations = metadata.get("collaborations") or []
    pub_info = (metadata.get("publication_info") or [{}])[0]

    return {
        "recid": metadata.get("control_number"),
        "title": _title_of(metadata),
        "authors": [a.get("full_name") for a in (metadata.get("authors") or [])],
        "abstract": _abstract_of(metadata),
        "arxiv_ids": [e.get("value") for e in arxiv_eprints],
        "dois": [d.get("value") for d in dois],
        "collaborations": [c.get("value") for c in collaborations],
        "citation_count": metadata.get("citation_count"),
        "journal": pub_info.get("journal_title"),
        "journal_year": pub_info.get("year"),
        "document_type": metadata.get("document_type"),
        "inspire_url": f"https://inspirehep.net/literature/{metadata.get('control_number')}"
        if metadata.get("control_number")
        else None,
    }


def _reference_record(ref: dict[str, Any]) -> dict[str, Any]:
    inner = ref.get("reference") or {}
    record_ref = (ref.get("record") or {}).get("$ref")
    recid = None
    if record_ref:
        recid = record_ref.rstrip("/").rsplit("/", 1)[-1]
        recid = int(recid) if recid.isdigit() else None
    return {
        "recid": recid,
        "label": inner.get("label"),
        "title": inner.get("title", {}).get("title") if isinstance(inner.get("title"), dict) else None,
        "authors": [a.get("full_name") for a in (inner.get("authors") or [])],
        "arxiv_id": inner.get("arxiv_eprint"),
    }


def _build_literature_query(
    query: str | None,
    title: str | None,
    author: str | None,
    collaboration: str | None,
    category: str | None,
) -> str:
    clauses: list[str] = []
    if query:
        clauses.append(f"({query})")
    if title:
        clauses.append(f'title:"{title}"')
    if author:
        clauses.append(f'authors.full_name:"{author}"')
    if collaboration:
        clauses.append(f"collaboration:{collaboration}")
    if category:
        clauses.append(f"arxiv_eprints.categories:{category}")
    return " AND ".join(clauses)


# mcp>=2.0 renamed FastMCP -> MCPServer; created at module scope (registry-mcp /
# dqm-mcp / metacat-mcp / arxiv-mcp pattern) rather than inside a factory
# function, so tools are decorated once at import time and read their runtime
# config from `_state` per call.
mcp = MCPServer("inspirehep", instructions=READ_ONLY_INSTRUCTIONS)

_state: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "client": InspireClient(
        base_url=DEFAULT_BASE_URL,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        rate_limiter=_RateLimiter(DEFAULT_REQUESTS_PER_SECOND),
        cache=_TTLCache(DEFAULT_CACHE_TTL_SECONDS, DEFAULT_CACHE_MAX_ENTRIES),
    ),
}


def _get_literature_by_identifier(client: InspireClient, identifier: str) -> dict[str, Any] | None:
    """INSPIRE's single-record endpoint accepts a bare recid or an external
    identifier (DOI, arXiv id, ORCID) directly per its own docs -- try that
    first since it's one request either way, and fall back to a field search
    only if that comes back 404 (e.g. an id INSPIRE's resolver doesn't
    recognize in that exact form)."""
    identifier = identifier.strip()
    record = client.get_json(f"/literature/{identifier}")
    if record is not None:
        return record

    for field_query in (f'arxiv:{identifier}', f'doi:"{identifier}"'):
        result = client.get_json("/literature", {"q": field_query, "size": 1})
        hits = (result or {}).get("hits", {}).get("hits", [])
        if hits:
            return hits[0]
    return None


@mcp.tool(description="Return server capabilities, version, rate-limit, and cache configuration.")
def get_server_info() -> dict[str, Any]:
    client = _state["client"]
    return {
        "name": "inspirehep",
        "version": _server_version(),
        "read_only": True,
        "transport": "streamable-http",
        "base_url": client.base_url,
        "rate_limit": {
            "requests_per_second": 1.0 / client.rate_limiter._min_interval
            if client.rate_limiter._min_interval
            else None,
            "inspirehep_documented_limit": "15 requests / 5s per source IP (429 on exceed)",
        },
        "cache": client.cache.stats(),
        "notes": [
            "Independent implementation against INSPIRE-HEP's public REST API "
            "(https://inspirehep.net/api) -- not affiliated with any third-party "
            "inspirehep MCP package.",
            "get_paper_figures is best-effort: INSPIRE's public API does not reliably "
            "expose extracted figures, unlike the other tools.",
            "Rate-limit pacing here only covers requests made through this server; see "
            "server instructions for what it can't cover.",
        ],
        "tools": [
            "search_papers",
            "get_paper_details",
            "get_author_papers",
            "get_citations",
            "search_by_collaboration",
            "get_references",
            "get_bibtex",
            "get_paper_figures",
            "server_stats",
            "get_server_info",
        ],
    }


@mcp.tool(description="Return cache hit-rate and request-count counters for this server process (resets on restart).")
def server_stats() -> dict[str, Any]:
    client = _state["client"]
    return {
        "requests_made_to_inspirehep": client.request_count,
        "cache": client.cache.stats(),
    }


@mcp.tool(
    description=(
        "Search INSPIRE-HEP literature by query and/or named filters (title/author/"
        "collaboration/category), sorted by most recent or most cited. Prefer named "
        "filters for a single clean condition each; use query for INSPIRE's raw "
        "Elasticsearch-syntax field queries (e.g. 'refereed:true') for anything they "
        "can't express -- both compose together (AND-combined) if given at once. At "
        "least one of query/title/author/collaboration/category is required."
    )
)
def search_papers(
    query: str | None = None,
    title: str | None = None,
    author: str | None = None,
    collaboration: str | None = None,
    category: str | None = None,
    sort: str = "mostrecent",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    if sort not in {"mostrecent", "mostcited"}:
        raise ValueError("sort must be 'mostrecent' or 'mostcited'")

    max_results = max(1, min(max_results, 200))
    full_query = _build_literature_query(query, title, author, collaboration, category)
    if not full_query:
        raise ValueError(
            "At least one of query/title/author/collaboration/category is required."
        )

    client = _state["client"]
    result = client.get_json(
        "/literature",
        {"q": full_query, "sort": sort, "size": max_results},
    )
    hits = (result or {}).get("hits", {})
    results = [_paper_record(h) for h in hits.get("hits", [])]

    return {
        "applied_filters": {
            "query": query,
            "title": title,
            "author": author,
            "collaboration": collaboration,
            "category": category,
            "sort": sort,
            "max_results": max_results,
        },
        "query_used": full_query,
        "total_matches": hits.get("total"),
        "returned": len(results),
        "results": results,
    }


@mcp.tool(
    description=(
        "Get full metadata for one paper by INSPIRE recid, DOI, or arXiv id -- e.g. "
        "'451647', '10.4310/ATMP.1998.v2.n2.a1', or 'hep-th/9711200'. Metadata only, "
        "same read-only/no-download scope as get_bibtex/get_references."
    )
)
def get_paper_details(identifier: str) -> dict[str, Any]:
    client = _state["client"]
    record = _get_literature_by_identifier(client, identifier)
    if record is None:
        raise ValueError(f"No paper found for identifier={identifier!r}")
    return _paper_record(record)


@mcp.tool(
    description=(
        "Get papers by an author's full name (e.g. 'Maldacena, Juan Martin' -- INSPIRE "
        "matches on authors.full_name, so 'Last, First' form works best), sorted by "
        "most recent or most cited. This is a literature search scoped to one author "
        "name, not an author-profile lookup -- name collisions across different people "
        "are possible for common names."
    )
)
def get_author_papers(
    author_name: str,
    sort: str = "mostrecent",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    if sort not in {"mostrecent", "mostcited"}:
        raise ValueError("sort must be 'mostrecent' or 'mostcited'")
    max_results = max(1, min(max_results, 200))

    client = _state["client"]
    query = f'authors.full_name:"{author_name}"'
    result = client.get_json("/literature", {"q": query, "sort": sort, "size": max_results})
    hits = (result or {}).get("hits", {})
    results = [_paper_record(h) for h in hits.get("hits", [])]

    return {
        "author_name": author_name,
        "query_used": query,
        "total_matches": hits.get("total"),
        "returned": len(results),
        "results": results,
    }


@mcp.tool(
    description=(
        "Get papers that CITE the given paper (its citation count is in "
        "get_paper_details' citation_count field; this returns the actual citing "
        "papers). Uses INSPIRE's own refersto:recid query. For the reverse direction "
        "(what this paper cites), use get_references instead."
    )
)
def get_citations(
    identifier: str,
    sort: str = "mostrecent",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    if sort not in {"mostrecent", "mostcited"}:
        raise ValueError("sort must be 'mostrecent' or 'mostcited'")
    max_results = max(1, min(max_results, 200))

    client = _state["client"]
    record = _get_literature_by_identifier(client, identifier)
    if record is None:
        raise ValueError(f"No paper found for identifier={identifier!r}")
    recid = record.get("metadata", {}).get("control_number")

    query = f"refersto:recid:{recid}"
    result = client.get_json("/literature", {"q": query, "sort": sort, "size": max_results})
    hits = (result or {}).get("hits", {})
    results = [_paper_record(h) for h in hits.get("hits", [])]

    return {
        "identifier": identifier,
        "recid": recid,
        "query_used": query,
        "total_matches": hits.get("total"),
        "returned": len(results),
        "results": results,
    }


@mcp.tool(
    description=(
        "Get papers from a specific experimental collaboration (e.g. 'ATLAS', 'CMS', "
        "'LHCb', 'Mu2e'), optionally combined with a free-text query, sorted by most "
        "recent or most cited."
    )
)
def search_by_collaboration(
    collaboration: str,
    query: str | None = None,
    sort: str = "mostrecent",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    if sort not in {"mostrecent", "mostcited"}:
        raise ValueError("sort must be 'mostrecent' or 'mostcited'")
    max_results = max(1, min(max_results, 200))

    full_query = _build_literature_query(query, None, None, collaboration, None)
    client = _state["client"]
    result = client.get_json("/literature", {"q": full_query, "sort": sort, "size": max_results})
    hits = (result or {}).get("hits", {})
    results = [_paper_record(h) for h in hits.get("hits", [])]

    return {
        "collaboration": collaboration,
        "query_used": full_query,
        "total_matches": hits.get("total"),
        "returned": len(results),
        "results": results,
    }


@mcp.tool(
    description=(
        "Get the reference list FROM one paper (what it cites) -- the reverse of "
        "get_citations. Entries include title/authors/arxiv_id where INSPIRE has "
        "matched the reference to a record; unmatched/lightly-parsed references may "
        "have only a label."
    )
)
def get_references(identifier: str) -> dict[str, Any]:
    client = _state["client"]
    record = _get_literature_by_identifier(client, identifier)
    if record is None:
        raise ValueError(f"No paper found for identifier={identifier!r}")

    metadata = record.get("metadata", {})
    refs = [_reference_record(r) for r in (metadata.get("references") or [])]

    return {
        "identifier": identifier,
        "recid": metadata.get("control_number"),
        "reference_count": len(refs),
        "references": refs,
    }


@mcp.tool(
    description=(
        "Get a citation entry for one paper as text, in BibTeX (default), or LaTeX "
        "('latex-eu'/'latex-us' style). Returns the raw text INSPIRE generates, "
        "unmodified."
    )
)
def get_bibtex(identifier: str, format: str = "bibtex") -> dict[str, Any]:
    if format not in {"bibtex", "latex-eu", "latex-us"}:
        raise ValueError("format must be one of: bibtex, latex-eu, latex-us")

    client = _state["client"]
    record = _get_literature_by_identifier(client, identifier)
    if record is None:
        raise ValueError(f"No paper found for identifier={identifier!r}")
    recid = record.get("metadata", {}).get("control_number")

    text = client.get_text(f"/literature/{recid}", {"format": format})
    return {
        "identifier": identifier,
        "recid": recid,
        "format": format,
        "text": text,
    }


@mcp.tool(
    description=(
        "Best-effort: get any figure/document URLs INSPIRE has attached to one paper's "
        "record. UNLIKE the other tools here, this is NOT reliably supported by "
        "INSPIRE-HEP's public API -- there is no dedicated figures endpoint or "
        "guaranteed metadata field for extracted figures; this returns whatever is in "
        "the record's own 'documents' metadata (when present), which is commonly empty. "
        "Expect empty results for most papers; do not treat that as confirmation a "
        "paper has no figures."
    )
)
def get_paper_figures(identifier: str) -> dict[str, Any]:
    client = _state["client"]
    record = _get_literature_by_identifier(client, identifier)
    if record is None:
        raise ValueError(f"No paper found for identifier={identifier!r}")

    metadata = record.get("metadata", {})
    documents = metadata.get("documents") or []
    figures = metadata.get("figures") or []

    return {
        "identifier": identifier,
        "recid": metadata.get("control_number"),
        "documents": [
            {"url": d.get("url"), "description": d.get("description"), "key": d.get("key")}
            for d in documents
        ],
        "figures": [
            {"url": f.get("url"), "caption": f.get("caption"), "key": f.get("key")}
            for f in figures
        ],
        "warning": "INSPIRE's public API does not reliably expose figures; an empty "
        "result here does not confirm the paper has none.",
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="inspirehep-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("INSPIREHEP_MCP_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("INSPIREHEP_MCP_PORT", str(DEFAULT_PORT))),
        help="bind port (default: %(default)s)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("INSPIREHEP_MCP_BASE_URL", DEFAULT_BASE_URL),
        help="INSPIRE-HEP API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=float(
            os.environ.get("INSPIREHEP_MCP_REQUESTS_PER_SECOND", str(DEFAULT_REQUESTS_PER_SECOND))
        ),
        help="self-imposed pacing for requests this server makes -- INSPIRE-HEP's "
        "documented limit is 15/5s per IP (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-ttl-seconds",
        type=float,
        default=float(
            os.environ.get("INSPIREHEP_MCP_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
        ),
        help="in-process GET response cache TTL (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-max-entries",
        type=int,
        default=int(
            os.environ.get("INSPIREHEP_MCP_CACHE_MAX_ENTRIES", str(DEFAULT_CACHE_MAX_ENTRIES))
        ),
        help="in-process GET response cache size cap (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(
            os.environ.get("INSPIREHEP_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        ),
        help="HTTP request timeout (default: %(default)s)",
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
    _state["client"] = InspireClient(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        rate_limiter=_RateLimiter(args.requests_per_second),
        cache=_TTLCache(args.cache_ttl_seconds, args.cache_max_entries),
    )

    if args.check:
        print(
            f"OK: inspirehep_mcp constructed "
            f"(host={args.host} port={args.port} base_url={args.base_url} "
            f"requests_per_second={args.requests_per_second})"
        )
        return

    LOGGER.info(
        "Starting inspirehep MCP server over streamable-http on %s:%s", args.host, args.port
    )
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
