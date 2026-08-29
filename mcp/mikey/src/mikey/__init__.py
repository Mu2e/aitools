"""mikey: interactive key tool + lightweight bearer-token check for Mu2e MCP servers.

Not real OAuth -- an informal "does this caller hold a key issued to a
collaboration member" check, backed by a hashed-JSON keys file whose actual
security boundary is OS file permissions on the shared account mikey and
the MCP process both run in. See ../../../AUTHPLAN.md for the full design
rationale and current status (not yet wired into any deployed MCP).
"""

from .store import KeyStore
from .verifier import MikeyTokenVerifier, build_auth_kwargs

__all__ = ["KeyStore", "MikeyTokenVerifier", "build_auth_kwargs"]
