# hep-genesis: install and the `mu2eai` service

For maintainers. Using hep-genesis yourself: [README.md](README.md).

## What is in this folder

The code lives in Mu2e/hep-genesis-agent (private; Mu2e's copy of
HEP-KE/hep-genesis-agent). This folder is what makes it Mu2e's deployment.

| File | What it is |
|---|---|
| `hep-genesis.pin` | The repository and the commit to install. An upgrade is a change of this file. |
| `install.sh` | Installs that commit under a folder: checkout, web UI, Python environment. |
| `setup.sh` | First use: installs if needed, creates your data folder, loads the environment. |
| `env.sh` | Every later shell: the environment only. |
| `site/` | Mu2e settings: MCP servers, model presets, context for the model, `.env` template, version constraints. |
| `automations/` | Automation templates, `<project>/<name>.json`. |
| `bin/` | `hep-genesis-watch`, `hep-genesis-add-automation`. |
| `systemd/` | The `systemd --user` units for a service account: all automations, and the web UI. |
| `log-session.py` | Usage logger: a chat's totals in the shared `usage.sessions` format. |

## Install or upgrade

Needs git access to the private repository, and node (>= 20) with npm for
the web UI.

```bash
harness/hep-genesis/install.sh <install>            # the pinned commit
harness/hep-genesis/install.sh <install> --no-ui    # automations only: no node needed
```

This creates `<install>/releases/<release>/` and points `<install>/current`
at it; the newest two releases are kept. A release is named after the
hep-genesis commit and the last aitools commit that changed this folder, so
a change of the harness alone (a template, a unit, the logger) is a new
release too; asking for one that is there already just makes it current. The install is self-contained: it
carries its own copy of `env.sh`, `site/`, `automations/` and `systemd/`,
and `VERSION` names both commits. To roll back, repoint `current`:

```bash
ln -sfn releases/<older release> <install>/current
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

Use a model endpoint with a key (Fermilab LiteLLM) for the service: a Globus
login to ALCF expires after 30 days, and an automation firing at night would
then fail. A restart repeats nothing, and does not stop chats that are
already running.

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
  use it, and acts as the service account in it: chats, automations, model
  settings, and MCP servers. Adding a stdio server there starts a command as
  the service account, so this is command access for every local account,
  whatever `HEP_CODING` says. Use it only where that is acceptable; leave the
  option out to keep the token (the link with it is then in the journal, new
  at every start).
- **No coding tasks**, as for the automations: a chat there cannot run
  commands. `install-unit.sh` refuses an `.env` that sets `HEP_CODING`.
- **Restarting it does not touch the automations**, and the other way round.

## Usage logging

Every chat's usage (model, model calls, tokens, tool names; no user, no
content) is written to `<data folder>/log/usage.jsonl` in the shared
`usage.sessions` format, as in the other harnesses. The service account also
sends it to Postgres, with these two lines in `$D/.env`:

```bash
LOG_PG_DSN=postgresql://ifdb11:5477/mu2e_ai_prd
KRB5CCNAME=/tmp/krb5cc_18199_auto
```

`$I/current/venv/bin/python $I/current/usage/log-session.py --check` says
where records go and tries the database.
