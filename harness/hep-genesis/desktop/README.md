# The hep-genesis desktop app on a laptop, set up for Mu2e

The desktop app is a program of its own: it runs its agent on your laptop
and knows nothing of the gpvm install. These two scripts give it the Mu2e
servers, the site's model and the site's context at every start.

```bash
git clone https://github.com/Mu2e/aitools
aitools/harness/hep-genesis/desktop/install.sh --mode local     # or --mode ssh
cd ~/hep-genesis/desktop/desktop && npm run dev
```

- **`--mode local`**: the laptop is on the Fermilab network, on site or over
  the VPN. The servers are reached at their own addresses.
- **`--mode ssh`**: from anywhere. One ssh connection to a Mu2e machine
  forwards the servers' ports to the laptop. It needs a Kerberos ticket
  (`kinit`) before the app starts, stays up after the app is closed, and
  ends with `connect.py --stop`.

Needs: git with access to the private `Mu2e/hep-genesis-agent`, node 20 or
newer, python3, and ssh set up for Fermilab. macOS or Linux.

## What happens at a start

`install.sh` registers `connect.py` as the app's start hook
(`~/hep-genesis/start-hook.json`; the hook is described in the app's
`docs/RUNNING.md`). At every start the app runs it and gets:

- **the MCP servers** the site's `servers.json` switches on in the registry,
  the same list as on the gpvms, with your token. The token comes from
  `$MIKEY_TOKEN` or is fetched over ssh; it is kept in memory only.
- **the site's model endpoint** from `site/env.template`, added once. It
  needs the Fermilab network; other models are added in the Models panel.
- **the site's context** (`site/context.md`): which tool answers what.
- **the model presets**: the Models panel offers the Mu2e ones from
  `site/models.json` (Fermilab LiteLLM, ALCF inference) and the laptop's
  additions from `desktop/models.json` (NRP, AmSC-MAG), not the app's stock
  list, and has no ChatGPT section, as in the web UI. On a first start the
  app's stock endpoints are not added. Endpoints already in the app are not
  removed: delete those you do not want in the Models panel.

**The Fermilab models in `--mode ssh`.** vLLM and LiteLLM answer only on the
Fermilab network. `--mode ssh` reaches them through the same ssh connection:
a small relay on the laptop takes plain http on a local port and speaks
https to the lab's server through a forwarded port. The app is given the
local addresses, and their names end in "(over ssh)":

| Model service | Address in the app |
|---|---|
| vLLM (the site's default, no key) | `http://127.0.0.1:8451/v1` |
| LiteLLM (your Fermilab key) | `http://127.0.0.1:8452/v1` |

The ports are set in `desktop/models.json` ("relay"). An endpoint saved in
one mode does not work in the other: `--mode local` uses the lab's own
addresses. The page that issues LiteLLM keys (`llm-keys.fnal.gov`) is a web
page and still needs the Fermilab network or VPN in your browser.

In the Servers panel these servers are marked "site": they can be switched
off, not edited.

## When it does not work

The app starts anyway and a red notice gives the reason. To see it without
the app:

```bash
aitools/harness/hep-genesis/desktop/connect.py --mode ssh --check
```

| Message | Meaning |
|---|---|
| `ssh ... failed (...). Is there a Kerberos ticket (kinit)?` | no ticket, or ssh is not set up for Fermilab |
| `the server registry is not reachable ...` | `--mode local` away from the Fermilab network: start the VPN, or use `--mode ssh` |
| `port N on this machine is already in use` | another program has the port; `--mode ssh` forwards each server to its own port number |
| `Error: Electron uninstall` at `npm run dev` | Electron's program is missing: npm held back its install script, or that script stopped without unpacking. Run `install.sh` again; it fetches and unpacks it |
| a model answers `The relay could not reach ...` | the ssh connection is gone (the laptop slept, the ticket expired): restart the app |
| a server shows "error" in the panel | the token expired or the server is down; restart the app for a new token |

Not on the laptop: automations, the web UI's start-a-chat link and its
administrator mode. Those are parts of the web server (`../SERVICE.md`).
