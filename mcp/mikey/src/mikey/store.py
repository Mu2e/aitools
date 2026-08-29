"""Plaintext-JSON-backed key store for mikey.

Threat model (see AUTHPLAN.md): the keys file's OS filesystem permissions
are the *actual* security boundary -- mikey and the MCP process it
authenticates callers for run in the same shared account, so anyone who
can read this file can already do anything that account can do anyway.

Keys are stored hashed rather than in plaintext even so: it costs nothing
(nothing ever needs the plaintext key back after the moment it's
generated) and it's a cheap defense against the narrower failure of this
file being copied somewhere with looser permissions than intended -- e.g.
committed to git by accident, or pasted into a chat log.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

KEY_PREFIX = "mikey_"
_SECRET_BYTES = 32  # 256 bits of entropy


class KeyRecord(TypedDict):
    hash: str
    username: str
    note: str
    created: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class KeyStore:
    """One JSON file, mapping a short key_id -> {hash, username, note, created}.

    key_id is a fingerprint (first 8 hex chars of the key's hash), not a
    secret itself -- safe to print in `mikey list` output, used to target
    `mikey revoke`.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({})
            self.path.chmod(0o600)

    def _load(self) -> dict[str, KeyRecord]:
        with self.path.open() as f:
            return json.load(f)

    def _save(self, records: dict[str, KeyRecord]) -> None:
        # tmp is created fresh under the process umask (typically 0644), so
        # without an explicit chmod here, every save after the first would
        # silently widen the file back open regardless of the 0600 set in
        # __init__ -- atomic replace swaps the whole inode, permissions
        # included.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(records, f, indent=2)
            f.write("\n")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def generate(self, username: str, note: str = "") -> str:
        """Create a new key for ``username``, persist its hash, return the plaintext key.

        The plaintext value is returned exactly once -- it is not
        recoverable from the store afterwards.
        """
        records = self._load()
        while True:
            token = KEY_PREFIX + secrets.token_hex(_SECRET_BYTES)
            digest = _hash(token)
            key_id = digest[:8]
            if key_id not in records:  # collision astronomically unlikely; retry anyway
                break
        records[key_id] = {
            "hash": digest,
            "username": username,
            "note": note,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save(records)
        return token

    def verify(self, token: str) -> KeyRecord | None:
        if not token.startswith(KEY_PREFIX):
            return None
        digest = _hash(token)
        for record in self._load().values():
            if record["hash"] == digest:
                return record
        return None

    def list(self) -> dict[str, KeyRecord]:
        return self._load()

    def revoke_id(self, key_id: str) -> bool:
        records = self._load()
        if key_id not in records:
            return False
        del records[key_id]
        self._save(records)
        return True

    def revoke_user(self, username: str) -> int:
        records = self._load()
        to_remove = [k for k, v in records.items() if v["username"] == username]
        for k in to_remove:
            del records[k]
        if to_remove:
            self._save(records)
        return len(to_remove)
