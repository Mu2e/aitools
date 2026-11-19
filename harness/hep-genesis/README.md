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
| `setup.sh` | What users source to use an installed copy. |
| `site/` | Mu2e settings: MCP servers, model presets, context for the model, `.env` template, version constraints. |
| `automations/` | Automation templates, `<project>/<name>.json`. |
| `bin/` | `hep-genesis-watch`, `hep-genesis-add-automation`. |
| `systemd/` | The `systemd --user` unit that runs automations for a service account. |

## Use it (on a gpvm)

```bash
source <install>/current/setup.sh
hep-genesis-web
```

`hep-genesis-web` prints an `ssh -L` line and a URL: run the first on your
laptop, open the second in a browser. The default model is the Fermilab vLLM
service, which needs no key; others (Fermilab LiteLLM, ALCF) are added in the
UI under Models, with the steps shown there.

Your chats, settings and keys are in
`/exp/mu2e/data/users/$USER/hep-genesis-data` (set `HG_DATA_DIR` for another
place), created on first use. Nothing is written into the install.

## Install or upgrade (maintainers)

Needs `uv`, git access to the private repository, and node (>= 20) with npm
for the web UI.

```bash
harness/hep-genesis/install.sh <install>            # the pinned commit
harness/hep-genesis/install.sh <install> --no-ui    # automations only: no node needed
```

This creates `<install>/releases/<commit>/` and points `<install>/current`
at it; the newest two releases are kept. The install is self-contained: it
carries its own copy of `setup.sh`, `site/`, `automations/` and `systemd/`,
and `VERSION` names both commits. To roll back, repoint `current`:

```bash
ln -sfn releases/<older commit> <install>/current
```

To upgrade, set `ref=` in `hep-genesis.pin` to the new commit, commit that,
and run `install.sh` again.

**Where.** The repository is private, and CVMFS is readable by anyone, so the
install belongs on `/exp/mu2e/app` (readable by the `mu2e` group), not on
CVMFS.

**As another account** (`mu2eai`), git needs read access: a read-only deploy
key on the repository, and
`HEP_GENESIS_REPO=git@github.com:Mu2e/hep-genesis-agent.git`.

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

# put the automations in place, then start them
export HEP_PROJECTS_DIR=$D/projects
$I/current/bin/hep-genesis-add-automation dqm-alarm/dqm-alarms.json
systemctl --user enable --now hep-genesis-watch@dqm-alarm__dqm-alarms
```

```bash
systemctl --user list-units 'hep-genesis-watch@*'
journalctl --user -u hep-genesis-watch@dqm-alarm__dqm-alarms -f -o cat
systemctl --user restart 'hep-genesis-watch@*'    # after an upgrade
```

Notes:

- **Model.** Use an endpoint with a key (Fermilab LiteLLM) for the service.
  A Globus login to ALCF expires after 30 days, and an automation firing at
  night would then fail.
- **A restart does not repeat anything.** An automation keeps what it has
  reported in its state folder next to its file.
- **Chats outlive the automation.** They are processes of their own
  (`KillMode=process`): restarting a unit does not stop an investigation.
- **Results** are chats in the service's data folder. Reading them needs a
  web UI on that folder (as the service account, on loopback, through an ssh
  tunnel); posting them somewhere people look (Slack, ECL) is not built yet.
- **Not from two places.** Do not also start the same automations from a web
  UI on the same data folder: Stop there ends the unit's copy, and systemd
  does not restart a clean stop.

## Templates

| Template | Checks | Fires | Starts |
|---|---|---|---|
| `dqm-alarm/dqm-alarms.json` | `dqm.list_alarms` (open), every 5 min | a new alarm, or a change of an alarm's level | a quick chat that investigates it with dqm, runs, ecl, kb |
| `docdb-digest/docdb-daily.json` | the clock | every day at 08:00 | a quick chat that writes a digest of the last day's DocDB documents (kb) |

Add one to your own data folder with
`hep-genesis-add-automation <project>/<name>.json`, then start it in the web
UI under Automations, where its prompt and settings can be edited.
