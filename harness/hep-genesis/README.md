# hep-genesis at Mu2e

[hep-genesis](https://github.com/Mu2e/hep-genesis-agent) is a research agent
with a web UI and a command line: quick chats and longer research runs over
the Mu2e MCP servers (run database, DQM, ECL, metacat, knowledge base,
literature), and *automations* that check something on a schedule and start
a chat when a condition is met.

The code lives in Mu2e/hep-genesis-agent (private; Mu2e's copy of
HEP-KE/hep-genesis-agent). This folder holds what makes it Mu2e's
deployment: which commit to install, how to install it, the Mu2e settings,
and the automations we run.

| File | What it is |
|---|---|
| `hep-genesis.pin` | The repository and the commit to install. An upgrade is a change of this file. |
| `install.sh` | Installs that commit under a folder: checkout, web UI, Python environment. |
| `setup.sh` | First use: installs if needed, creates your data folder, loads the environment. |
| `env.sh` | Every later shell: the environment only. |
| `site/` | Mu2e settings: MCP servers, model presets, context for the model, `.env` template, version constraints. |
| `automations/` | Automation templates, `<project>/<name>.json`. |
| `bin/` | `hep-genesis-watch`, `hep-genesis-add-automation`. |
| `systemd/` | The `systemd --user` unit that runs all automations for a service account. |

## Use it (on a gpvm)

As for the other harnesses: `setup.sh` once, `env.sh` in every later shell.

```bash
cd /exp/mu2e/app/users/$USER/aitools/harness/hep-genesis
. setup.sh          # first time: installs (a minute or two), creates your data folder
hep-genesis-web
```

```bash
. /exp/mu2e/app/users/$USER/aitools/harness/hep-genesis/env.sh    # in a new shell
```

`hep-genesis-web` prints an `ssh -L` line and a URL: run the first on your
laptop, open the second in a browser. The default model is the Fermilab vLLM
service, which needs no key; others (Fermilab LiteLLM, ALCF) are added in the
UI under Models, with the steps shown there.

| | Where | Override |
|---|---|---|
| The install | `/exp/mu2e/app/users/$USER/hep-genesis` | `HEP_GENESIS_ROOT` |
| Your chats, settings, keys | `/exp/mu2e/data/users/$USER/hep-genesis-data` (mode 700) | `HG_DATA_DIR` |

`setup.sh` installs from the private repository Mu2e/hep-genesis-agent, which
every member of the Mu2e GitHub organization can read. Git has to show GitHub
who you are: an ssh key registered on GitHub (test with `ssh -T
git@github.com`), or the gh CLI (`gh auth login && gh auth setup-git`), which
the installer falls back to. It also needs node >= 20 with npm for the web UI
(`uv` is fetched if you have none). Without those,
use a shared install somebody else built: no clone, no build,

```bash
. <shared install>/current/env.sh
```

which gives you the same commands with your own data folder.

## Add your own MCP server

The Mu2e servers (run database, DQM, ECL, metacat, knowledge base, ...) come
from the registry and need no setup. A server of your own is one more entry
in your `servers.json`, added in the web UI under **Servers → Add server**
or by editing the file (`$HEP_SERVERS_FILE`,
`/exp/mu2e/data/users/$USER/hep-genesis-data/servers.json`).

A **stdio** server is a program hep-genesis starts itself and talks to over
its standard input and output: nothing listens on a port, and it runs as
you, with your files.

### Example: the analysis server, over stdio

[analysis-mcp-server](https://github.com/michaelmackenzie/analysis-mcp-server)
runs Mu2e analyses (`mu2e -c <fcl> -s <file>`, or Python over a ROOT file)
as tools. It needs the Mu2e environment, so it is started through a small
script.

1. Get the server:

   ```bash
   cd /exp/mu2e/app/users/$USER
   git clone https://github.com/michaelmackenzie/analysis-mcp-server.git
   ```

2. Write a start script, `/exp/mu2e/app/users/$USER/analysis-mcp-server/start-stdio.sh`:

   ```bash
   #!/usr/bin/env bash
   # Everything the setup prints goes to stderr (>&2): on stdio, standard
   # output carries the MCP messages and must hold nothing else.
   cd "$(dirname "${BASH_SOURCE[0]}")"
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh >&2
   pyenv ana >&2
   exec python3 -m analysis_mcp_server --transport stdio --musing 'SimJob MDC2025ay'
   ```

   ```bash
   chmod +x /exp/mu2e/app/users/$USER/analysis-mcp-server/start-stdio.sh
   ```

   `--musing` picks the Offline release the analyses run with; see the
   server's README for a work area or a tarball instead.

3. Add it. In the web UI: **Servers → Add server**, name `mu2e-analysis`,
   transport *stdio*, and the script's full path as the command (no
   arguments). Or in `servers.json`, one more entry in `"servers"`, with
   your user name written out (the path is used as written):

   ```json
   {
     "name": "mu2e-analysis",
     "transport": "stdio",
     "command": "/exp/mu2e/app/users/<you>/analysis-mcp-server/start-stdio.sh"
   }
   ```

4. Check it. In the web UI, **Connect** on its row lists its tools, or
   says why it could not start. On the command line:

   ```bash
   hep-genesis-agent --list-servers
   hep-genesis-agent --quick --only-servers mu2e-analysis "which analyses can you run?"
   ```

Things that go wrong:

- **It connects and then fails with a parse error**: something printed to
  standard output before the server started. Send it to stderr (`>&2`), as
  in the script.
- **It is slow to start**: the first start after a while reads the Mu2e
  environment from a cold CVMFS cache. Connect again.
- **A tool cannot read a file on tape or in dCache**: the server runs as
  you, so it needs what you would need in a shell (a Kerberos ticket, the
  file staged).
- **A started chat does not use it**: an automation's chat only gets the
  servers its "Tools: only these servers" field names.

A server that is already running somewhere is the other kind: transport
*streamable-http* and its URL (`http://host:port/mcp`), with a header for
its key if it needs one.

## Install or upgrade (maintainers)

Needs git access to the private repository, and node (>= 20) with npm for
the web UI.

```bash
harness/hep-genesis/install.sh <install>            # the pinned commit
harness/hep-genesis/install.sh <install> --no-ui    # automations only: no node needed
```

This creates `<install>/releases/<commit>/` and points `<install>/current`
at it; the newest two releases are kept. The install is self-contained: it
carries its own copy of `env.sh`, `site/`, `automations/` and `systemd/`,
and `VERSION` names both commits. To roll back, repoint `current`:

```bash
ln -sfn releases/<older commit> <install>/current
```

To upgrade, set `ref=` in `hep-genesis.pin` to the new commit, commit that,
and run `install.sh` again.

**Where.** The repository is private, and CVMFS is readable by anyone, so the
install belongs on `/exp/mu2e/app` (readable by the `mu2e` group), not on
CVMFS.

**As another account** (`mu2eai`), git needs read access too: a read-only
deploy key on the repository, as that account's ssh key.

## Automations as a service (`mu2eai`)

An automation is a file, `<project>/watches/<name>.json` in the data folder:
what to check, when it fires, and the chat it then starts (prompt, project,
model, which MCP servers the chat may use). The templates here limit their
chats to read-only servers; a chat started by the service never gets coding
tasks.

```bash
I=/exp/mu2e/app/users/mu2eai/hep-genesis          # the install
D=/exp/mu2e/data/users/mu2eai/hep-genesis         # the service's data

harness/hep-genesis/install.sh $I --no-ui
mkdir -p $D && chmod 700 $D
cp $I/current/site/env.template $D/.env && chmod 600 $D/.env   # then: model endpoint + key, MIKEY_TOKEN

$I/current/systemd/install-unit.sh --data-dir $D --env-file $D/.env
loginctl enable-linger                            # once per account

# put the automations in place, then start the one service that runs them all
export HEP_PROJECTS_DIR=$D/projects
$I/current/bin/hep-genesis-add-automation dqm-alarm/dqm-alarms.json
systemctl --user enable --now hep-genesis-automations
```

```bash
systemctl --user status hep-genesis-automations
journalctl --user -u hep-genesis-automations -f -o cat    # every line starts with <project>/<name>
systemctl --user restart hep-genesis-automations          # a new automation, or an upgrade
```

One process runs every automation file of the data folder
(`hep-genesis-agent --watch-all`). What needs a restart and what does not:

| Change | Takes effect |
|---|---|
| Edit an automation (web form, or its file) | by itself, within seconds |
| `"enabled": false` in its file (Enabled in the form) | by itself: that one ends |
| A new automation, or one enabled again | on a restart of the service |
| A new release | on a restart of the service |

### The web UI of the service

To read the chats the automations start, and to edit the automations in a
form, run the web UI on the same data folder as a second service:

```bash
$I/current/systemd/install-unit.sh --data-dir $D --env-file $D/.env --web-port 8200 --web-no-token
systemctl --user enable --now hep-genesis-web
```

```bash
ssh -L 8200:localhost:8200 mu2eai@mu2eaigpvm01      # then open http://localhost:8200/
journalctl --user -u hep-genesis-web -n 20 -o cat
```

- **It listens on the machine itself only** (127.0.0.1), on port 8200. MCP
  servers use 80XX there.
- **`--web-no-token`: no login.** Everyone who can log in to the machine can
  use it, and acts as the service account in it: chats, automations, MCP
  servers, model settings. Leave the option out to keep the token; the link
  with it is then in the journal, new at every start.
- **No coding tasks**, as for the automations: a chat there cannot run
  commands. `install-unit.sh` refuses an `.env` that sets `HEP_CODING`.
- **Restarting it does not touch the automations**, and the other way round.

Notes:

- **Model.** Use an endpoint with a key (Fermilab LiteLLM) for the service.
  A Globus login to ALCF expires after 30 days, and an automation firing at
  night would then fail.
- **A restart does not repeat anything.** An automation keeps what it has
  reported in its state folder next to its file.
- **Chats outlive the automations.** They are processes of their own
  (`KillMode=process`): restarting the service does not stop an investigation.
- **A slow server.** A check that gets no answer is given up after 120 s
  (`"timeout_s"` in the check, under `probes`), reported once as an event, and
  tried again at the next interval. The other automations are not held up.
- **One failing takes all down, briefly.** An unexpected error in one
  automation ends the process, and systemd starts it again after a minute. A
  wrong file is not that: it is reported and left out.
- **Results** are chats in the service's data folder. Reading them needs a
  web UI on that folder (as the service account, on loopback, through an ssh
  tunnel); posting them somewhere people look (Slack, ECL) is not built yet.
- **With a web UI on the same data folder.** Stop there ends that automation
  until the service restarts; Start runs it from the web server instead of
  the service. To switch one off for good, untick Enabled in its form.

## Templates

| Template | Checks | Fires | Starts |
|---|---|---|---|
| `dqm-alarm/dqm-alarms.json` | `dqm.list_alarms` (open), every 5 min | a new alarm, or a change of an alarm's level | a quick chat that investigates it with dqm, runs, ecl, kb |
| `docdb-digest/docdb-daily.json` | the clock | every day at 08:00 | a quick chat that writes a digest of the last day's DocDB documents (kb) |

Add one to your own data folder with
`hep-genesis-add-automation <project>/<name>.json`, then start it in the web
UI under Automations, where its prompt and settings can be edited.
