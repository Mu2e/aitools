"""ecl-mcp: streamable-HTTP MCP server for the Fermilab Electronic
Collaboration Logbook (ECL) -- read-only by default, mikey-authenticated.

Reuses ecl-api's ``ECL``/``ECLEntry`` client (github.com/Mu2e/ecl-api, MIT)
for the actual XML/REST protocol work -- signature scheme, active-server
redirect resolution, XML parsing, category/tag/form sampling. This package
only rebuilds the MCP tool layer on top of it. ecl-api ships its own
bundled MCP server too, but it targets the pre-2.0 `mcp.server.fastmcp`
API, which `mcp>=2.0.0` (pinned here, same as every other server in this
workspace) removed outright -- confirmed by direct import
(`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`), not assumed.
So the tool logic below is a port, not a reuse, of ecl-api's mcp/ subpackage.

This is the first server in the workspace to carry mikey auth from the
start rather than having it added later -- see AUTHPLAN.md.
"""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import logging
import os
import sys
import threading
from typing import Any

from ecl_api import ECL, ECLEntry
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mikey import build_auth_kwargs

LOGGER = logging.getLogger("ecl_mcp")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8005


def _server_version() -> str:
    """Resolved from the installed package's metadata -- itself derived from
    the nearest git tag by setuptools_scm at build/install time (see
    pyproject.toml). Falls back gracefully for a raw checkout run without an
    install."""
    try:
        return importlib.metadata.version("ecl-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


def _read_only_from_env(default: bool = True) -> bool:
    raw = os.environ.get("ECL_MCP_READ_ONLY")
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


READ_ONLY_INSTRUCTIONS = (
    "Read-only MCP server for the Fermilab Electronic Collaboration Logbook "
    "(ECL) -- search, retrieve, and browse entries in one experiment's "
    "logbook (which experiment is fixed by this deployment's ECL_URL, not a "
    "tool argument).\n\n"
    "DATA MODEL: entries are free-form logbook posts, each with a category "
    "(e.g. 'Shift', 'Purity Monitors'), optional tags, an optional form "
    "(structured fields beyond the free-text body), an author, and a "
    "timestamp. The ECL API doesn't expose category/tag/form catalogs "
    "directly -- list_categories/list_tags/list_forms sample the most "
    "recent entries (default 500) and return the unique values seen, so "
    "rarely-used values may not appear.\n\n"
    "Typical flow: list_categories/list_tags/list_forms to see what exists, "
    "then search_entries with whatever filters narrow it down, then "
    "get_entry for one entry's full detail by id. search_entry_ids is a "
    "cheaper variant of search_entries when only ids/counts are needed."
)

WRITE_INSTRUCTIONS = READ_ONLY_INSTRUCTIONS + (
    "\n\npost_entry is also available: it takes do_post (default False) so "
    "an entry can be prepared and inspected before actually being written -- "
    "there is no undo once do_post=True is used."
)

_read_only = _read_only_from_env()

# Constructed at module import time (needed so @mcp.tool() below registers
# before main() runs) -- build_auth_kwargs() reads MIKEY_KEYS_FILE from the
# environment and returns {} (auth disabled) if it's unset. See mikey's
# README for why this has to be an env var rather than a --flag.
mcp = MCPServer(
    "ecl",
    instructions=WRITE_INSTRUCTIONS if not _read_only else READ_ONLY_INSTRUCTIONS,
    **build_auth_kwargs(),
)

_ecl_lock = threading.Lock()
_ecl_instance: ECL | None = None


def _get_ecl() -> ECL:
    """Process-wide ECL client, built lazily from ECL_URL / ECL_USER_NAME /
    ECL_PASSWORD env vars on first use -- never CLI flags, so credentials
    never show up in `ps` or get baked into a rendered systemd unit's
    ExecStart line. Caching preserves ECL's own category/tag/form sampling
    cache across tool calls."""
    global _ecl_instance
    with _ecl_lock:
        if _ecl_instance is None:
            _ecl_instance = ECL(as_json=True)
        return _ecl_instance


def _wrap(fn):
    """Convert exceptions raised inside a tool into a readable ToolError.

    Originally this returned a plain {"error": "<code>", "message": ...}
    dict instead of raising. That's broken for any tool annotated to
    return a list (ecl_search_entries, ecl_search_entry_ids,
    ecl_list_categories/tags/forms -- i.e. every read tool except
    ecl_get_entry): the SDK converts a successful return value against
    the tool's declared output schema *after* the function returns, and a
    dict doesn't satisfy a list[...]-typed schema -- confirmed live
    (unreachable ECL_URL triggering this exact path), the conversion
    failure gets classified as an unexpected crash, and per the SDK's own
    design that deliberately withholds the real message from the caller
    ("Error executing tool <name>", nothing else). Raising ToolError
    instead sidesteps output conversion entirely and is exactly the
    mechanism the SDK provides for "a failure you saw coming": the
    message reaches the caller as an is_error=True result, logged
    server-side at INFO without a traceback -- so the LOGGER.exception
    call below is kept, to still get a full traceback in this server's
    own logs for the genuinely unexpected case.
    """

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(f"bad_request: {e}") from e
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            LOGGER.exception("ecl-mcp tool %s failed", fn.__name__)
            raise ToolError(f"tool_failed: {type(e).__name__}: {e}") from e

    return inner


@mcp.tool(description="Get ecl-mcp configuration and version info.")
def get_server_info() -> dict[str, Any]:
    return {
        "name": "ecl",
        "version": _server_version(),
        "transport": "streamable-http",
        "read_only": _read_only,
        "auth": "mikey" if mcp.settings.auth else "disabled",
    }


@mcp.tool(name="ecl_search_entries")
@_wrap
def search_entries(
    category: str = "",
    after: str = "",
    before: str = "",
    form_name: str = "",
    tag: str = "",
    username: str = "",
    substring: str = "",
    words: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search recent ECL entries with optional filters.

    Args:
        category: restrict to this category (use list_categories for valid values)
        after: lower time bound. "<n>days", "<n>hours", "<n>minutes",
               or "yyyy-mm-dd+hh:mm:ss".
        before: upper time bound, same formats as after.
        form_name: restrict to this form
        tag: restrict to entries with this tag
        username: restrict to this author
        substring: free-text substring search (slow -- no index)
        words: indexed full-text search
        limit: max number of entries to return (default 50)

    Returns a list of entry dicts. Each dict has at least: id (int), author,
    subject, category, timestamp, form, tags (list[str]), text, and may have
    fields (dict) when the entry's form has extra fields.
    """
    return _get_ecl().search(
        category=category,
        after=after,
        before=before,
        form_name=form_name,
        tag=tag,
        username=username,
        substring=substring,
        words=words,
        limit=limit,
    )


@mcp.tool(name="ecl_search_entry_ids")
@_wrap
def search_entry_ids(
    category: str = "",
    after: str = "",
    before: str = "",
    form_name: str = "",
    tag: str = "",
    username: str = "",
    substring: str = "",
    words: str = "",
    limit: int = 200,
) -> list[int]:
    """Same filters as ecl_search_entries but returns only entry IDs.

    Cheaper than ecl_search_entries when you only need IDs (e.g. to count
    matches or to follow up with ecl_get_entry for specific ones).
    """
    return _get_ecl().search(
        category=category,
        after=after,
        before=before,
        form_name=form_name,
        tag=tag,
        username=username,
        substring=substring,
        words=words,
        limit=limit,
        ids_only=True,
    )


@mcp.tool(name="ecl_get_entry")
@_wrap
def get_entry(entry_id: int) -> dict[str, Any]:
    """Fetch a single ECL entry by its numeric ID."""
    return _get_ecl().get_entry(entry_id=entry_id)


@mcp.tool(name="ecl_list_categories")
@_wrap
def list_categories(sample_size: int = 500, force_refresh: bool = False) -> list[str]:
    """Return sorted list of categories seen in recent entries.

    Sampled from the most recent sample_size entries (default 500). Cached
    after the first call; pass force_refresh=True to re-sample.
    """
    return _get_ecl().list_categories(sample_size=sample_size, force_refresh=force_refresh)


@mcp.tool(name="ecl_list_tags")
@_wrap
def list_tags(sample_size: int = 500, force_refresh: bool = False) -> list[str]:
    """Return sorted list of tags seen in recent entries."""
    return _get_ecl().list_tags(sample_size=sample_size, force_refresh=force_refresh)


@mcp.tool(name="ecl_list_forms")
@_wrap
def list_forms(sample_size: int = 500, force_refresh: bool = False) -> list[str]:
    """Return sorted list of forms seen in recent entries."""
    return _get_ecl().list_forms(sample_size=sample_size, force_refresh=force_refresh)


if not _read_only:

    @mcp.tool(name="ecl_post_entry")
    @_wrap
    def post_entry(
        category: str,
        text: str,
        formname: str = "default",
        subject: str = "",
        tags: list[str] | None = None,
        fields: dict[str, str] | None = None,
        preformatted: bool = False,
        private: bool = False,
        related_entry_id: int | None = None,
        do_post: bool = False,
    ) -> dict[str, Any]:
        """Post a new entry to the ECL logbook.

        DESTRUCTIVE: when do_post=True (the default is False -- dry run), a
        real entry is written to the live logbook. There is no undo.

        Args:
            category: target category (must exist -- see ecl_list_categories)
            text: free-form body of the entry
            formname: form to use (default "default" -- see ecl_list_forms)
            subject: short subject line
            tags: list of tag names to attach (see ecl_list_tags)
            fields: extra form fields as {"field_name": "value"}
            preformatted: if True, body is treated as preformatted text
            private: if True, entry is visible only to authenticated users
            related_entry_id: optional ID of an existing entry to link to
            do_post: must be set to True to actually submit. False (the
                     default) returns the prepared XML for inspection.

        Returns {"posted": bool, "xml": str, "response": ...}. When
        do_post=False, response is None and xml is the prepared body.
        """
        entry = ECLEntry(
            category=category,
            tags=tuple(tags or ()),
            formname=formname,
            text=text,
            preformatted=preformatted,
            private=private,
            related_entry=related_entry_id,
        )
        if subject:
            entry._entry.attrib["subject"] = subject  # noqa: SLF001 -- no public setter
        if fields:
            entry.set_form_elements(fields)

        xml = entry.show()
        response = _get_ecl().post(entry, do_post=do_post)

        return {
            "posted": bool(do_post),
            "xml": xml,
            "response": response,
        }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ecl-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("ECL_MCP_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ECL_MCP_PORT", str(DEFAULT_PORT))),
        help="bind port (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "import/construct only, then exit -- does not bind a socket. "
            "Unlike other servers' --check, this DOES make a live network "
            "call: constructing the ECL client resolves the active server "
            "via a redirect-following GET, and requires ECL_URL/"
            "ECL_USER_NAME/ECL_PASSWORD to be set (ECL() raises otherwise)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("ECL_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    if args.check:
        try:
            ecl_client = _get_ecl()  # see --check help: this is a real network call
        except ValueError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            sys.exit(1)
        if ecl_client._url is None:  # noqa: SLF001 -- see _resolve_server in ecl-api:
            # it catches connection errors itself (prints "Error discovering active
            # server: ..." above) and returns None rather than raising, so ECL()
            # construction "succeeds" with a client that can't reach anything. This
            # check exists so --check actually fails in that case instead of
            # reporting OK on an unreachable ECL_URL.
            print(
                "FAILED: could not resolve the ECL server -- see the "
                "'Error discovering active server' message above. Credentials are "
                "set, but ECL_URL is not reachable from this host.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"OK: ecl_mcp constructed "
            f"(host={args.host} port={args.port} read_only={_read_only} "
            f"auth={'mikey' if mcp.settings.auth else 'disabled'})"
        )
        return

    LOGGER.info(
        "Starting ecl MCP server over streamable-http on %s:%s (read_only=%s, auth=%s)",
        args.host,
        args.port,
        _read_only,
        "mikey" if mcp.settings.auth else "disabled",
    )
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
