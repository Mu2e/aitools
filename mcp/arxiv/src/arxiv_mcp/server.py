from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import sys
from typing import Any

import arxiv
from mcp.server.mcpserver import MCPServer

LOGGER = logging.getLogger("arxiv_mcp")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8003
DEFAULT_MAX_RESULTS = 20
DEFAULT_PAGE_SIZE = 100
DEFAULT_DELAY_SECONDS = 3.0
DEFAULT_NUM_RETRIES = 3

READ_ONLY_INSTRUCTIONS = (
    "Read-only MCP server for arXiv paper search, via the 'arxiv' PyPI package "
    "wrapping arXiv's public Atom API. No authentication needed; no write tools "
    "exposed.\n\n"
    "DATA MODEL: arXiv is a preprint repository, not a citation database -- there is "
    "no citation graph, no author-disambiguation/metrics system, and no BibTeX "
    "service here (that's INSPIRE-HEP's job, a separate MCP for HEP-specific "
    "bibliographic workflows). What this server gives you: full-text/field search "
    "over paper metadata (title, authors, abstract, categories, dates, links), and "
    "direct lookup by arXiv id.\n\n"
    "QUERY SYNTAX (search_papers' query param, and what title/author/abstract/"
    "category are built from under the hood): arXiv's field-prefixed syntax -- "
    "ti:, au:, abs:, cat:, all: -- combined with AND / OR / ANDNOT, e.g. "
    "'au:\"Feynman\" AND cat:hep-ex'. Category codes look like 'hep-ex', 'hep-ph', "
    "'physics.ins-det', etc. Prefer the named title/author/abstract/category "
    "parameters for a single clean filter each; drop into the raw query param "
    "for anything boolean/compound they can't express directly -- both compose "
    "together (AND-combined) if given at once.\n\n"
    "Typical flow: search_papers(author=..., category=...) or "
    "search_papers(query=...) to find candidates, then get_paper(arxiv_id=...) "
    "for one paper's full metadata once you have its id from a search result.\n\n"
    "PDF/FULL TEXT: no tool here fetches paper content, only metadata -- pdf_url in "
    "results is a plain link, not fetched or embedded. Retrieving it requires a "
    "completely separate capability outside this MCP server entirely (e.g. a web-fetch "
    "tool), and that fetch draws on arXiv's SAME rate limit as this server's own "
    "requests (arXiv enforces it per source IP, not per client/tool) -- fetching many "
    "PDFs back-to-back with no pacing after a search can trip that limit even though "
    "this server's own search/lookup calls stayed within it."
)


def _server_version() -> str:
    """Resolved from the installed package's metadata -- itself derived from
    the nearest git tag by setuptools_scm at build/install time (see
    pyproject.toml). Falls back gracefully for a raw checkout run without an
    install (e.g. `python -m arxiv_mcp.server` against source directly)."""
    try:
        return importlib.metadata.version("arxiv-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("ARXIV_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


_SORT_BY = {
    "relevance": arxiv.SortCriterion.Relevance,
    "last_updated_date": arxiv.SortCriterion.LastUpdatedDate,
    "submitted_date": arxiv.SortCriterion.SubmittedDate,
}

_SORT_ORDER = {
    "ascending": arxiv.SortOrder.Ascending,
    "descending": arxiv.SortOrder.Descending,
}


def _build_query(
    query: str | None,
    title: str | None,
    author: str | None,
    abstract: str | None,
    category: str | None,
) -> str:
    clauses: list[str] = []
    if query:
        clauses.append(f"({query})")
    if title:
        clauses.append(f'ti:"{title}"')
    if author:
        clauses.append(f'au:"{author}"')
    if abstract:
        clauses.append(f'abs:"{abstract}"')
    if category:
        clauses.append(f"cat:{category}")
    return " AND ".join(clauses)


def _result_record(r: arxiv.Result) -> dict[str, Any]:
    return {
        "arxiv_id": r.get_short_id(),
        "entry_id": r.entry_id,
        "title": r.title,
        "authors": [a.name for a in r.authors],
        "summary": r.summary,
        "published_iso_utc": r.published.isoformat() if r.published else None,
        "updated_iso_utc": r.updated.isoformat() if r.updated else None,
        "primary_category": r.primary_category,
        "categories": r.categories,
        "comment": r.comment,
        "journal_ref": r.journal_ref,
        "doi": r.doi,
        "pdf_url": r.pdf_url,
    }


# mcp>=2.0 renamed FastMCP -> MCPServer; created at module scope (registry-mcp /
# dqm-mcp / metacat-mcp pattern) rather than inside a factory function, so tools
# are decorated once at import time and read their runtime config from `_state`
# per call.
mcp = MCPServer("arxiv", instructions=READ_ONLY_INSTRUCTIONS)

# Set once by main() from CLI args (env vars as fallback defaults) before
# mcp.run(); read per-call by the tools below.
_state: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "client": arxiv.Client(
        page_size=DEFAULT_PAGE_SIZE,
        delay_seconds=DEFAULT_DELAY_SECONDS,
        num_retries=DEFAULT_NUM_RETRIES,
    ),
}


