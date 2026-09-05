"""memory-mcp: persistent project memory for agents, backed by Postgres.

Stores documents keyed by (owner, project, name) and serves them back later --
across sessions, and (the forward-looking case) across ephemeral per-session
sandboxes that keep no disk of their own.

Two things make this server different from the others in this workspace, and
both are deliberate:

1. It WRITES. Every other MCP here is read-only. Writes are append-only all
   the way down: the database role is granted INSERT and SELECT and nothing
   else, so a new version is a new row and a metadata change is a new row.
   Nothing this server can do modifies or deletes existing data. See
   sql/create_tables.sql for the admin-only delete path.

2. mikey auth is MANDATORY, not optional. In ecl-mcp/runs-mcp an unset
   MIKEY_KEYS_FILE just means "no auth"; here it would mean "no owner", and
   the owner IS the access-control boundary -- so the server refuses to start
   without it, and every tool independently fails closed if a request somehow
   arrives with no authenticated identity.

The owner is taken from the mikey key's name and is never a tool parameter.
Postgres is not enforcing per-owner isolation -- the database role is shared
by every caller -- so this process is the only thing standing between one
owner's documents and another's. Every query is built through the helpers
below with `owner` bound; there is deliberately no code path that accepts an
owner from the caller.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import importlib.metadata
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mikey import build_auth_kwargs
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

LOGGER = logging.getLogger("memory_mcp")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8007

DEFAULT_DB_HOST = "ifdb11"
DEFAULT_DB_PORT = 5477
DEFAULT_DB_NAME = "mu2e_ai_prd"
DEFAULT_DB_SCHEMA = "memory"
# Assumed only for the duration of a write transaction. The login role
# (mu2eai) is a member of it, and is granted SELECT directly so that reads
# never need to elevate.
DEFAULT_WRITE_ROLE = "update_role"
DEFAULT_DB_TIMEOUT_SECONDS = 30
DEFAULT_DB_POOL_MAX = 4

# Ceiling on a single stored document. Postgres TEXT would take far more; this
# is about keeping the store useful (and one runaway agent from filling it).
MAX_CONTENT_BYTES = 1_048_576  # 1 MiB

# get_document refuses to return more than this inline, so a naive caller
# cannot blow its own context by asking for a large document without meaning
# to. Same reasoning as runs-mcp's get_config_blob.
DEFAULT_MAX_INLINE_BYTES = 100_000

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_SORT_OPTIONS = {
    "weight": "m.weight DESC NULLS LAST, d.project, d.name",
    "name": "d.project, d.name",
    "created": "d.create_time DESC",
    "updated": "v.create_time DESC",
    "size": "v.content_size DESC",
}

INSTRUCTIONS = (
    "Persistent project-memory store for agents. Documents survive across "
    "sessions, so this is where to put things worth remembering next time: "
    "conclusions, conventions, decisions and their rationale, gotchas, "
    "working notes.\n\n"
    "DATA MODEL: a document is identified by (project, name). The owner is "
    "taken from your authentication key -- it is not a parameter, and you can "
    "only ever see your own documents. Content is markdown by convention but "
    "any text is accepted.\n\n"
    "VERSIONING: put_document on an existing (project, name) creates a new "
    "version; older versions are kept and can be fetched by number via "
    "get_document(version=N). Nothing is ever overwritten or deleted. "
    "mode='replace' stores what you pass; mode='append' adds it to the end of "
    "the current content.\n\n"
    "METADATA (all optional, but worth providing -- it is what makes a "
    "document findable later): keywords, a description written for an agent "
    "to read, an importance weight 0-100, and an expiry date. Updating "
    "metadata carries forward whatever you do not specify.\n\n"
    "TYPICAL FLOW: list_projects to see what exists, list_documents to browse "
    "(returns metadata and SIZE but never content -- check the size before "
    "reading), then get_document for the text. For a document too large to "
    "read comfortably, pass file_path to have it written to disk instead of "
    "returned inline.\n\n"
    "Retiring a document (set_metadata(retired=true)) hides it from listings "
    "but does not delete it; actual deletion requires a database administrator."
)


@dataclass
class _Config:
    db_host: str = os.environ.get("MEMORY_MCP_DB_HOST", DEFAULT_DB_HOST)
    db_port: int = int(os.environ.get("MEMORY_MCP_DB_PORT", str(DEFAULT_DB_PORT)))
    db_name: str = os.environ.get("MEMORY_MCP_DB_NAME", DEFAULT_DB_NAME)
    db_schema: str = os.environ.get("MEMORY_MCP_DB_SCHEMA", DEFAULT_DB_SCHEMA)
    write_role: str = os.environ.get("MEMORY_MCP_WRITE_ROLE", DEFAULT_WRITE_ROLE)
    db_timeout_seconds: int = int(
        os.environ.get("MEMORY_MCP_DB_TIMEOUT_SECONDS", str(DEFAULT_DB_TIMEOUT_SECONDS))
    )
    db_pool_max: int = int(os.environ.get("MEMORY_MCP_DB_POOL_MAX", str(DEFAULT_DB_POOL_MAX)))
    max_content_bytes: int = int(
        os.environ.get("MEMORY_MCP_MAX_CONTENT_BYTES", str(MAX_CONTENT_BYTES))
    )


_config = _Config()


def _server_version() -> str:
    try:
        return importlib.metadata.version("memory-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


mcp = MCPServer("memory", instructions=INSTRUCTIONS, **build_auth_kwargs())


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _owner() -> str:
    """The authenticated caller's mikey name, which is the document owner.

    Fails closed: if the server was somehow started without auth, or a request
    arrives without an identity, there is no owner and therefore no namespace
    to operate on -- so every tool errors rather than falling back to anything.
    """
    token = get_access_token()
    if token is None or not token.client_id:
        raise ToolError(
            "no authenticated identity -- memory-mcp requires a mikey bearer token, "
            "because the token's name is the document owner. Check that this server "
            "was started with MIKEY_KEYS_FILE set and that your client sends an "
            "Authorization: Bearer header."
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
    untouched for hours between calls.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            conninfo = psycopg.conninfo.make_conninfo(
                host=_config.db_host,
                port=_config.db_port,
                dbname=_config.db_name,
                application_name="memory-mcp",
                connect_timeout=_config.db_timeout_seconds,
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
    """Cursor for reads, running as the plain login role (mu2eai).

    Deliberately does NOT assume the write role. The login role is granted
    SELECT directly, so reads work without elevation -- and an accidental
    INSERT from a code path that was only meant to read fails at the database
    rather than silently succeeding. In an append-only store where a stray
    write is permanent and cannot be deleted, that is worth the extra grant.
    """
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


@contextlib.contextmanager
def _write_cursor():
    """Cursor for writes: a transaction, with the write role assumed for it.

    The transaction serves two purposes. Atomicity: a put_document may insert
    into documents, versions and metadata, and those must land together or not
    at all. And scope: `SET LOCAL ROLE` (not plain `SET ROLE`) means the
    elevation reverts on commit or rollback, so it cannot leak to whichever
    request picks up this pooled connection next.
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
# Helpers
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
            LOGGER.exception("memory-mcp tool %s failed (database)", fn.__name__)
            raise ToolError(f"database_error: {type(e).__name__}: {e}") from e
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            LOGGER.exception("memory-mcp tool %s failed", fn.__name__)
            raise ToolError(f"tool_failed: {type(e).__name__}: {e}") from e

    return inner


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _row(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in record.items()}


