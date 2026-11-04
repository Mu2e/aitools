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

    all_files = sorted(
        PROJECTS_ROOT.rglob("*.jsonl"),
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

def parse_session(jsonl_path: Path) -> dict:
    lines = []
    with open(jsonl_path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

    session_id  = jsonl_path.stem
    model       = None
    timestamps  = []
    turns       = 0
    llm_calls   = 0
    tool_calls  = Counter()
    cost_state  = None  # last cost-state entry = session-level totals
    msg_usage   = {}    # message.id -> usage (fallback when no cost-state)

    for d in lines:
        t = d.get("type")
        ts = d.get("timestamp")
        if ts:
            timestamps.append(ts)

        if t == "user":
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

    # Extract cumulative token counts from cost-state (best source).
    # Falls back to summing per-message usage when there is no cost-state.
    input_tok   = 0
    output_tok  = 0
    cache_read  = 0
    cache_write = 0
    think_tok   = 0

    if cost_state:
        # model_usage is a dict keyed by model name -- sum across all models
        # (shouldn't be more than one in practice, but be safe)
        for mu in (cost_state.get("modelUsage") or {}).values():
            input_tok   += mu.get("inputTokens", 0)
            output_tok  += mu.get("outputTokens", 0)
            cache_read  += mu.get("cacheReadInputTokens", 0)
            cache_write += mu.get("cacheCreationInputTokens", 0)
            think_tok   += mu.get("thinkingTokens", 0)
        # cost_usd intentionally not logged (privacy / not needed)
        # Use the model from cost-state if we haven't found one yet
        if not model and cost_state.get("modelUsage"):
            model = next(iter(cost_state["modelUsage"]))
    else:
        # Newer Claude Code versions no longer write cost-state records;
        # sum the per-message API usage instead.
        for u in msg_usage.values():
            input_tok   += u.get("input_tokens") or 0
            output_tok  += u.get("output_tokens") or 0
            cache_read  += u.get("cache_read_input_tokens") or 0
            cache_write += u.get("cache_creation_input_tokens") or 0
            think_tok   += (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0

    return {
        "session_id":         session_id,
        "model":              model,
        "turns":              turns,
        "llm_calls":          llm_calls,
        "tool_calls_total":   sum(tool_calls.values()),
        "tool_breakdown":     dict(tool_calls),
        "input_tokens":       input_tok,
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

def _endpoint_url() -> str:
    """ANTHROPIC_BASE_URL (env.sh sources it from .env); read .env directly as
    a fallback so the hook subprocess has it too. Empty means Anthropic's API."""
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return os.environ["ANTHROPIC_BASE_URL"]
    claude_home = os.environ.get("CLAUDE_HOME") or \
                  f"/exp/mu2e/app/users/{os.environ.get('USER', '')}/claude-code/home"
    env_file = Path(claude_home) / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export ANTHROPIC_BASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def build_record(parsed: dict) -> dict:
    endpoint = _endpoint_url()
    return usage_record.make_record(
        "claude-code", parsed["session_id"],
        session_created_at=usage_record.utc_iso(parsed["session_created_at"]),
        session_updated_at=usage_record.utc_iso(parsed["session_updated_at"]),
        provider=usage_record.provider_from_endpoint(endpoint, default="anthropic"),
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

    print(f"Logged session {record['session_id']}: "
          f"{record['turns']} turns, "
          f"{record['input_tokens']}+{record['output_tokens']} tokens "
          f"({record['model'] or 'model unknown'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
