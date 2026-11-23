# hep-genesis at Mu2e

[hep-genesis](https://github.com/Mu2e/hep-genesis-agent) is a research agent
with a web UI and a command line: quick chats and longer research runs over
the Mu2e MCP servers (run database, DQM, ECL, metacat, knowledge base,
literature), and *automations* that check something on a schedule and start
a chat when a condition is met.

## Use it (on a gpvm)

First time:

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/aitools.git     # skip if you have it already
cd aitools/harness/hep-genesis
. setup.sh          # installs (a few minutes), creates your data folder
hep-genesis-web
```

`hep-genesis-web` prints an `ssh -L` line and a URL: run the first on your
laptop, open the second in a browser. The default model is the Fermilab vLLM
service, which needs no key; others (Fermilab LiteLLM, ALCF) are added in the
UI under Models, with the steps shown there. On the command line:
`hep-genesis-agent --help`.

**In every new shell**, load the environment first. It puts the commands on
your `PATH` and points them at your data folder; without it they are not
found:

```bash
. /exp/mu2e/app/users/$USER/aitools/harness/hep-genesis/env.sh
```

| | Where | Override |
|---|---|---|
| The install | `/exp/mu2e/app/users/$USER/hep-genesis` | `HEP_GENESIS_ROOT` |
| Your chats, settings, keys | `/exp/mu2e/data/users/$USER/hep-genesis-data` (mode 700) | `HG_DATA_DIR` |

**Usage log.** Counts, model and tool names per chat (no user, no content)
go to `log/usage.jsonl` in your data folder and to the
[usage log service](../usage/server/README.md); `LOG_URL=` (empty) in your
`.env` keeps them local.

**What the install needs.** The code is in the private repository
Mu2e/hep-genesis-agent, readable by every member of the Mu2e GitHub
organization, so git has to know who you are on GitHub: an ssh key
registered there (`ssh -T git@github.com`), or the gh CLI (`gh auth login`).
The web UI is built with node >= 20 and npm.

**Upgrade.** `setup.sh` leaves an existing install alone. To move to the
commit the harness now pins:

```bash
cd /exp/mu2e/app/users/$USER/aitools && git pull
harness/hep-genesis/install.sh /exp/mu2e/app/users/$USER/hep-genesis
```

Then restart `hep-genesis-web`.

## Add your own MCP server

The Mu2e servers come from the registry and need no setup. A server of your
own is one more entry in your `servers.json`: in the web UI under
**Servers → Add server**, or in the file
(`/exp/mu2e/data/users/$USER/hep-genesis-data/servers.json`).

- **stdio**: a program hep-genesis starts itself and talks to over standard
  input and output. Nothing listens on a port, and it runs as you.
- **streamable-http**: a server already running somewhere; give its URL
  (`http://host:port/mcp`) and a header for its key if it needs one.

### Example: the analysis server, over stdio

[analysis-mcp-server](https://github.com/michaelmackenzie/analysis-mcp-server)
runs Mu2e analyses as tools. Clone it to
`/exp/mu2e/app/users/$USER/analysis-mcp-server`. It has no start script of
its own, so save this there as `start-stdio.sh` and make it executable
(`chmod +x`):

```bash
#!/usr/bin/env bash
# On stdio, standard output carries the MCP messages and nothing else:
# what the setup prints goes to stderr.
cd "$(dirname "${BASH_SOURCE[0]}")"
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh >&2
pyenv ana >&2
exec python3 -m analysis_mcp_server --transport stdio --musing 'SimJob MDC2025ay'
```

Then add it under **Servers → Add server** (name `mu2e-analysis`, transport
*stdio*, the script's full path as the command), or as one more entry in
`"servers"` in the file:

```json
{"name": "mu2e-analysis", "transport": "stdio",
 "command": "/exp/mu2e/app/users/<you>/analysis-mcp-server/start-stdio.sh"}
```

**Connect** on its row lists its tools, or says why it could not start.

## Automations

An automation checks something on a schedule and starts a chat when a
condition is met. Two templates come with the harness:

| Template | Checks | Fires | Starts |
|---|---|---|---|
| `dqm-alarm/dqm-alarms.json` | `dqm.list_alarms` (open), every 5 min | a new alarm, or a change of an alarm's level | a quick chat that investigates it with dqm, runs, ecl, kb |
| `docdb-digest/docdb-daily.json` | the clock | Tuesday to Saturday at 08:00 | a quick chat that writes a short digest of the day before, by topic: DocDB (kb), with the logbook, runs and data quality where they add something, and posts it to a Slack channel |
| `docdb-digest/docdb-weekly.json` | the clock | Monday at 08:00 | the same for the week Monday to Sunday: one short post on where things stand, and up to three replies in its thread for what is worth more |

The two digests post to Slack, which needs a server entry of its own that is
not part of the site's list: see "Digests in Slack" in [SERVICE.md](SERVICE.md).
Without it they write the digest as the chat's answer and say that they could
not post.

Add one to your own data folder with
`hep-genesis-add-automation <project>/<name>.json`, then start it in the web
UI under Automations, where its prompt and settings can be edited.

### What an automation file says

Three things: what to check, when that triggers, and the action then taken.
The DQM template without its prompt:

```json
{
  "every_s": 300,
  "check":   {"server": "dqm", "tool": "list_alarms", "args": {"open_only": true, "limit": 100}},
  "trigger": {"type": "list", "id": "aid", "changes": ["level"]},
  "action":  {"servers": ["dqm", "runs", "ecl", "kb"],
              "budget": {"minutes": 15, "tool_calls": 25, "tokens": 400000},
              "prompt": "A DQM alarm needs a look: {title} ..."}
}
```

- **`check`**: the MCP tool called every `every_s` seconds, with these
  arguments, as a chat would call it.
- **`trigger`**: when it fires. Its `type`:

  | `type` | Fires when | Settings |
  |---|---|---|
  | `list` | the result's list has a new item, or an item's `changes` fields changed | `id` (what tells items apart), `changes` |
  | `value` | a value passes a threshold, for some time when asked | `path`, `op`, `value`, `for_s` |
  | `time` | once a day (needs no `check`) | `at`, `days` |
  | `change` | anything in the result differs from the check before | `path` |

- **`action`**: the chat it starts: the `prompt`, the MCP `servers` the chat
  may use, its `budget`, and optionally `project`, `mode`, `endpoint`/`model`
  and `max_per_hour` (4).

For more than one, `checks` (named) and `triggers` (a list) take the place of
`check` and `trigger`; each trigger then names its check and may have its own
`action`. The full description is at the top of
`backend/src/hep_genesis/agent/watch.py` in hep-genesis-agent. Files in the
earlier names (`probes`, `rules`, `chat`) still work; saving one in the web
form rewrites it in these names.

## Develop hep-genesis-agent

To run your own checkout of hep-genesis-agent with the Mu2e settings, make
the install a development one:

```bash
harness/hep-genesis/install.sh /exp/mu2e/app/users/$USER/hep-genesis --src <your checkout>
```

`setup.sh` and `env.sh` then work as above, and the commands run the
checkout's code: backend edits count at the next start, the web UI after
`scripts/build_mu2e_web.sh` in the checkout. The same command without
`--src` goes back to the pinned release.

## For maintainers

The pinned commit, installing for a service account, the `mu2eai`
automations service and its web UI: [SERVICE.md](SERVICE.md).
