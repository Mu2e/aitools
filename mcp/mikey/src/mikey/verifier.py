"""Wires KeyStore into the mcp SDK's TokenVerifier protocol.

Not used by any deployed MCP yet (see AUTHPLAN.md) -- this module exists so
that adopting mikey later is a two-line change in a server's module-level
`MCPServer(...)` call:

    from mikey import build_auth_kwargs
    mcp = MCPServer("dqm", instructions=..., **build_auth_kwargs())

Confirmed directly against the installed `mcp` SDK (mcp>=2.0.0):
TokenVerifier is a one-method Protocol (`verify_token`), MCPServer requires
`auth: AuthSettings` whenever `token_verifier` is set, and AuthSettings'
`issuer_url`/`resource_server_url` are required fields even though they're
meaningless for a static-secret check like this one -- kb-mcp (a real
working MCP with a similar API-key-only mode) establishes the precedent of
pointing both at the server's own base URL. See mcp.server.auth.provider
and mcp.server.auth.settings in the installed package for the source of
truth if the SDK's auth API changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .store import KeyStore

ENV_KEYS_FILE = "MIKEY_KEYS_FILE"
ENV_SERVER_URL = "MIKEY_SERVER_URL"

# There's no real OAuth authorization server behind mikey, so there's no
# discovery document worth publishing at these URLs -- they only exist to
# satisfy AuthSettings' required fields.
_PLACEHOLDER_URL = "http://localhost/"


class MikeyTokenVerifier(TokenVerifier):
    def __init__(self, store: KeyStore):
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        record = self.store.verify(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record["username"],
            scopes=[],
            claims={"note": record["note"], "created": record["created"]},
        )


def build_auth_kwargs(
    keys_file: str | Path | None = None,
    *,
    server_url: str | None = None,
) -> dict:
    """Return kwargs to splat into MCPServer(...) to turn on mikey bearer-token auth.

    Resolution order for the keys file: the explicit ``keys_file`` argument,
    then the ``MIKEY_KEYS_FILE`` env var. If neither is set, returns ``{}``
    -- auth stays off, matching every MCP server's current no-auth
    behavior. This is deliberate: MCPServer(...) is constructed at module
    import time (before argparse runs), so there's no CLI flag to gate this
    on -- the environment variable *is* the on/off switch.
    """
    resolved = keys_file or os.environ.get(ENV_KEYS_FILE)
    if not resolved:
        return {}

    url = server_url or os.environ.get(ENV_SERVER_URL) or _PLACEHOLDER_URL
    store = KeyStore(resolved)
    return {
        # validate_token_resource=False is REQUIRED here, not a preference, and
        # setting it explicitly is what keeps every mikey-authenticated server
        # working across the SDK's next major release.
        #
        # A mikey token is a static shared secret. It carries no RFC 8707
        # resource indicator, so MikeyTokenVerifier returns an AccessToken with
        # `resource` unset -- there is no audience to check, and stamping one on
        # would be asserting a claim the token does not make.
        #
        # The SDK's resource check (BearerAuthBackend._issued_for_this_resource)
        # does `AnyHttpUrl(resource or "")` and treats the resulting
        # ValidationError as a mismatch, so a token with no resource FAILS it and
        # the request gets a 401. That check runs whenever resource_server_url is
        # set and validate_token_resource is true. Leaving it unset gets a
        # deprecation warning today and defaults to True in 3.0 -- at which point
        # every server built with these kwargs would reject every valid key.
        # Declaring False pins today's behavior through that change.
        #
        # If mikey ever issues per-resource tokens, this is the line to revisit:
        # set the AccessToken's `resource` in verify_token first, then flip this
        # to True.
        "auth": AuthSettings(
            issuer_url=url,
            resource_server_url=url,
            validate_token_resource=False,
        ),
        "token_verifier": MikeyTokenVerifier(store),
    }
