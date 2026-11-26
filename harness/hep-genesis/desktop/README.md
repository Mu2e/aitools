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
| a server shows "error" in the panel | the token expired or the server is down; restart the app for a new token |

Not on the laptop: automations, the web UI's start-a-chat link and its
administrator mode. Those are parts of the web server (`../SERVICE.md`).
