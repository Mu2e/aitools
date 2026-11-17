"""MCP tool access.

The agent's entire capability surface is what the MCP servers in the internal
registry expose. There is deliberately no shell, file or code-execution tool
anywhere in this package -- "no system access" is a property of what exists
here, not a setting that can be flipped.

Connections are opened per call rather than held open for the life of the
process. That costs one HTTP round trip per tool call on a campus network,
and buys three things that matter more here: MCP servers can be redeployed
underneath a running bot, no session expires while a Slack thread sits idle
for hours, and every anyio cancel scope opens and closes inside the same task
(long-lived streamablehttp sessions shared across asyncio tasks are the usual
source of "attempted to exit cancel scope in a different task" crashes).

Besides the registry, a server can be a local stdio command
(``--stdio-server slack=slack-mcp-stdio``). The same per-call rule applies:
the subprocess is spawned for one call and exits with it. That is a process
start per call instead of an HTTP round trip -- fine at chat pace -- and
keeps a crashed or wedged child from outliving the call that hit it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import shutil
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import aiohttp
import httpx2
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger(__name__)

# Slack threads are chatty and tool output is not; a runaway result would
# otherwise eat the whole context window in one call.
MAX_RESULT_CHARS = 20000

_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


@dataclass
class ToolImage:
    """An image a tool returned (MCP ImageContent), kept out of the text result.

    Base64 as text is useless to a model -- it cannot see pixels in it, and
    20k characters of it cost ~15k tokens -- so the image travels separately:
    the Slack side posts it to the thread, and a vision model gets it as an
    image block (see agent.Conversation).
    """

    data: bytes
    mime: str
    name: str

    @property
    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}"


def _image_name(arguments: dict, mime: str, n: int) -> str:
    """A file name for an image, from the call's arguments when they name one."""
    base = ""
    for key in ("image_filename", "filename", "identifier", "name", "id"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            base = value.strip().rsplit("/", 1)[-1]
            break
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base) or "image"
    ext = _IMAGE_EXT.get(mime, "png")
    if not base.lower().endswith("." + ext):
        base = f"{base}.{ext}"
    return base if n == 0 else base.replace(f".{ext}", f"-{n + 1}.{ext}")


@dataclass
class Server:
    name: str
    url: str
    description: str
    needs_token: bool
    # Set for a local stdio server instead of url: argv of the subprocess.
    command: list[str] | None = None


@dataclass
class Tool:
    server: str
    name: str
    qualified: str
    description: str
    schema: dict


def parse_stdio_servers(specs: list[str]) -> dict[str, list[str]]:
    """``NAME=COMMAND [ARGS...]`` -> {name: argv}. COMMAND is split like a
    shell would (shlex), but never run through one."""
    servers: dict[str, list[str]] = {}
    for spec in specs:
        name, sep, command = spec.partition("=")
        name, argv = name.strip(), shlex.split(command)
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not argv:
            raise ValueError(f"bad stdio server {spec!r}: expected NAME=COMMAND [ARGS...]")
        servers[name] = argv
    return servers


def resolve_command(command: str) -> str:
    """A bare command name is looked up next to this interpreter first: a tool
    installed into the bot's own venv (``uv pip install slack-mcp``) is not on
    the systemd unit's PATH."""
    if os.sep in command:
        return command
    venv_bin = os.path.dirname(sys.executable)
    return shutil.which(command, path=os.pathsep.join([venv_bin, os.environ.get("PATH", "")])) or command


def stdio_env(server: str) -> dict[str, str]:
    """What a stdio server gets from the bot's environment, on top of mcp's
    safe defaults (HOME, PATH, USER, ...): only variables named for it
    (``SLACK_*`` for ``slack``). Everything else -- MIKEY_TOKEN, the LLM key --
    stays out of the child."""
    prefix = re.sub(r"[^A-Z0-9]", "_", server.upper()) + "_"
    return {k: v for k, v in os.environ.items() if k.startswith(prefix)}


def _qualify(server: str, tool: str) -> str:
    """OpenAI tool names allow [a-zA-Z0-9_-]{1,64}; registry names and MCP tool
    names are both free-form, so sanitize and truncate."""
    raw = f"{server}__{tool}"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)[:64]


