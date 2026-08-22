# inspirehep-mcp

Read-only streamable-HTTP MCP server for INSPIRE-HEP literature/author
search. **An independent implementation against INSPIRE-HEP's public REST
API** (`https://inspirehep.net/api`) -- written from scratch against that
API and the published feature list of a third-party `inspirehep-mcp` PyPI
package (AGPL-3.0, stdio-only transport), not derived from or containing any
of that package's code. It exists to give this workspace the same
capability under this workspace's own license (Apache-2.0, matching
`aitools`) and its own deployment pattern (uv-installed, systemd
`--user`-run, streamable-HTTP -- same as `../registry`, `../dqm`,
`../metacat`, `../arxiv`).

## Scope

- Read-only operations only -- no write tools exposed
- No authentication -- INSPIRE-HEP's search/record API is fully open
- Streamable-HTTP transport
- Retains the same tool *coverage* as the reference package (search, paper
  details, author papers, citations, collaboration search, references,
  BibTeX, figures, stats) -- see Notes for the one tool that's honestly
  best-effort rather than full parity (`get_paper_figures`)

## Exposed MCP tools

- `search_papers(query, title, author, collaboration, category, sort, max_results)`
- `get_paper_details(identifier)` -- by INSPIRE recid, DOI, or arXiv id
- `get_author_papers(author_name, sort, max_results)` -- literature search
  scoped to one author name (not an author-profile lookup)
- `get_citations(identifier, sort, max_results)` -- papers that cite this one
- `search_by_collaboration(collaboration, query, sort, max_results)`
- `get_references(identifier)` -- what this paper itself cites
- `get_bibtex(identifier, format)` -- BibTeX/LaTeX text
- `get_paper_figures(identifier)` -- **best-effort only**, see Notes
- `server_stats()` -- cache hit-rate / request-count counters
- `get_server_info()` -- version, rate-limit, cache configuration

## How the underlying API actually works (verified while building this)

Researched directly against `https://inspirehep.net/api` (one request at a
time, spaced out, not a burst -- see the rate-limit note below for why that
mattered while building the `arxiv` MCP):

- Single record: `GET /literature/{recid-or-doi-or-arxiv-id}` -- the
  identifier resolver accepts a bare INSPIRE recid, or a DOI/arXiv id
  directly, confirmed against a real record.
- Search: `GET /literature?q=<query>&sort=mostrecent|mostcited&size=<n>` --
  `q` accepts Elasticsearch-syntax field queries (`field.path:"value"`,
  `AND`/`OR`).
- Citations (who cites a paper): `q=refersto:recid:<recid>` against
  `/literature` -- this is literally what INSPIRE's own record `links.citations`
  field points at, not a guess.
- References (what a paper cites): included inline in the record's own
  `metadata.references` array -- no extra request needed.
- Author papers: `q=authors.full_name:"Last, First"` against `/literature`
  -- confirmed working (a bare `a <BAI>` legacy-syntax attempt did not, so
  this server uses the field-path form).
- Collaboration filter: `q=collaboration:<name>` -- confirmed against a real
  9,791-hit ATLAS query.
- BibTeX/LaTeX: `GET /literature/{recid}?format=bibtex` (also `latex-eu`,
  `latex-us`) -- these formats are advertised directly in each record's own
  `links` field.
- **Figures**: no dedicated endpoint and no figures field was present on the
  real record inspected while building this. `get_paper_figures` here
  returns whatever's in a record's `documents`/`figures` metadata (commonly
  empty) with an explicit warning -- this is the one tool where "retain all
  the functionality of the reference package" isn't achievable against the
  public API as documented; it's included for interface parity, not full
  capability parity.

## Rate limiting

INSPIRE-HEP's documented limit (`https://github.com/inspirehep/rest-api-doc`):
**15 requests per 5 seconds per source IP**, `429` on exceed. Verified live
while building this: `access-control-expose-headers` advertises
`X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` as
CORS-exposable, but none of those headers were actually present on any real
response captured -- so this server cannot do adaptive/observed-remaining
pacing, only fixed self-imposed pacing (`--requests-per-second`, default
`2.0`, comfortably under the 15/5s≈3/s ceiling with margin) plus an
in-process TTL cache (`--cache-ttl-seconds`, default 1h) to cut down on
repeat-query traffic. Both are the same conservative-default philosophy as
`arxiv-mcp`'s `delay_seconds`.