@mcp.tool(description="Return server capabilities, version, and rate-limit configuration.")
def get_server_info() -> dict[str, Any]:
    client = _state["client"]
    return {
        "name": "arxiv",
        "version": _server_version(),
        "read_only": True,
        "transport": "streamable-http",
        "rate_limit": {
            "page_size": client.page_size,
            "delay_seconds": client.delay_seconds,
            "num_retries": client.num_retries,
        },
        "defaults": {
            "max_results": DEFAULT_MAX_RESULTS,
        },
        "notes": [
            "Wraps arXiv's public Atom API via the 'arxiv' PyPI package; no auth needed.",
            "No citation graph, author metrics, collaboration filter, or BibTeX here -- "
            "arXiv itself doesn't track those; see the (planned) inspirehep MCP for that.",
            "delay_seconds/num_retries follow arXiv's requested rate-limit etiquette; "
            "not intended to be driven concurrently at high volume.",
        ],
    }


@mcp.tool(
    description=(
        "Search arXiv papers by query and/or named filters (title/author/abstract/"
        "category), sorted by relevance, last-updated, or submission date. Prefer the "
        "named filters for a single clean condition each; use query for arXiv's raw "
        "field-prefixed boolean syntax (ti:/au:/abs:/cat:/all: with AND/OR/ANDNOT) when "
        "you need something compound -- both compose together (AND-combined) if given "
        "at once. At least one of query/title/author/abstract/category is required."
    )
)
def search_papers(
    query: str | None = None,
    title: str | None = None,
    author: str | None = None,
    abstract: str | None = None,
    category: str | None = None,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    if sort_by not in _SORT_BY:
        raise ValueError(f"sort_by must be one of: {sorted(_SORT_BY)}")
    if sort_order not in _SORT_ORDER:
        raise ValueError(f"sort_order must be one of: {sorted(_SORT_ORDER)}")

    max_results = max(1, min(max_results, 200))

    full_query = _build_query(query, title, author, abstract, category)
    if not full_query:
        raise ValueError(
            "At least one of query/title/author/abstract/category is required."
        )

    search = arxiv.Search(
        query=full_query,
        max_results=max_results,
        sort_by=_SORT_BY[sort_by],
        sort_order=_SORT_ORDER[sort_order],
    )

    client = _state["client"]
    results = [_result_record(r) for r in client.results(search)]

    return {
        "applied_filters": {
            "query": query,
            "title": title,
            "author": author,
            "abstract": abstract,
            "category": category,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "max_results": max_results,
        },
        "query_used": full_query,
        "returned": len(results),
        "results": results,
    }


@mcp.tool(
    description=(
        "Get full metadata for one paper by arXiv id (e.g. '2301.12345' or "
        "'2301.12345v2'). Metadata only, no PDF/full-text content and no download -- "
        "the returned pdf_url is a plain link; fetching it requires a separate tool "
        "outside this MCP server entirely, and that fetch shares arXiv's rate limit "
        "with this server's own requests (see server instructions)."
    )
)
def get_paper(arxiv_id: str) -> dict[str, Any]:
    arxiv_id = arxiv_id.strip()
    if not arxiv_id:
        raise ValueError("arxiv_id must not be empty")

    search = arxiv.Search(id_list=[arxiv_id])
    client = _state["client"]
    results = list(client.results(search))

    if not results:
        raise ValueError(f"No paper found for arxiv_id={arxiv_id!r}")

    return _result_record(results[0])


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="arxiv-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("ARXIV_MCP_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ARXIV_MCP_PORT", str(DEFAULT_PORT))),
        help="bind port (default: %(default)s)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=int(os.environ.get("ARXIV_MCP_PAGE_SIZE", str(DEFAULT_PAGE_SIZE))),
        help="arxiv.Client page_size (default: %(default)s)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=float(os.environ.get("ARXIV_MCP_DELAY_SECONDS", str(DEFAULT_DELAY_SECONDS))),
        help="arxiv.Client delay_seconds -- arXiv's requested rate-limit gap between "
        "requests (default: %(default)s)",
    )
    parser.add_argument(
        "--num-retries",
        type=int,
        default=int(os.environ.get("ARXIV_MCP_NUM_RETRIES", str(DEFAULT_NUM_RETRIES))),
        help="arxiv.Client num_retries (default: %(default)s)",
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
    _state["client"] = arxiv.Client(
        page_size=args.page_size,
        delay_seconds=args.delay_seconds,
        num_retries=args.num_retries,
    )

    if args.check:
        print(
            f"OK: arxiv_mcp constructed "
            f"(host={args.host} port={args.port} page_size={args.page_size} "
            f"delay_seconds={args.delay_seconds} num_retries={args.num_retries})"
        )
        return

    LOGGER.info("Starting arxiv MCP server over streamable-http on %s:%s", args.host, args.port)
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
