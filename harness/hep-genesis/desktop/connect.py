#!/usr/bin/env python3
"""Start hook of the hep-genesis desktop app at Mu2e (a laptop).

The app runs this at every start (~/hep-genesis/start-hook.json, written by
install.sh) and reads one JSON object from its output: the Mu2e MCP servers
with your token, the site's model endpoint, and the site's context. Nothing
but that object goes to stdout; messages go to stderr, and the app shows
the last of them when this fails.

    connect.py --mode local    on the Fermilab network (on site, or over
                               the VPN): the servers are reached at their
                               own addresses
    connect.py --mode ssh      from anywhere: one ssh connection to a Mu2e
                               machine forwards the servers' ports to this
                               laptop (needs a Kerberos ticket: kinit)
    connect.py --stop          end the ssh connection of --mode ssh
    connect.py --check ...     say what would be set up, without the token

The servers are the ones the site's servers.json switches on in its
registry, so the list is the same as on the gpvms. The token is $MIKEY_TOKEN
when set; otherwise it is fetched over ssh with the command servers.json
names. The ssh connection of --mode ssh stays up after the app is closed
and is used again at the next start.

For macOS and Linux (on Windows: inside WSL).

Environment: MU2E_SSH_HOST (default mu2egpvm01.fnal.gov), MU2E_SSH (the ssh
program and options, default "ssh"), MIKEY_TOKEN. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SITE = HERE.parent / "site"
MARK = "---mu2e-registry---"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "GSSAPIAuthentication=yes", "-o", "GSSAPIDelegateCredentials=yes"]


class Failed(Exception):
    """A reason the user can act on."""


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def state_dir() -> Path:
    d = Path.home() / "hep-genesis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ssh_base(host: str) -> list[str]:
    return shlex.split(os.environ.get("MU2E_SSH", "ssh")) + SSH_OPTIONS + [host]


def ssh_run(host: str, command: str, timeout: int = 25) -> str:
    try:
        r = subprocess.run(ssh_base(host) + [command], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        raise Failed("no ssh program on this machine")
    except subprocess.TimeoutExpired:
        raise Failed(f"ssh to {host} did not answer within {timeout} s")
    if r.returncode != 0:
        why = (r.stderr.strip().splitlines() or ["no message"])[-1]
        raise Failed(f"ssh to {host} failed ({why}). Is there a Kerberos ticket (kinit)?")
    return r.stdout


def site_servers(path: Path) -> tuple[str, dict, str]:
    """(registry address, {name: on/off}, the command that prints the token)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    registries = [r for r in data.get("registries", []) if r.get("url") and r.get("enabled") is not False]
    if not registries:
        raise Failed(f"{path} names no registry")
    reg = registries[0]
    auth = data.get("auth", {}).get(reg.get("token_auth", ""), {})
    return reg["url"], data.get("registry_enabled", {}), str(auth.get("token", {}).get("command", ""))


def registry_entries(payload: str) -> dict:
    try:
        data = json.loads(payload)
    except ValueError:
        raise Failed("the server registry did not answer with JSON")
    found = data.get("mcpServers", data) if isinstance(data, dict) else {}
    return {k: v for k, v in found.items() if isinstance(v, dict) and v.get("url")}


def over_local(registry: str, host: str, token_command: str) -> tuple[dict, str]:
    try:
        with urllib.request.urlopen(registry, timeout=10) as resp:
            entries = registry_entries(resp.read().decode("utf-8"))
    except Failed:
        raise
    except Exception as exc:  # no route, refused, timeout
        raise Failed(f"the server registry is not reachable ({exc}). Are you on the Fermilab network or its VPN? Otherwise use --mode ssh.")
    token = os.environ.get("MIKEY_TOKEN", "").strip()
    if not token and token_command:
        token = ssh_run(host, token_command).strip()
    return entries, token


def over_ssh(registry: str, host: str, token_command: str) -> tuple[dict, str]:
    """The registry's answer and the token, in one ssh call."""
    own = os.environ.get("MIKEY_TOKEN", "").strip()
    get_token = "true" if own or not token_command else token_command
    out = ssh_run(host, f"{get_token}; echo; echo {MARK}; curl -fsS --max-time 10 {shlex.quote(registry)}")
    token, sep, payload = out.partition(MARK)
    if not sep:
        raise Failed(f"unexpected answer from {host}")
    return registry_entries(payload.strip()), own or token.strip()


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def tunnel_file() -> Path:
    return state_dir() / "mu2e-tunnel.json"


def running_tunnel() -> dict | None:
    try:
        t = json.loads(tunnel_file().read_text(encoding="utf-8"))
        os.kill(int(t["pid"]), 0)
        return t
    except (OSError, ValueError, KeyError):
        return None


def stop_tunnel() -> bool:
    t = running_tunnel()
    if t:
        try:
            os.kill(int(t["pid"]), signal.SIGTERM)
        except OSError:
            pass
    tunnel_file().unlink(missing_ok=True)
    return bool(t)


