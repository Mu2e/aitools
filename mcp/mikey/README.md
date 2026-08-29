# mikey

Interactive key tool, plus a small library any Mu2e MCP server can import
to check callers against the keys it manages. See `AUTHPLAN.md` at the
repo root for the full design rationale.

## What this is (and isn't)

mikey is *not* real OAuth, and doesn't try to be. It's a shared static
secret ("bearer token" in MCP-SDK terms) checked against a JSON file on
disk. It exists because everyone actually authorized to use a collaboration
MCP already has legitimate access to the shared account/disk the MCP runs
on -- someone reachable on the network but *not* authorized does not. A
key in a file on that disk is therefore a valid stand-in for "is this
person a collaboration member," even though the network port itself may be
reachable more broadly.

Keys are stored **hashed** (SHA-256), not in plaintext -- the plaintext
value is shown exactly once, at generation time, and can't be recovered
from the file afterwards. This isn't because file permissions aren't
trusted (they are -- that's the actual security boundary here); it's a
free bit of insurance against the file being copied somewhere with looser
permissions than intended.

## Interactive use

```bash
mikey
```

Prompts for a username (defaults to `$USER`) and an optional note, then
prints a new key once, along with the ready-to-paste client command:

```
mikey -- generating a new key in /home/you/.config/mikey/keys.json
Username [alice]: alice
Note (optional, e.g. what this key is for): laptop

New key for 'alice':

    mikey_3f9c...        (64 hex chars)

This is shown once -- only its hash is stored, so it cannot be recovered later.
Give it to them now, e.g. to run on their own machine:

    claude mcp add --transport http --scope user <name> <url> \
      --header "Authorization: Bearer mikey_3f9c..."

(--scope user or --scope local -- never --scope project, which would
 commit the key into .mcp.json and share it with the whole team.)
```

## Non-interactive subcommands

```bash
mikey generate alice --note "laptop"
mikey list                        # username / note / created -- never the secret
mikey revoke <key_id>             # key_id is the short id shown by `list`
mikey revoke --user alice         # revoke every key alice holds
mikey check <token>               # verify a token by hand, e.g. while testing
```

All subcommands (including bare `mikey`) require `--keys-file PATH` or the
`MIKEY_KEYS_FILE` environment variable -- there is no default location.

## Keys file location

There is deliberately no default path. mikey is meant to manage the *one*
shared keys file a centrally-deployed MCP server reads (via its own
`MIKEY_KEYS_FILE`), in that same account -- a silent per-user fallback
(e.g. `~/.config/mikey/keys.json`) would risk an admin generating keys in
their own home directory that no running MCP server ever actually checks
against. Always pass the same path explicitly, e.g.:

```bash
export MIKEY_KEYS_FILE=/path/to/shared/mcp-account/mikey/keys.json
mikey generate alice
```

The file is created with `0600` permissions on first use; nothing else
protects it beyond that and the account's own access controls -- see
"What this is" above.

## Library use (for later MCP integration)

```python
from mikey import build_auth_kwargs

mcp = MCPServer("dqm", instructions=..., **build_auth_kwargs())
```

`build_auth_kwargs()` reads `MIKEY_KEYS_FILE` from the environment; if it's
unset, it returns `{}` and the server stays exactly as unauthenticated as
it is today. `MCPServer(...)` is constructed at *module import time* in
every existing server here (needed so `@mcp.tool()` decorators register
before `main()` runs), so this has to be an environment variable, not a
new CLI flag -- the env var itself is the on/off switch.

`MIKEY_SERVER_URL` optionally sets the placeholder `issuer_url`/
`resource_server_url` AuthSettings requires; these aren't meaningful here
(there's no real OAuth authorization server behind mikey) and default to
`http://localhost/` if unset.

Clients then authenticate with:

```bash
claude mcp add --transport http <name> <url> \
  --header "Authorization: Bearer <token>"
```

using `--scope local` or `--scope user` -- never `--scope project`.

## Identity model

`username` is an arbitrary label, not a real account -- the same keys file
can hold personal keys (`alice`), a group key (`crv-group`), and a
collaboration-wide key (`mu2e`) side by side, checked identically. Server
code reads which one authenticated the current request via
`mcp.server.auth.middleware.auth_context.get_access_token().client_id` and
decides what to do with it (e.g. treat `mu2e` as "any collaboration
member, no per-user distinction needed").

Identity is bound to the connection -- fixed by whichever key was in the
`Authorization` header when the client connected. An LLM can't switch keys
mid-session; that header isn't exposed to tool calls. To use two
identities in one session, register the same server twice under different
local names, each with its own key; both connections stay live in
parallel and the agent picks per tool call which one to use.

## Bearer-token risk

Possession is authority: a leaked key lets anyone act as whatever identity
it's labeled with, with no second factor checked. Mitigate by keeping keys
as narrowly scoped as the use case allows (personal over group over
collaboration-wide) and revoking (`mikey revoke`) as soon as a leak is
suspected -- nothing here can prevent use of a leaked key before that.

## Local dev

```bash
mu2einit
slc uv
cd aitools/mcp/mikey
uv venv
uv pip install -e .
.venv/bin/mikey
```