def _require(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required and cannot be empty")
    return text


def _parse_keywords(raw: str | None) -> list[str] | None:
    """Comma-separated -> normalized list. Lower-cased so that matching is
    case-insensitive; '' means 'clear', None means 'leave unchanged'."""
    if raw is None:
        return None
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _parse_time(raw: str | None, field: str) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{field}: could not parse {raw!r} -- use ISO 8601, e.g. '2026-12-31' "
            "or '2026-12-31T17:00:00-06:00'"
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _content_size(content: str) -> int:
    return len(content.encode("utf-8"))


def _resolve_doc_id(cur, owner: str, project: str, name: str) -> int:
    row = cur.execute(
        _q("SELECT doc_id FROM {s}.documents WHERE owner=%s AND project=%s AND name=%s"),
        (owner, project, name),
    ).fetchone()
    if row is None:
        raise ToolError(
            f"not_found: no document {project!r}/{name!r} for owner {owner!r} "
            "(use list_documents to see what exists)"
        )
    return row["doc_id"]


def _latest_metadata(cur, doc_id: int) -> dict[str, Any] | None:
    return cur.execute(
        _q(
            "SELECT keywords, description, weight, expires, retired "
            "FROM {s}.metadata WHERE doc_id=%s ORDER BY metadata_id DESC LIMIT 1"
        ),
        (doc_id,),
    ).fetchone()


def _insert_metadata(
    cur,
    doc_id: int,
    keywords: list[str] | None,
    description: str | None,
    weight: int | None,
    expires: datetime | None,
    retired: bool | None,
    *,
    clear_expires: bool,
) -> dict[str, Any]:
    """Insert a merged metadata row: anything not specified is carried forward
    from the current metadata, so a partial update does not blank the rest."""
    current = _latest_metadata(cur, doc_id) or {}
    merged = {
        "keywords": current.get("keywords") if keywords is None else keywords,
        "description": current.get("description") if description is None else description,
        "weight": current.get("weight") if weight is None else weight,
        "expires": None if clear_expires else (current.get("expires") if expires is None else expires),
        "retired": bool(current.get("retired", False)) if retired is None else retired,
    }
    row = cur.execute(
        _q(
            "INSERT INTO {s}.metadata (doc_id, keywords, description, weight, expires, retired) "
            "VALUES (%(doc_id)s, %(keywords)s, %(description)s, %(weight)s, %(expires)s, %(retired)s) "
            "RETURNING keywords, description, weight, expires, retired, create_time"
        ),
        {"doc_id": doc_id, **merged},
    ).fetchone()
    return _row(row)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(description="Get memory-mcp configuration, version, and the calling owner.")
@_wrap
def get_server_info() -> dict[str, Any]:
    return {
        "name": "memory",
        "version": _server_version(),
        "transport": "streamable-http",
        "auth": "mikey" if mcp.settings.auth else "disabled",
        "owner": _owner(),
        "database": f"{_config.db_host}:{_config.db_port}/{_config.db_name}",
        "schema": _config.db_schema,
        "max_content_bytes": _config.max_content_bytes,
        "default_max_inline_bytes": DEFAULT_MAX_INLINE_BYTES,
    }


@mcp.tool(name="list_projects")
@_wrap
def list_projects() -> list[str]:
    """List the project names you have documents under.

    Cheap discovery entry point -- start here when you don't know what is
    stored yet.
    """
    owner = _owner()
    with _read_cursor() as cur:
        rows = cur.execute(
            _q("SELECT DISTINCT project FROM {s}.documents WHERE owner=%s ORDER BY project"),
            (owner,),
        ).fetchall()
    return [r["project"] for r in rows]


@mcp.tool(name="list_documents")
@_wrap
def list_documents(
    project: str = "",
    name_contains: str = "",
    keywords: str = "",
    description_contains: str = "",
    min_weight: int | None = None,
    created_after: str = "",
    updated_after: str = "",
    include_expired: bool = False,
    include_retired: bool = False,
    sort_by: str = "weight",
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Browse your documents. Returns metadata and size, never content.

    This is the "decide what is worth reading" call: check size_bytes before
    calling get_document, so a large document does not land in your context
    unintentionally.

    Args:
        project: restrict to one project (empty = all your projects)
        name_contains: case-insensitive substring match on the document name
        keywords: comma-separated; matches a document carrying ANY of them
        description_contains: case-insensitive substring match on the description
        min_weight: only documents with importance weight >= this (0-100)
        created_after: ISO date/time -- when the document was first created
        updated_after: ISO date/time -- when its latest version was written
        include_expired: include documents past their expiry date
        include_retired: include documents that have been retired
        sort_by: weight (default), name, created, updated, or size
        limit: max rows (default 50, capped at 500)

    Each row: project, name, latest_version, size_bytes, created, updated,
    description, keywords, weight, expires, retired.
    """
    owner = _owner()
    if sort_by not in _SORT_OPTIONS:
        raise ValueError(f"sort_by must be one of {sorted(_SORT_OPTIONS)}, got {sort_by!r}")
    limit = max(1, min(limit, MAX_LIMIT))

    clauses = ["d.owner = %(owner)s"]
    params: dict[str, Any] = {"owner": owner, "limit": limit}

    if project.strip():
        clauses.append("d.project = %(project)s")
        params["project"] = project.strip()
    if name_contains.strip():
        clauses.append("d.name ILIKE %(name_like)s")
        params["name_like"] = f"%{name_contains.strip()}%"
    if keywords.strip():
        clauses.append("m.keywords && %(keywords)s::text[]")
        params["keywords"] = _parse_keywords(keywords)
    if description_contains.strip():
        clauses.append("m.description ILIKE %(desc_like)s")
        params["desc_like"] = f"%{description_contains.strip()}%"
    if min_weight is not None:
        clauses.append("m.weight >= %(min_weight)s")
        params["min_weight"] = min_weight
    if created_after.strip():
        clauses.append("d.create_time >= %(created_after)s")
        params["created_after"] = _parse_time(created_after, "created_after")
    if updated_after.strip():
        clauses.append("v.create_time >= %(updated_after)s")
        params["updated_after"] = _parse_time(updated_after, "updated_after")
    if not include_expired:
        clauses.append("(m.expires IS NULL OR m.expires > now())")
    if not include_retired:
        clauses.append("COALESCE(m.retired, false) = false")

    query = _q(
        "SELECT d.project, d.name, d.create_time AS created, "
        "       v.version AS latest_version, v.content_size AS size_bytes, "
        "       v.create_time AS updated, "
        "       m.keywords, m.description, m.weight, m.expires, "
        "       COALESCE(m.retired, false) AS retired "
        "  FROM {s}.documents d "
        "  JOIN {s}.current_versions v ON v.doc_id = d.doc_id "
        "  LEFT JOIN {s}.current_metadata m ON m.doc_id = d.doc_id "
        " WHERE " + " AND ".join(clauses) + " "
        " ORDER BY " + _SORT_OPTIONS[sort_by] + " "
        " LIMIT %(limit)s"
    )

    with _read_cursor() as cur:
        rows = cur.execute(query, params).fetchall()
    return [_row(r) for r in rows]


@mcp.tool(name="list_versions")
@_wrap
def list_versions(project: str, name: str) -> list[dict[str, Any]]:
    """List every stored version of one document, newest first.

    Returns version, size_bytes and create_time per version. Fetch a specific
    one with get_document(version=N).
    """
    owner = _owner()
    project = _require(project, "project")
    name = _require(name, "name")
    with _read_cursor() as cur:
        doc_id = _resolve_doc_id(cur, owner, project, name)
        rows = cur.execute(
            _q(
                "SELECT version, content_size AS size_bytes, create_time "
                "FROM {s}.versions WHERE doc_id=%s ORDER BY version DESC"
            ),
            (doc_id,),
        ).fetchall()
    return [_row(r) for r in rows]


@mcp.tool(name="get_document")
@_wrap
def get_document(
    project: str,
    name: str,
    version: int | None = None,
    file_path: str = "",
    overwrite: bool = False,
    max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES,
) -> dict[str, Any]:
    """Fetch one document's text.

    Args:
        project, name: the document to fetch
        version: a specific version number; omit for the latest
        file_path: if given, the content is written to this path on the server
                   host instead of being returned inline. Only useful if you
                   can read that path -- give somewhere on shared cluster
                   storage, or a scratch directory you control. The parent
                   directory must already exist.
        overwrite: allow file_path to replace an existing file
        max_inline_bytes: refuse to return more than this inline (default
                   100000). A document larger than this errors with its size,
                   so you can decide to raise the limit or use file_path.

    Returns project, name, version, size_bytes, create_time and either
    content (inline) or file_path plus bytes_written.
    """
    owner = _owner()
    project = _require(project, "project")
    name = _require(name, "name")

    with _read_cursor() as cur:
        doc_id = _resolve_doc_id(cur, owner, project, name)
        if version is None:
            row = cur.execute(
                _q(
                    "SELECT version, content, content_size, create_time FROM {s}.versions "
                    "WHERE doc_id=%s ORDER BY version DESC LIMIT 1"
                ),
                (doc_id,),
            ).fetchone()
        else:
            row = cur.execute(
                _q(
                    "SELECT version, content, content_size, create_time FROM {s}.versions "
                    "WHERE doc_id=%s AND version=%s"
                ),
                (doc_id, version),
            ).fetchone()
            if row is None:
                raise ToolError(
                    f"not_found: {project}/{name} has no version {version} "
                    "(use list_versions to see which exist)"
                )

    if row is None:
        raise ToolError(f"not_found: {project}/{name} has no versions stored")

    result: dict[str, Any] = {
        "project": project,
        "name": name,
        "version": row["version"],
        "size_bytes": row["content_size"],
        "create_time": _iso(row["create_time"]),
    }

    if file_path.strip():
        target = Path(file_path.strip()).expanduser()
        if target.exists() and not overwrite:
            raise ToolError(
                f"refusing to overwrite existing file {target} -- pass overwrite=true "
                "if that is what you want"
            )
        if not target.parent.is_dir():
            raise ToolError(
                f"parent directory {target.parent} does not exist -- this server will not "
                "create directory trees; choose an existing directory"
            )
        target.write_text(row["content"], encoding="utf-8")
        result["file_path"] = str(target)
        result["bytes_written"] = row["content_size"]
        result["content"] = None
        return result

    if row["content_size"] > max_inline_bytes:
        raise ToolError(
            f"document is {row['content_size']} bytes, over max_inline_bytes="
            f"{max_inline_bytes}. Re-call with file_path=... to write it to disk, "
            "or raise max_inline_bytes if you really want it inline."
        )

    result["content"] = row["content"]
    return result


@mcp.tool(name="put_document")
@_wrap
def put_document(
    project: str,
    name: str,
    content: str,
    mode: str = "replace",
    keywords: str | None = None,
    description: str | None = None,
    weight: int | None = None,
    expires: str | None = None,
) -> dict[str, Any]:
    """Store a document, creating it or adding a new version.

    The document is owned by whoever your key identifies; (project, name) is
    the key within that ownership. Every call creates a new version -- earlier
    versions stay retrievable by number and are never overwritten.

    Args:
        project: grouping name, your choice (see list_projects)
        name: document name, unique within the project
        content: the text. Markdown by convention; any text is fine.
        mode: 'replace' (default) stores content as the new version;
              'append' adds it to the end of the current content, inserting a
              newline first if the existing content does not end with one.
        keywords: comma-separated, stored lower-cased
        description: a short description written for an agent to read later --
              this is the main thing that makes a document findable
        weight: importance 0-100, used for default listing order
        expires: ISO date/time after which the document drops out of listings

    Metadata arguments are optional and merge with existing metadata: anything
    you omit is carried forward, not blanked.
    """
    owner = _owner()
    project = _require(project, "project")
    name = _require(name, "name")
    if mode not in ("replace", "append"):
        raise ValueError(f"mode must be 'replace' or 'append', got {mode!r}")
    if weight is not None and not 0 <= weight <= 100:
        raise ValueError(f"weight must be between 0 and 100, got {weight}")
    expires_at = _parse_time(expires, "expires")
    keyword_list = _parse_keywords(keywords)

    if mode == "replace" and _content_size(content) > _config.max_content_bytes:
        raise ValueError(
            f"content is {_content_size(content)} bytes, over the "
            f"{_config.max_content_bytes} byte limit"
        )

    has_metadata = any(x is not None for x in (keyword_list, description, weight, expires_at))

    # The version number is computed in the INSERT itself and guarded by
    # UNIQUE(doc_id, version); a concurrent writer loses the race with a unique
    # violation and we simply redo the whole write. Deliberately avoids
    # SELECT ... FOR UPDATE, which an insert-only role may not be able to use.
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with _write_cursor() as cur:
                row = cur.execute(
                    _q(
                        "INSERT INTO {s}.documents (owner, project, name) VALUES (%s,%s,%s) "
                        "ON CONFLICT (owner, project, name) DO NOTHING RETURNING doc_id"
                    ),
                    (owner, project, name),
                ).fetchone()
                if row is None:
                    doc_id = _resolve_doc_id(cur, owner, project, name)
                else:
                    doc_id = row["doc_id"]

                new_content = content
                if mode == "append":
                    previous = cur.execute(
                        _q(
                            "SELECT content FROM {s}.versions WHERE doc_id=%s "
                            "ORDER BY version DESC LIMIT 1"
                        ),
                        (doc_id,),
                    ).fetchone()
                    if previous is not None:
                        base = previous["content"]
                        separator = "" if not base or base.endswith("\n") else "\n"
                        new_content = base + separator + content
                    size = _content_size(new_content)
                    if size > _config.max_content_bytes:
                        raise ValueError(
                            f"appending would make the document {size} bytes, over the "
                            f"{_config.max_content_bytes} byte limit"
                        )

                size = _content_size(new_content)
                created = cur.execute(
                    _q(
                        "INSERT INTO {s}.versions (doc_id, version, content, content_size) "
                        "SELECT %(doc_id)s, COALESCE(MAX(version), 0) + 1, "
                        "       %(content)s, %(size)s "
                        "  FROM {s}.versions WHERE doc_id = %(doc_id)s "
                        "RETURNING version, create_time"
                    ),
                    {"doc_id": doc_id, "content": new_content, "size": size},
                ).fetchone()

                result = {
                    "project": project,
                    "name": name,
                    "version": created["version"],
                    "size_bytes": size,
                    "mode": mode,
                    "create_time": _iso(created["create_time"]),
                }
                if has_metadata:
                    result["metadata"] = _insert_metadata(
                        cur,
                        doc_id,
                        keyword_list,
                        description,
                        weight,
                        expires_at,
                        None,
                        clear_expires=False,
                    )
                return result
        except psycopg.errors.UniqueViolation as e:  # concurrent writer; retry
            last_error = e
            continue

    raise ToolError(f"write_conflict: could not allocate a version number: {last_error}")


@mcp.tool(name="set_metadata")
@_wrap
def set_metadata(
    project: str,
    name: str,
    keywords: str | None = None,
    description: str | None = None,
    weight: int | None = None,
    expires: str | None = None,
    retired: bool | None = None,
) -> dict[str, Any]:
    """Update a document's metadata without storing a new version.

    Only the fields you pass change; everything else is carried forward from
    the current metadata. Pass an empty string to clear a text field (for
    example expires="" removes the expiry). The previous metadata is archived,
    not overwritten.

    retired=true hides the document from listings without deleting it -- the
    closest thing to deletion available, since this server cannot delete.
    """
    owner = _owner()
    project = _require(project, "project")
    name = _require(name, "name")
    if weight is not None and not 0 <= weight <= 100:
        raise ValueError(f"weight must be between 0 and 100, got {weight}")
    if all(x is None for x in (keywords, description, weight, expires, retired)):
        raise ValueError("nothing to update -- pass at least one metadata field")

    clear_expires = expires is not None and not expires.strip()
    expires_at = _parse_time(expires, "expires")

    with _write_cursor() as cur:
        doc_id = _resolve_doc_id(cur, owner, project, name)
        metadata = _insert_metadata(
            cur,
            doc_id,
            _parse_keywords(keywords),
            description,
            weight,
            expires_at,
            retired,
            clear_expires=clear_expires,
        )
    return {"project": project, "name": name, "metadata": metadata}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="memory-mcp", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("MEMORY_MCP_HOST", DEFAULT_HOST),
                        help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MEMORY_MCP_PORT", str(DEFAULT_PORT))),
                        help="bind port (default: %(default)s)")
    parser.add_argument("--db-host", default=_config.db_host,
                        help="Postgres host (default: %(default)s)")
    parser.add_argument("--db-port", type=int, default=_config.db_port,
                        help="Postgres port (default: %(default)s)")
    parser.add_argument("--db-name", default=_config.db_name,
                        help="Postgres database (default: %(default)s)")
    parser.add_argument("--db-schema", default=_config.db_schema,
                        help="schema holding the memory tables (default: %(default)s)")
    parser.add_argument("--write-role", default=_config.write_role,
                        help="role assumed for the duration of a write transaction; "
                             "reads run as the login role (default: %(default)s)")
    parser.add_argument("--max-content-bytes", type=int, default=_config.max_content_bytes,
                        help="largest document accepted by put_document (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help=(
                            "verify configuration and exit -- does not bind a socket. "
                            "Makes a real database connection, confirms the schema is "
                            "present and that the write role can be assumed, and "
                            "requires MIKEY_KEYS_FILE to be set."
                        ))
    return parser.parse_args(argv)


def _apply_args(args: argparse.Namespace) -> None:
    _config.db_host = args.db_host
    _config.db_port = args.db_port
    _config.db_name = args.db_name
    _config.db_schema = args.db_schema
    _config.write_role = args.write_role
    _config.max_content_bytes = args.max_content_bytes


def _preflight() -> None:
    """Fail loudly on the things that make this server useless if wrong."""
    if not mcp.settings.auth:
        raise RuntimeError(
            "MIKEY_KEYS_FILE is not set. memory-mcp requires mikey auth: the key's "
            "name is the document owner, which is the only thing separating one "
            "owner's documents from another's. Refusing to start without it."
        )
    # The read path and the write path have different privileges, so check
    # them separately -- either one being misconfigured breaks the server in a
    # way that would otherwise only show up on a live call.
    with _read_cursor() as cur:
        login_role = cur.execute("SELECT session_user AS r").fetchone()["r"]
        found = cur.execute(
            "SELECT to_regclass(%s) AS t", (f"{_config.db_schema}.documents",)
        ).fetchone()
        if found["t"] is None:
            raise RuntimeError(
                f"schema {_config.db_schema!r} has no 'documents' table in "
                f"{_config.db_name} -- has sql/create_tables.sql been run?"
            )
        # Actually read, rather than just confirming the table exists:
        # to_regclass succeeds even with no SELECT privilege, so this is what
        # proves the login role's read grant is in place.
        try:
            cur.execute(_q("SELECT 1 FROM {s}.documents LIMIT 1"))
        except psycopg.errors.InsufficientPrivilege as e:
            raise RuntimeError(
                f"login role {login_role!r} cannot SELECT from {_config.db_schema}.documents. "
                f"Reads deliberately run unelevated, so it needs the grant directly:\n"
                f"  GRANT USAGE ON SCHEMA {_config.db_schema} TO {login_role};\n"
                f"  GRANT SELECT ON ALL TABLES IN SCHEMA {_config.db_schema} TO {login_role};"
            ) from e

    # Confirm the write role can be assumed, in a transaction forced to roll
    # back -- so --check verifies the write path without writing anything.
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
        "database ok: %s:%s/%s schema=%s, login=%s, write_role=%s",
        _config.db_host, _config.db_port, _config.db_name,
        _config.db_schema, login_role, _config.write_role,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("MEMORY_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    _apply_args(args)

    try:
        _preflight()
    except Exception as e:  # noqa: BLE001 -- report cleanly, never a traceback
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print(
            f"OK: memory_mcp constructed (host={args.host} port={args.port} "
            f"db={_config.db_host}:{_config.db_port}/{_config.db_name} "
            f"schema={_config.db_schema} write_role={_config.write_role} auth=mikey)"
        )
        return

    LOGGER.info(
        "Starting memory MCP server over streamable-http on %s:%s (db=%s:%s/%s, auth=mikey)",
        args.host, args.port, _config.db_host, _config.db_port, _config.db_name,
    )
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
