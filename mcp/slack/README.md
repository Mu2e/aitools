# slack-mcp

MCP server for an **allowlisted** set of Mu2e Slack channels, read-only
unless an operator switches [posting](#posting) on. It reads channels
such as `#mu2e-shift` and gives agents the human side of operations — what
shifters and experts reported, decided and asked, and when — next to the
machine side served by the DAQ, run-log and DCS servers.

Same packaging and deployment as the other `mcp/*` servers: `pyproject.toml`
installed with `uv` from the git subdirectory, versioned release dirs, a
`systemd --user` unit, optional mikey bearer-token auth over streamable-HTTP.
Additionally it has a stdio entry point so a local agent (daqpy) can spawn it
as a subprocess, like it does with `ecl-mcp-stdio` and `dcs-mcp-stdio`.

## Scope

- **Three read tools.** No reactions, no workspace search, no user
  directory, no editing or deleting. A fourth tool that posts exists only
  where an operator named channels for it ([Posting](#posting)).
- **Allowlist is the universe.** `SLACK_MCP_CHANNELS` names the channels that
  can be listed or read. A channel not in it cannot be reached by name or by
  id — callers are told which channels exist here, not that one was denied.
- **Bounded output.** At most `SLACK_MCP_MAX_MESSAGES` (500) messages per
  call; defaults are the last 24 h and 100 messages.

## Exposed tools

| Tool | Returns |
|---|---|
| `slack_server_info()` | version, allowlist, limits, auth mode (prefixed so clients merging several servers' tools, like daqpy, see no duplicate name) |
| `slack_list_channels(refresh=False)` | the allowlisted channels: id, name, private?, topic, purpose, members |
| `slack_read_channel(channel, since="24h", until=None, limit=100, include_threads=False)` | messages oldest-first: ISO time, author name, text with `@mentions`/links resolved, permalink, reply_count/thread_ts, files, reactions; optionally each thread's replies nested |
| `slack_read_thread(channel, thread_ts, limit=200)` | the parent and all replies of one thread |

Times accept relative (`24h`, `7d`, `30m`), ISO 8601, or unix epoch.

## Posting

Off by default. With `SLACK_MCP_POST_CHANNELS` set, a stdio copy also offers

| Tool | Does |
|---|---|
| `slack_post_message(channel, text, thread_ts=None)` | posts `text` to one of the post channels (or as a reply in a thread there) and returns the permalink and the `ts` (give it as `thread_ts` to reply to that post) |

It is meant for one application that starts its own copy with its own
lists, for example a digest automation that writes to one channel. The HTTP
server refuses to start with a post list: there every key holder would get
the tool.

What a post can and cannot do:

- **Only the post channels.** The list is separate from the read list; a
  channel can be on either or both. They are the only values the
  tool's schema allows for `channel`.
- **Markdown in, Slack markup out.** Bold, lists, `[text](url)` links and
  code blocks are converted; a long text is split into several messages.
- **Nobody is notified.** Every `<`, `>` and `&` of the text is escaped, so
  `<!channel>`, `<!here>` and `<@user>` arrive as plain text. The only links
  are the http(s) ones built from the Markdown.
- **No link previews**, so Slack fetches no address that ends up in a post.
- **Limits:** `SLACK_MCP_POST_MAX_CHARS` (12000) per post and
  `SLACK_MCP_POST_PER_HOUR` (5) per process. Over stdio a process is one
  chat or one automation run.
- **A footer under every post**, by default
  "_AI-generated; may contain mistakes._". `SLACK_MCP_POST_FOOTER` sets
  another (Markdown, for example with the automation's name); empty: none.
- **No double posts.** The same text is not posted to the same channel
  twice within an hour (by its hash), so a retry after a timeout is refused.
  A retry is often a new process: that case is covered when
  `SLACK_MCP_POST_LOG` is set.
- **A record of every post:** channel, length, hash and permalink (never the
  text) go to the log, and as one JSON line to `SLACK_MCP_POST_LOG` when set.
- **Trying a prompt:** with `SLACK_MCP_POST_DRY_RUN=1` the tool returns what
  it would post and posts nothing.

### Deleting a post

No tool edits or deletes. To remove a post, as the account that holds the
bot token:

```bash
export SLACK_BOT_TOKEN=xoxb-...        # or: set -a; . <env file>; set +a
slack-mcp --delete <permalink> [<permalink> ...]
```

The permalink is "Copy link" on the message in Slack, or the `permalinks`
of the post record (`SLACK_MCP_POST_LOG`). A long post is several messages:
give each link. Slack lets the bot delete only its own messages. Without
the token at hand, a workspace admin can delete any message in Slack
(message menu, "Delete message").

## Slack app

A bot token (`xoxb-…`) with scopes `channels:history`, `channels:read`,
`users:read`; for private channels also `groups:history`, `groups:read`;
for posting also `chat:write`.
Invite the bot to every allowlisted channel (`/invite @<bot>`) — Slack lists
public channels the bot is not in, but refuses their history
(`not_in_channel`). The DAQ bot (`daqbot`) token can be reused; without a
post list this server never posts with it. Scopes by use case for all our Slack tools:
`harness/slack/SLACK_APP.md`.

## Environment variables

| Variable | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | bot token (required) |
| `SLACK_MCP_CHANNELS` | comma-separated channel names (no `#`) or ids — the allowlist (required, unless the copy only posts) |
| `SLACK_MCP_POST_CHANNELS` | channels that can be posted to; empty (default) = no post tool. stdio only |
| `SLACK_MCP_POST_MAX_CHARS` / `SLACK_MCP_POST_PER_HOUR` | limits of a post, default 12000 characters and 5 an hour |
| `SLACK_MCP_POST_FOOTER` | the line under every post; default "_AI-generated; may contain mistakes._", empty = none |
| `SLACK_MCP_POST_LOG` | file that gets one JSON line per post (channel, length, permalinks) |
| `SLACK_MCP_POST_DRY_RUN` | `1`: the post tool returns what it would post and posts nothing |
| `SLACK_MCP_MAX_MESSAGES` | per-call cap, default 500 |
| `SLACK_MCP_HOST` / `SLACK_MCP_PORT` | bind for the HTTP transport (default 0.0.0.0:8009) |
| `SLACK_MCP_LOG_LEVEL` | default INFO (WARNING for stdio) |
| `MIKEY_KEYS_FILE` | enables mikey auth on HTTP; unset = disabled |

## `--check`

`slack-mcp --check` calls `auth.test` and resolves the allowlist and the
post list (names via `conversations.list`, ids via `conversations.info`),
prints the result and exits non-zero if a channel cannot be resolved. It
reads no messages and posts nothing.

## Local dev

```bash
uv venv && uv pip install -e '.[dev]'
set -a; . ./config/slack-mcp.env.example; set +a   # or your real env file
slack-mcp --check
slack-mcp --transport stdio                        # for an MCP client over stdio
python -m pytest tests                              # no network
```

## Server-account install

On mu2eaigpvm01, as `mu2eai`, next to the other servers (uv comes from
`mu2einit && slc uv` there):

```bash
deploy=/exp/mu2e/app/users/mu2eai/mcp/slack
./scripts/install.sh $deploy <ref>
cp $deploy/current/.venv/share/slack-mcp/slack-mcp.env.example $deploy/slack-mcp.env
$EDITOR $deploy/slack-mcp.env && chmod 600 $deploy/slack-mcp.env
#   SLACK_BOT_TOKEN     -- the askmu2e bot token can be reused (read scopes only are used)
#   SLACK_MCP_CHANNELS  -- the allowlist; over HTTP it is readable by every mikey holder
#   MIKEY_KEYS_FILE=/exp/mu2e/app/users/mu2eai/mcp/mikey/keys
set -a; . $deploy/slack-mcp.env; set +a
$deploy/current/.venv/bin/slack-mcp.sh --check
$deploy/current/.venv/bin/slack-mcp-install-unit.sh --port 8009 --env-file $deploy/slack-mcp.env
$deploy/current/.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8009 <mikey-token>
```

Not in the registry yet: port 8009 is this server's default, but
`../registry/config/ports.json` deliberately has no `slack` entry, so nothing
discovers it until we decide to publish it. To publish, deploy this server
first, then add to `ports.json` and redeploy the registry:

```json
"slack": {
  "port": 8009,
  "description": "Read-only MCP server for an allowlisted set of Mu2e Slack channels (slack_list_channels, slack_read_channel, slack_read_thread).",
  "token": "yes"
}
```

Once it is in the registry, the Slack bot can drop its local
`--stdio-server slack=...`: the registry's `slack` server then provides the
same tools (a stdio server of the same name would take precedence).

## Client config

HTTP (Claude Code, Goose, the Slack bot via the registry):

```bash
claude mcp add --transport http --scope user slack http://<host>:8009/mcp \
  --header "Authorization: Bearer mikey_<token>"
```

stdio (daqpy): install the package into daqpy's venv; daqpy auto-detects the
sibling `slack-mcp-stdio` and reads `SLACK_BOT_TOKEN` / `SLACK_MCP_CHANNELS`
from its own environment.

stdio with posting (hep-genesis, `servers.json`): an entry that is off for
chats and named only by the automation that posts.

```json
{"name": "slack-digest", "transport": "stdio", "enabled": false,
 "command": "<deploy>/current/.venv/bin/slack-mcp-stdio",
 "env": {"SLACK_BOT_TOKEN": {"env": "SLACK_BOT_TOKEN"},
         "SLACK_MCP_CHANNELS": "<channel>",
         "SLACK_MCP_POST_CHANNELS": "<channel>",
         "SLACK_MCP_POST_LOG": "<data folder>/slack-posts.jsonl"}}
```
