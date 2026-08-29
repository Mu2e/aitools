"""mikey -- interactive key tool for the mikey bearer-token auth check.

Run with no arguments for an interactive prompt (the common case: someone
sitting in the shared MCP account making a key for a new collaboration
member). Subcommands exist for scripting the same operations.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .store import KeyStore
from .verifier import ENV_KEYS_FILE


def _resolve_keys_file(arg: str | None) -> Path:
    # No silent per-user fallback (e.g. ~/.config/mikey/keys.json): mikey
    # exists to manage the one shared keys file a centrally-deployed MCP
    # server reads via MIKEY_KEYS_FILE, in that same account. Guessing a
    # path would risk generating keys nobody's MCP server ever checks
    # against.
    resolved = arg or os.environ.get(ENV_KEYS_FILE)
    if not resolved:
        sys.exit(
            f"mikey: no keys file given -- pass --keys-file PATH or set ${ENV_KEYS_FILE}.\n"
            "This should be the same path the MCP server itself is configured with."
        )
    return Path(resolved)


def cmd_generate(store: KeyStore, username: str, note: str) -> None:
    token = store.generate(username, note)
    print()
    print(f"New key for {username!r}:")
    print()
    print(f"    {token}")
    print()
    print("This is shown once -- only its hash is stored, so it cannot be recovered later.")
    print("Give it to them now, e.g. to run on their own machine:")
    print()
    print("    claude mcp add --transport http --scope user <name> <url> \\")
    print(f'      --header "Authorization: Bearer {token}"')
    print()
    print("(--scope user or --scope local -- never --scope project, which would")
    print(" commit the key into .mcp.json and share it with the whole team.)")


def cmd_list(store: KeyStore) -> None:
    records = store.list()
    if not records:
        print("No keys.")
        return
    width = max(len(r["username"]) for r in records.values())
    for key_id, r in sorted(records.items(), key=lambda kv: kv[1]["created"]):
        note = f"  ({r['note']})" if r["note"] else ""
        print(f"{key_id}  {r['username']:<{width}}  {r['created']}{note}")


def cmd_revoke(store: KeyStore, key_id: str | None, username: str | None) -> None:
    if key_id:
        ok = store.revoke_id(key_id)
        print("Revoked." if ok else f"No key with id {key_id!r}.")
    else:
        n = store.revoke_user(username)  # type: ignore[arg-type]
        print(f"Revoked {n} key(s) for {username!r}." if n else f"No keys for {username!r}.")


def cmd_check(store: KeyStore, token: str) -> None:
    record = store.verify(token)
    if record is None:
        print("INVALID")
        sys.exit(1)
    print(f"OK -- belongs to {record['username']!r} (created {record['created']})")


def _interactive(store: KeyStore) -> None:
    print(f"mikey -- generating a new key in {store.path}")
    default_user = getpass.getuser()
    username = input(f"Username [{default_user}]: ").strip() or default_user
    note = input("Note (optional, e.g. what this key is for): ").strip()
    cmd_generate(store, username, note)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mikey", description=__doc__)
    parser.add_argument(
        "--keys-file",
        help=f"path to the shared keys.json (required, unless ${ENV_KEYS_FILE} is set)",
    )
    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="create a new key non-interactively")
    p_gen.add_argument("username")
    p_gen.add_argument("--note", default="")

    sub.add_parser("list", help="list issued keys (username/note/date -- never the secret)")

    p_rev = sub.add_parser("revoke", help="revoke a key by id, or all keys for a user")
    group = p_rev.add_mutually_exclusive_group(required=True)
    group.add_argument("key_id", nargs="?", help="the short id shown by `mikey list`")
    group.add_argument("--user", help="revoke every key belonging to this username")

    p_check = sub.add_parser("check", help="verify a token, e.g. before wiring mikey into a server")
    p_check.add_argument("token")

    args = parser.parse_args(argv)
    store = KeyStore(_resolve_keys_file(args.keys_file))

    if args.command is None:
        _interactive(store)
    elif args.command == "generate":
        cmd_generate(store, args.username, args.note)
    elif args.command == "list":
        cmd_list(store)
    elif args.command == "revoke":
        cmd_revoke(store, args.key_id, args.user)
    elif args.command == "check":
        cmd_check(store, args.token)


if __name__ == "__main__":
    main()