class ToolRegistry:
    """Tools from every server in the MCP registry, namespaced by server."""

    def __init__(self, registry_url: str, token: str = "", timeout: int = 120,
                 stdio_servers: dict[str, list[str]] | None = None):
        self._registry_url = registry_url
        self._token = token
        self._timeout = timeout
        self._stdio_servers = dict(stdio_servers or {})
        self.servers: dict[str, Server] = {}
        self.tools: dict[str, Tool] = {}
        self.failed: dict[str, str] = {}

    # -- discovery ------------------------------------------------------------

    async def load(self) -> None:
        """Fetch the registry, then list tools on every server it names.

        A server that is down is recorded in .failed and skipped: one broken
        MCP should not keep the bot off Slack entirely.
        """
        self.servers = await self._fetch_registry() if self._registry_url else {}
        for name, argv in self._stdio_servers.items():
            if name in self.servers:
                log.warning("stdio server %s replaces the registry entry of the same name", name)
            self.servers[name] = Server(name=name, url="", description=f"local stdio: {shlex.join(argv)}",
                                        needs_token=False, command=argv)
        self.tools = {}
        self.failed = {}

        results = await asyncio.gather(
            *(self._list_server_tools(s) for s in self.servers.values()),
            return_exceptions=True,
        )
        for server, result in zip(self.servers.values(), results):
            if isinstance(result, BaseException):
                reason = describe(result)
                self.failed[server.name] = reason
                log.warning("MCP server %s unavailable: %s", server.name, reason)
                continue
            for tool in result:
                self.tools[tool.qualified] = tool

        log.info(
            "Loaded %d tools from %d/%d MCP servers",
            len(self.tools), len(self.servers) - len(self.failed), len(self.servers),
        )

    async def _fetch_registry(self) -> dict[str, Server]:
        async with aiohttp.ClientSession() as session:
            async with session.get(self._registry_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)

        servers: dict[str, Server] = {}
        for name, entry in (payload.get("mcpServers") or {}).items():
            if name == "registry":
                # The registry endpoint itself -- a directory, not a tool
                # provider worth handing to the model.
                continue
            servers[name] = Server(
                name=name,
                url=entry["url"],
                description=entry.get("description", ""),
                needs_token=entry.get("token") == "yes",
            )
        return servers

    async def _list_server_tools(self, server: Server) -> list[Tool]:
        async with self._session(server) as session:
            listing = await session.list_tools()

        tools = []
        for t in listing.tools:
            schema = dict(t.input_schema or {})
            if not schema:
                schema = {"type": "object", "properties": {}}
            schema.setdefault("type", "object")
            tools.append(Tool(
                server=server.name,
                name=t.name,
                qualified=_qualify(server.name, t.name),
                description=(t.description or "").strip(),
                schema=schema,
            ))
        return tools

    # -- use ------------------------------------------------------------------

    @property
    def schemas(self) -> list[dict]:
        """Tool definitions in OpenAI chat-completions form."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.qualified,
                    "description": tool.description,
                    "parameters": tool.schema,
                },
            }
            for tool in self.tools.values()
        ]

    async def call(self, qualified: str, arguments: dict,
                   images: "list[ToolImage] | None" = None) -> str:
        """Call a tool and return its text for the model.

        Images in the result are appended to ``images`` (when given) instead of
        going into the text; the text says where they went.
        """
        tool = self.tools.get(qualified)
        if tool is None:
            return f"Error: no such tool {qualified!r}."

        server = self.servers[tool.server]
        started = time.monotonic()
        try:
            async with self._session(server) as session:
                result = await session.call_tool(
                    tool.name, arguments, read_timeout_seconds=float(self._timeout)
                )
        except Exception as exc:
            reason = describe(exc)
            log.warning("Tool %s failed after %.1fs: %s",
                        qualified, time.monotonic() - started, reason)
            return f"Error calling {qualified}: {reason}"

        text = _result_text(result, images, arguments)
        # A server that is broken usually reports it *in* a successful response
        # rather than by raising -- memory-mcp answers a dead connection pool
        # with a 30s "PoolTimeout" string, which looked like a healthy call in
        # the log and left us blaming the model for making it up. Log the
        # outcome of every call, and say so out loud when it smells like a
        # failure.
        elapsed = time.monotonic() - started
        looks_wrong = getattr(result, "isError", False) or text.lstrip().startswith("Error")
        log.log(logging.WARNING if looks_wrong else logging.INFO,
                "tool %s %s in %.1fs (%d chars)%s", qualified,
                "FAILED" if looks_wrong else "ok", elapsed, len(text),
                f": {text[:160]}" if looks_wrong else "")
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n...[truncated]"
        return text

    @asynccontextmanager
    async def _session(self, server: Server):
        if server.command:
            params = StdioServerParameters(command=resolve_command(server.command[0]),
                                           args=server.command[1:], env=stdio_env(server.name))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            return

        # mcp 2.x takes per-request headers only through a pre-built HTTP
        # client, so the bearer token for the gated servers (ecl, runs,
        # memory) has to be set here rather than on the transport.
        headers = {}
        if server.needs_token and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        # Long read timeout because a server may hold the response stream open
        # while it works; connect/write stay short so a dead host fails fast.
        timeout = httpx2.Timeout(30.0, read=float(self._timeout))

        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http_client:
            async with streamable_http_client(server.url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


def describe(exc: BaseException) -> str:
    """Flatten an exception to something worth printing.

    anyio runs the transport in a task group, so a refused connection or a 401
    surfaces as an ExceptionGroup whose str() is "unhandled errors in a
    TaskGroup (1 sub-exception)" -- which tells an operator nothing. Unwrap to
    the causes underneath.
    """
    inner = getattr(exc, "exceptions", None)
    if inner:
        return "; ".join(describe(e) for e in inner)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _result_text(result, images: "list[ToolImage] | None" = None, arguments: dict | None = None) -> str:
    """Flatten an MCP CallToolResult into something a chat model can read.

    Image content is decoded into ``images`` and replaced by a short note:
    never pasted into the text as base64.
    """
    parts = []
    n_images = 0
    for item in getattr(result, "content", []) or []:
        if getattr(item, "type", None) == "image" and getattr(item, "data", None):
            mime = getattr(item, "mimeType", None) or "image/png"
            try:
                data = base64.b64decode(item.data)
            except (ValueError, TypeError):
                parts.append(f"[an image ({mime}) that could not be decoded]")
                continue
            if images is None:
                parts.append(f"[an image ({mime}, {len(data) // 1024} KB) -- not shown here]")
                continue
            name = _image_name(arguments or {}, mime, n_images)
            n_images += 1
            images.append(ToolImage(data=data, mime=mime, name=name))
            parts.append(f"[image {name} ({len(data) // 1024} KB, {mime}): it is posted to the "
                         f"user's Slack thread with your answer, so you can refer to it as shown]")
            continue
        text = getattr(item, "text", None)
        parts.append(text if text is not None else str(item))

    if not parts:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps(structured, indent=2, default=str)
        return "(tool returned no content)"

    body = "\n".join(parts)
    if getattr(result, "isError", False):
        return f"Tool reported an error: {body}"
    return body