def open_tunnel(host: str, targets: list[tuple[str, int]]) -> None:
    """Forward each (host, port) to the same port on this machine."""
    want = sorted({f"{h}:{p}" for h, p in targets})
    t = running_tunnel()
    if t and t.get("targets") == want and all(listening(p) for _, p in targets):
        return
    stop_tunnel()
    busy = [p for _, p in targets if listening(p)]
    if busy:
        raise Failed(f"port {busy[0]} on this machine is already in use by another program")
    forwards = [x for h, p in targets for x in ("-L", f"127.0.0.1:{p}:{h}:{p}")]
    argv = ssh_base(host)[:-1] + ["-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=60"] + forwards + [host]
    log = open(state_dir() / "mu2e-tunnel.log", "w")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    tunnel_file().write_text(json.dumps({"pid": proc.pid, "host": host, "targets": want}), encoding="utf-8")
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            tunnel_file().unlink(missing_ok=True)
            raise Failed(f"the ssh connection to {host} ended at once; see {log.name}")
        if all(listening(p) for _, p in targets):
            return
        time.sleep(0.3)
    stop_tunnel()
    raise Failed(f"the forwarded ports did not open within 15 s; see {log.name}")


def site_endpoint(env_template: Path) -> list[dict]:
    """The site's default model, from the same template the gpvm install uses."""
    values = {}
    try:
        for line in env_template.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(LLM_URL|LLM_MODEL|LLM_API_KEY)=(.*)$", line.strip())
            if m:
                values[m.group(1)] = m.group(2).strip()
    except OSError:
        return []
    if not values.get("LLM_URL") or not values.get("LLM_MODEL"):
        return []
    return [{"name": "Mu2e default", "baseUrl": values["LLM_URL"], "model": values["LLM_MODEL"],
             "apiKey": values.get("LLM_API_KEY", "")}]


def site_presets(site: Path, extra: Path) -> dict:
    """The Models panel's presets and whether it offers ChatGPT: the file the
    web UI reads on the gpvms, then the desktop's own additions. {} when
    there is neither."""
    out, seen = {}, set()
    for path in (site, extra):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for p in data.get("presets") or []:
            if isinstance(p, dict) and p.get("baseUrl") and p["baseUrl"] not in seen:
                seen.add(p["baseUrl"])
                out.setdefault("presets", []).append(p)
        if isinstance(data.get("chatgpt"), bool) and "chatgpt" not in out:
            out["chatgpt"] = data["chatgpt"]
    return out


def site_context(path: Path, connected: set[str]) -> str:
    """context.md as the agent reads it on the gpvms: comments dropped, a
    section headed "## Title [kb, dqm]" kept only with one of those servers."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    out, keep = [], True
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.S).split("\n"):
        if line.startswith("## "):
            m = re.match(r"^(##\s.*?)\s*\[([^\]]+)\]\s*$", line)
            keep = not m or bool({n.strip() for n in m.group(2).split(",")} & connected)
            line = m.group(1) if m else line
        if keep:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Start hook of the hep-genesis desktop app at Mu2e.")
    ap.add_argument("--mode", choices=("local", "ssh"), default="local")
    ap.add_argument("--ssh-host", default=os.environ.get("MU2E_SSH_HOST", "mu2egpvm01.fnal.gov"))
    ap.add_argument("--site", type=Path, default=SITE, help="folder with servers.json, env.template, models.json, context.md")
    ap.add_argument("--stop", action="store_true", help="end the ssh connection of --mode ssh")
    ap.add_argument("--check", action="store_true", help="print the result without the token")
    args = ap.parse_args()

    if args.stop:
        say("ssh connection ended" if stop_tunnel() else "no ssh connection was open")
        return 0
    try:
        registry, enabled, token_command = site_servers(args.site / "servers.json")
        fetch = over_ssh if args.mode == "ssh" else over_local
        entries, token = fetch(registry, args.ssh_host, token_command)
        chosen = {n: e for n, e in entries.items() if enabled.get(n)}
        if not chosen:
            raise Failed("the registry lists none of the servers the site switches on")
        if args.mode == "ssh":
            targets = []
            for e in chosen.values():
                u = urlparse(e["url"])
                if not u.hostname or not u.port:
                    raise Failed(f"cannot forward {e['url']}: no host or port")
                targets.append((u.hostname, u.port))
            open_tunnel(args.ssh_host, targets)
        servers = {}
        for name, e in chosen.items():
            url = e["url"]
            if args.mode == "ssh":
                u = urlparse(url)
                url = u._replace(netloc=f"127.0.0.1:{u.port}").geturl()
            needs_token = str(e.get("token", "no")).lower() in ("yes", "true", "1")
            if needs_token and not token:
                raise Failed("no token: set MIKEY_TOKEN, or check that ssh reaches a Mu2e machine")
            servers[name] = {"url": url}
            if needs_token:
                servers[name]["headers"] = {"Authorization": "Bearer " + ("<token>" if args.check else token)}
        result = {"servers": servers, "endpoints": site_endpoint(args.site / "env.template"),
                  "context": site_context(args.site / "context.md", set(servers)),
                  **site_presets(args.site / "models.json", HERE / "models.json")}
    except Failed as exc:
        say(f"Mu2e connection ({args.mode}): {exc}")
        return 1
    except (OSError, ValueError) as exc:
        say(f"Mu2e connection ({args.mode}): {exc}")
        return 1
    say(f"Mu2e connection ({args.mode}): {len(servers)} servers: {', '.join(sorted(servers))}")
    json.dump(result, sys.stdout, indent=2 if args.check else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
