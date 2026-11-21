#!/usr/bin/env python3
"""
log-session-claude.py  --  extract per-session usage summary from Claude Code's
                            JSONL transcript files and write to a flat JSON-lines
                            file (mirroring what goose/log-session.py does for goose).

Usage:
    log-session-claude.py [SESSION_ID]

    SESSION_ID  A Claude Code session UUID (e.g. "d6e1339a-2731-4ff7-...").
                Defaults to the most-recently-updated session across all projects.

Output:
    Upserts one JSON line per session into $LOG_OUTPUT
    (default: $CLAUDE_ROOT/log/usage.jsonl, i.e.
    /exp/mu2e/app/users/$USER/claude-code/log/usage.jsonl).

    Records have exactly the columns of the shared table usage.sessions
    (../usage/sessions.sql), interface "claude-code" -- the same shape the
    Slack bot and goose write; see ../usage/usage_record.py. No user,
    working directory or content is recorded; session_id is a hash.
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usage"))
import usage_record  # noqa: E402  (harness/usage/usage_record.py)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

def _default_projects_root() -> Path:
    claude_home = os.environ.get("CLAUDE_HOME") or \
                  f"/exp/mu2e/app/users/{os.environ.get('USER', 'unknown')}/claude-code/home"
    return Path(claude_home) / ".claude" / "projects"


def _default_output() -> Path:
    # CLAUDE_ROOT (exported by env.sh) is the harness root; CLAUDE_HOME is
    # its home/ subdir and must not be used here.
    claude_root = os.environ.get("CLAUDE_ROOT") or \
                  f"/exp/mu2e/app/users/{os.environ.get('USER', 'unknown')}/claude-code"
    return Path(claude_root) / "log" / "usage.jsonl"


PROJECTS_ROOT = Path(os.environ.get("LOG_PROJECTS_ROOT", _default_projects_root()))
OUTPUT_FILE   = Path(os.environ.get("LOG_OUTPUT",         _default_output()))


# ---------------------------------------------------------------------------
# FIND SESSION FILE
# ---------------------------------------------------------------------------

def find_session_file(session_id: Optional[str]) -> Optional[Path]:
    """Return the JSONL file for the given session_id, or the most recently
    modified one if session_id is None."""
    if not PROJECTS_ROOT.exists():
        return None

    # A session is projects/<project>/<session>.jsonl; the transcripts of its
    # subagents are deeper (<session>/subagents/) and are not sessions.
    all_files = sorted(
        (p for p in PROJECTS_ROOT.rglob("*.jsonl") if p.parent.name != "subagents"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not all_files:
        return None

    if session_id is None:
        return all_files[0]

    for f in all_files:
        if f.stem == session_id:
            return f
    return None


# ---------------------------------------------------------------------------
# PARSE TRANSCRIPT
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def _is_prompt(d: dict) -> bool:
    """A "user" record the person wrote. Tool results come back as "user"
    records too, as do notes the client adds itself; neither is a turn."""
    if d.get("isMeta") or d.get("isSidechain"):
        return False
    content = (d.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    blocks = [b for b in content or [] if isinstance(b, dict)]
    return bool(blocks) and not any(b.get("type") == "tool_result" for b in blocks)


def parse_session(jsonl_path: Path) -> dict:
    lines = [(d, False) for d in _read_jsonl(jsonl_path)]
    # What the session's subagents did is in files of their own, not in the
    # session's transcript: their model calls, tokens and tool calls belong
    # to the session's totals all the same.
    for sub in sorted((jsonl_path.parent / jsonl_path.stem / "subagents").glob("*.jsonl")):
        lines += [(d, True) for d in _read_jsonl(sub)]

    session_id  = jsonl_path.stem
    model       = None
    timestamps  = []
    turns       = 0
    llm_calls   = 0
    tool_calls  = Counter()
    cost_state  = None  # last cost-state entry = session-level totals
    msg_usage   = {}    # message.id -> usage
    msg_model   = {}    # message.id -> model

    for d, from_subagent in lines:
        t = d.get("type")
        ts = d.get("timestamp")
        if ts:
            timestamps.append(ts)

        if t == "user":
            if not from_subagent and _is_prompt(d):
                turns += 1

        elif t == "assistant":
            msg = d.get("message", {})
            m = msg.get("model", "")
            if m == "<synthetic>":
                continue  # client-side placeholder, not an API call
            # One API response can be split over several transcript records
            # sharing message.id; count it (and its usage) once.
            mid = msg.get("id") or d.get("uuid")
            if mid not in msg_usage:
                llm_calls += 1
            msg_usage[mid] = msg.get("usage") or {}
            msg_model[mid] = m
            if not model and m:
                model = m
            # Tool use blocks in assistant content
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls[block.get("name", "unknown")] += 1

        elif t == "cost-state":
            # Prefer the last cost-state -- it's the running cumulative total.
            # This is the same data /usage shows inside Claude Code.
            cost_state = d

    # Sort timestamps for created/updated
    timestamps.sort()
    created_at = timestamps[0]  if timestamps else None
    updated_at = timestamps[-1] if timestamps else None

    # Token totals. Two sources, and each can be behind the other:
    #   - cost-state records: Claude Code's own running totals per model.
    #     They include calls that leave no message in the transcript, but are
    #     only written now and then, so the last one can be well behind in a
    #     long session; newer versions do not write them at all.
    #   - the usage of every message, of the session and its subagents.
    # Both are lower bounds of what the session used: take the larger.
    def totals(rows):
        t = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0}
        for r in rows:
            for k in t:
                t[k] += r.get(k) or 0
        return t

    by_state = totals(
        {"input": mu.get("inputTokens"), "output": mu.get("outputTokens"),
         "cache_read": mu.get("cacheReadInputTokens"), "cache_write": mu.get("cacheCreationInputTokens"),
         "thinking": mu.get("thinkingTokens")}
        for mu in ((cost_state or {}).get("modelUsage") or {}).values())
    by_message = totals(
        {"input": u.get("input_tokens"), "output": u.get("output_tokens"),
         "cache_read": u.get("cache_read_input_tokens"), "cache_write": u.get("cache_creation_input_tokens"),
         "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens")}
        for u in msg_usage.values())
    whole = lambda t: t["input"] + t["cache_read"] + t["cache_write"]  # noqa: E731
    best = by_state if whole(by_state) > whole(by_message) else by_message
    # The prompt counts belong together and come from one source; output and
    # thinking are each the larger of the two.
    input_tok, cache_read, cache_write = best["input"], best["cache_read"], best["cache_write"]
    output_tok = max(by_state["output"], by_message["output"])
    think_tok = max(by_state["thinking"], by_message["thinking"])
    # cost_usd intentionally not logged (privacy / not needed)
    # The record has one model: the one most of the prompt tokens went to
    # (a session can switch models, and subagents may use another).
    per_model: dict = {}
    for mid, u in msg_usage.items():
        if msg_model.get(mid):
            per_model[msg_model[mid]] = per_model.get(msg_model[mid], 0) + (u.get("input_tokens") or 0) \
                + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
    if per_model:
        model = max(per_model, key=per_model.get)
    elif not model and (cost_state or {}).get("modelUsage"):
        model = next(iter(cost_state["modelUsage"]))

    return {
        "session_id":         session_id,
        "model":              model,
        "turns":              turns,
        "llm_calls":          llm_calls,
        "tool_calls_total":   sum(tool_calls.values()),
        "tool_breakdown":     dict(tool_calls),
        # The shared convention (usage_record.py): input_tokens is the whole
        # prompt, cache reads and writes included. Anthropic's API, and so
        # the transcript, counts the uncached part only.
        "input_tokens":       input_tok + cache_read + cache_write,
        "output_tokens":      output_tok,
        "cache_read_tokens":  cache_read,
        "cache_write_tokens": cache_write,
        "thinking_tokens":    think_tok,
        "session_created_at": created_at,
        "session_updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# ASSEMBLE LOG RECORD
# ---------------------------------------------------------------------------

def _backend_setting(name: str) -> str:
    """A backend variable such as ANTHROPIC_BASE_URL: from the environment
    (claude is started with .env loaded, so the hook inherits it), else read
    from .env directly. Empty if not set."""
    if os.environ.get(name):
        return os.environ[name]
    claude_home = os.environ.get("CLAUDE_HOME") or \
                  f"/exp/mu2e/app/users/{os.environ.get('USER', '')}/claude-code/home"
    env_file = Path(claude_home) / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"export {name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _provider(endpoint: str) -> str:
    """The gateway of a custom endpoint ("litellm"); for Anthropic's own API
    "anthropic" with an API key and "claude.ai" with a subscription login.

    Both of those talk to api.anthropic.com, so they are told apart by the
    credentials: a key (environment, .env or an apiKeyHelper) takes
    precedence in Claude Code, so only without one is the OAuth login that
    /login stores in .claude/.credentials.json what the session ran on."""
    if endpoint:
        return usage_record.provider_from_endpoint(endpoint, default="anthropic")
    if _backend_setting("ANTHROPIC_API_KEY") or _backend_setting("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    claude_dir = PROJECTS_ROOT.parent   # .claude/ of the home the session ran in
    try:
        if "apiKeyHelper" in json.loads((claude_dir / "settings.json").read_text()):
            return "anthropic"
    except (OSError, ValueError):
        pass
    try:
        if (claude_dir / ".credentials.json").stat().st_size > 0:
            return "claude.ai"
    except OSError:
        pass
    return "anthropic"


def build_record(parsed: dict) -> dict:
    endpoint = _backend_setting("ANTHROPIC_BASE_URL")
    return usage_record.make_record(
        "claude-code", parsed["session_id"],
        session_created_at=usage_record.utc_iso(parsed["session_created_at"]),
        session_updated_at=usage_record.utc_iso(parsed["session_updated_at"]),
        provider=_provider(endpoint),
        endpoint_url=endpoint or "https://api.anthropic.com",
        model=parsed.get("model") or None,
        turns=parsed["turns"],
        llm_calls=parsed["llm_calls"],
        tool_calls=parsed["tool_calls_total"],
        tool_breakdown=parsed["tool_breakdown"],
        input_tokens=parsed["input_tokens"],
        output_tokens=parsed["output_tokens"],
        cache_read_tokens=parsed["cache_read_tokens"],
        cache_write_tokens=parsed["cache_write_tokens"],
        thinking_tokens=parsed["thinking_tokens"],
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else None

    jsonl_path = find_session_file(session_id)
    if jsonl_path is None:
        if session_id:
            print(f"Error: session {session_id!r} not found under {PROJECTS_ROOT}",
                  file=sys.stderr)
        else:
            print(f"Error: no session files found under {PROJECTS_ROOT}",
                  file=sys.stderr)
        return 1

    parsed = parse_session(jsonl_path)
    record = build_record(parsed)
    usage_record.write_jsonl(record, OUTPUT_FILE)
    # Also to the usage log service ($LOG_URL), which inserts it into the
    # shared table; when it cannot be reached the record waits in a backlog.
    usage_record.send(record, usage_record.backlog_path(OUTPUT_FILE))

    print(f"Logged session {record['session_id']}: "
          f"{record['turns']} turns, "
          f"{record['input_tokens']}+{record['output_tokens']} tokens "
          f"({record['model'] or 'model unknown'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