**Same caveat as `arxiv-mcp`, and worth repeating because it's the thing
that actually bit us there**: this pacing only covers requests made
*through this server*. It cannot protect against other traffic sharing the
same outbound IP -- another process on the same box/account hitting INSPIRE
directly, or ad hoc `curl`/debugging against `inspirehep.net/api` while this
service is also running, draws from the same 15/5s budget. If a search or
lookup is taking unexpectedly long or erroring, suspect this before
assuming the server or INSPIRE-HEP itself is broken.

## How it's installed and started

Same shape as the other four servers: a real (non-editable) `uv pip install`
produces `bin/inspirehep-mcp` (entry point), `bin/inspirehep-mcp.sh`
(no-op-setup wrapper), and `bin/inspirehep-mcp-install-unit.sh`
(renders + links the systemd `--user` unit). No source tree gets copied;
`install.sh` is a convenience wrapper around `uv venv` + `uv pip install
"inspirehep-mcp @ git+...#subdirectory=mcp/inspirehep"`, not a requirement.

## Local dev run

```bash
mu2einit
slc uv
cd aitools/mcp/inspirehep
uv venv
uv pip install -e .
.venv/bin/inspirehep-mcp --host=127.0.0.1 --port=8004
```

Startup compatibility check (imports/constructs only, no bind/listen):

```bash
.venv/bin/inspirehep-mcp.sh --check
```

Smoke test against a running server (MCP handshake + tool calls, including
a live search against INSPIRE-HEP):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8004
```

## Config (CLI flags / env vars)

| Flag | Env var | Default |
|---|---|---|
| `--host` | `INSPIREHEP_MCP_HOST` | `0.0.0.0` |
| `--port` | `INSPIREHEP_MCP_PORT` | `8004` |
| `--base-url` | `INSPIREHEP_MCP_BASE_URL` | `https://inspirehep.net/api` |
| `--requests-per-second` | `INSPIREHEP_MCP_REQUESTS_PER_SECOND` | `2.0` |
| `--cache-ttl-seconds` | `INSPIREHEP_MCP_CACHE_TTL_SECONDS` | `3600` |
| `--cache-max-entries` | `INSPIREHEP_MCP_CACHE_MAX_ENTRIES` | `512` |
| `--timeout-seconds` | `INSPIREHEP_MCP_TIMEOUT_SECONDS` | `30` |

The cache is in-process only (a plain dict with TTL + oldest-evict cap) --
not persistent across restarts, unlike the reference package's optional
SQLite cache. `server_stats()` reports current hit-rate/entry counters.

`INSPIREHEP_MCP_LOG_LEVEL` (default `INFO`) controls log verbosity.

## Server-account install

```bash
cd aitools/mcp/inspirehep
./scripts/install.sh /path/to/deploy/inspirehep v0.4.0
```

Then:

```bash
/path/to/deploy/inspirehep/current/.venv/bin/inspirehep-mcp.sh --check
/path/to/deploy/inspirehep/current/.venv/bin/inspirehep-mcp-install-unit.sh --port 8004
```

Requires linger enabled once per account so the service survives logout:

```bash
loginctl enable-linger        # or: sudo loginctl enable-linger <account>
```

## Client config

```json
{
  "mcpServers": {
    "inspirehep": { "url": "http://<host>:8004/mcp" }
  }
}
```

Register in `../registry/config/ports.json` once deployed.

## Notes

- Port convention for this workspace: `registry`=8000, `dqm`=8001,
  `metacat`=8002, `arxiv`=8003, `inspirehep`=8004.
- Reaches `inspirehep.net` over the public internet -- same egress
  consideration as `arxiv-mcp` (confirm outbound access from wherever this
  is deployed).
- `get_paper_figures` is the one tool that doesn't achieve full parity with
  the reference package -- see "How the underlying API actually works"
  above. Everything else here covers the same ground the reference
  package's tool list does, against the same underlying data source.
- Version comes from the nearest git tag via `setuptools_scm`, same as the
  other four -- see `../registry/pyproject.toml`'s comment.
