"""The agent loop: one Conversation per Slack thread.

A plain OpenAI chat-completions loop against whatever endpoint is configured
(vllm.fnal.gov/gpt-oss today), with the MCP registry's tools as the only
capability. Conversation state lives in memory for as long as the thread is
active; the durable artifact is the usage record (see usage_log.py), not the
transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .mcp_tools import ToolImage, ToolRegistry

log = logging.getLogger(__name__)

COMPACT_TRIGGER = 0.8  # fraction of the context budget that forces a compaction

# Anthropic prompt caching, passed through LiteLLM's OpenAI-compatible API as
# cache_control markers. Three breakpoints (of the four allowed), in render
# order tools -> system -> messages:
#   1. the last tool definition -- identical for every thread and user;
#   2. the system prompt        -- identical for the life of a thread;
#   3. the newest message       -- the conversation so far, reused by the next
#      model call in the same turn (one per tool round trip) and the next turn.
# A cache hit needs a byte-identical prefix, which is why nothing in the
# system prompt changes per request (the time of day rides on each user
# message instead). Reads cost ~0.1x input, writes ~1.25x.
CACHE_MARK = {"type": "ephemeral"}

# Images shown to the model per turn, and the largest one it gets: hosted vision
# APIs refuse payloads over ~5 MB, and every image stays in the thread's history
# (sent again with each later request). Slack gets all of them regardless.
# The bot's own tool for putting an image in front of the user. Images the
# tools return go to the model; only those it names here reach the thread, and
# only names from this turn's tool results are accepted.
SHOW_IMAGE_TOOL = "slack__show_image"
SHOW_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": SHOW_IMAGE_TOOL,
        "description": (
            "Show images to the user: posts them to the Slack thread after your answer. Only images "
            "a tool returned in this turn (e.g. kb_get_image), by the file name given in its result. "
            "Use it when the user asked to see an image or it clearly helps the answer; images you "
            "only looked at yourself need not be shown."
        ),
        "parameters": {
            "type": "object",
            "properties": {"names": {"type": "array", "items": {"type": "string"},
                                     "description": "File names of the images to show"}},
            "required": ["names"],
        },
    },
}

MAX_VISION_IMAGES = 4
MAX_VISION_BYTES = 3_500_000
_NO_VISION_NOTE = "[image not shown to the model: it does not accept images; the user sees it in the thread]"

COMPACT_INSTRUCTION = (
    "Summarize the conversation so far for your own future reference. Keep the "
    "user's goal, every fact you established from tool calls (with document "
    "ids, run numbers, dataset names and links), and anything still open. Drop "
    "pleasantries and superseded detail. Write it as notes, not prose."
)


@dataclass
class Usage:
    turns: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    tool_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def tool_calls(self) -> int:
        return sum(self.tool_breakdown.values())


class Conversation:
    """One Slack thread's worth of state."""

    def __init__(self, key: str, cfg, client, tools: ToolRegistry, context: dict):
        self.key = key
        self.cfg = cfg
        self.client = client
        self.tools = tools
        self.context = context
        self.model = cfg.model  # per-thread, switchable with !model
        self.messages: list[dict] = []
        self.usage = Usage()
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at
        self.last_prompt_tokens = 0
        self.tool_notifications = cfg.tool_notifications
        # Images the tools returned in the current turn; the Slack side posts them.
        self.turn_images: list[ToolImage] = []
        # The ones the model chose to show the user (SHOW_IMAGE_TOOL).
        self.turn_shown: list[ToolImage] = []
        self.can_show_images = bool(getattr(cfg, "post_images", True))
        # Show them to the model too (vision); turned off for the thread when
        # its model rejects an image block, so a text-only model keeps working.
        self.vision = bool(getattr(cfg, "vision_tool_images", True))
        self.lock = asyncio.Lock()
        self.last_active = time.monotonic()

    # -- prompt ---------------------------------------------------------------

    def _system_prompt(self) -> dict:
        # No clock in here: anything that changes per request in the system
        # prompt defeats prompt caching for it and everything after it. The
        # time travels with each user message instead (see ask()).
        prefix = self.cfg.command_prefix
        extra = (
            f"\n\nEach user message starts with the local time it was sent, in brackets."
            f"\nThis conversation is a Slack thread; everything said in it is shared "
            f"context. Users can type {prefix}help for commands that control this "
            f"session (model, compaction, reset) -- mention that only if they ask "
            f"how to change something."
            f"\n\nContext: {json.dumps(self.context, indent=2)}"
        )
        return {"role": "system", "content": self.cfg.system_prompt + extra}

    # -- main loop ------------------------------------------------------------

    async def ask(self, text: str, on_tool=None) -> str:
        """Run one user turn to completion and return the reply text."""
        self.usage.turns += 1
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        self.messages.append({"role": "user", "content": f"[{now}] {text}"})

        # Reset per turn, not per conversation: asking the same question again
        # later is legitimate, repeating a call inside one turn is not.
        attempted: set[str] = set()
        self.turn_images = []
        self.turn_shown = []

        for _ in range(self.cfg.max_tool_iterations):
            message = await self._complete()

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                await self._maybe_compact()
                return message.get("content") or "(no answer)"

            new_images: list[ToolImage] = []
            for call in tool_calls:
                await self._run_tool(call, on_tool, attempted, new_images)
            # After all the tool results: the API wants every tool answer to
            # follow its call directly, and tool messages can only carry text.
            self._show_images(new_images)

        # Ran out of tool budget. Everything gathered so far is still in
        # self.messages, so a follow-up question can continue from here.
        return (
            f"I stopped after {self.cfg.max_tool_iterations} tool calls without "
            f"reaching an answer. Ask me to continue, or narrow the question."
        )

    def _caching(self) -> bool:
        mode = getattr(self.cfg, "prompt_cache", "auto")
        if mode == "auto":
            # Only Anthropic models understand cache_control; vLLM/gpt-oss may
            # reject the unknown field. Checked per call: !model can switch.
            return "claude" in self.model.lower()
        return mode == "on"

    async def _create(self):
        messages, schemas = self._request()
        return await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=schemas or None,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
        )

    def _request(self) -> tuple[list[dict], list[dict]]:
        """Messages and tools for one model call, with cache markers when
        caching is on. Marks go on copies: self.messages stays plain, so the
        history is the same whichever model reads it next."""
        system = self._system_prompt()
        plain = self._plain_messages()
        messages = [system] + plain
        schemas = self.tools.schemas
        if self.can_show_images:
            schemas = list(schemas) + [SHOW_IMAGE_SCHEMA]
        if not self._caching():
            return messages, schemas
        if schemas:
            schemas = schemas[:-1] + [{**schemas[-1], "cache_control": CACHE_MARK}]
        messages = [_marked(system)] + plain[:-1] + [_marked(plain[-1])]
        return messages, schemas

    def _show(self, arguments: dict) -> str:
        """SHOW_IMAGE_TOOL: mark images from this turn's tool results for the thread."""
        names = arguments.get("names")
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names:
            return "Error: give the file names of the images to show, as names=[...]."
        available = {i.name: i for i in self.turn_images}
        if not available:
            return "No image was returned by a tool in this turn, so there is nothing to show."
        shown, unknown = [], []
        for n in names:
            image = available.get(str(n).strip().rsplit("/", 1)[-1])
            if image is None:
                unknown.append(str(n))
            elif image not in self.turn_shown:
                self.turn_shown.append(image)
                shown.append(image.name)
        parts = []
        if shown:
            parts.append(f"Will be posted to the thread after your answer: {', '.join(shown)}.")
        if unknown:
            parts.append(f"Not from this turn's tool results, not shown: {', '.join(unknown)}. "
                         f"Available: {', '.join(available)}.")
        return " ".join(parts) or "Already marked to be shown."

    def _show_images(self, images: "list[ToolImage]") -> None:
        """Give a vision model the images the last tool calls returned."""
        if not (self.vision and images):
            return
        shown = [i for i in images if len(i.data) <= MAX_VISION_BYTES][:MAX_VISION_IMAGES]
        if not shown:
            return
        names = ", ".join(i.name for i in shown)
        blocks: list[dict] = [{"type": "text", "text": f"The images the tool returned ({names}):"}]
        blocks += [{"type": "image_url", "image_url": {"url": i.data_url}} for i in shown]
        self.messages.append({"role": "user", "content": blocks, "tool_images": True})

    def _plain_messages(self, without_images: bool = False) -> list[dict]:
        """The history as the API takes it: without our "tool_images" marker,
        and with the image blocks replaced by a note when ``without_images``."""
        out = []
        for m in self.messages:
            if "tool_images" not in m:
                out.append(m)
            elif without_images:
                out.append({"role": "user", "content": "[images were shown here]"})
            else:
                out.append({k: v for k, v in m.items() if k != "tool_images"})
        return out

    def _drop_images(self) -> bool:
        """Replace the image blocks in the history by a note; True if any were there."""
        dropped = False
        for i, m in enumerate(self.messages):
            if m.get("tool_images"):
                self.messages[i] = {"role": "user", "content": _NO_VISION_NOTE}
                dropped = True
        return dropped

    async def _complete(self) -> dict:
        try:
            response = await self._create()
        except Exception as exc:
            # A text-only model (gpt-oss on vLLM) rejects image blocks with a
            # 400. Drop them, keep the thread going without vision.
            if not (_is_bad_request(exc) and self._drop_images()):
                raise
            log.warning("model %s rejected an image (%s); continuing without vision in %s",
                        self.model, str(exc)[:120], self.key)
            self.vision = False
            response = await self._create()
        self._account(response)

        choice = response.choices[0].message
        # Rebuild the assistant message by hand rather than dumping the model
        # object: vLLM adds fields (reasoning_content and friends) that some
        # endpoints reject when they come back in the next request.
        message: dict = {"role": "assistant", "content": choice.content or ""}
        if choice.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in choice.tool_calls
            ]
        self.messages.append(message)
        self.updated_at = datetime.now(timezone.utc)
        self.last_active = time.monotonic()
        return message

    async def _run_tool(self, call: dict, on_tool, attempted: set[str],
                        images: "list[ToolImage] | None" = None) -> None:
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"] or "{}"
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError:
            arguments = {}
            result = f"Error: could not parse arguments for {name}: {raw_args!r}"
        else:
            # A model that gets an empty result tends to re-issue the same call
            # rather than accept the answer. Refusing the duplicate costs one
            # cheap round trip instead of a real one, and says plainly what to
            # do instead -- far more effective than prompt wording alone.
            signature = f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"
            if signature in attempted:
                self.messages.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "content": ("You already made this exact call in this turn and the "
                                "result is above. Repeating it will not change the answer. "
                                "Either take a materially different approach, or tell the "
                                "user what you found and what you could not find."),
                })
                return
            attempted.add(signature)

            self.usage.tool_breakdown[name] = self.usage.tool_breakdown.get(name, 0) + 1
            if on_tool is not None:
                await on_tool(name, arguments)
            if name == SHOW_IMAGE_TOOL:
                result = self._show(arguments)
            else:
                found: list[ToolImage] = []
                result = await self.tools.call(name, arguments, images=found)
                self.turn_images.extend(found)
                if images is not None:
                    images.extend(found)
                if found and self.can_show_images:
                    result += (f"\n(To show {'it' if len(found) == 1 else 'them'} to the user, call "
                               f"{SHOW_IMAGE_TOOL} with the file name; otherwise only you see it.)")

        self.messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": result,
        })

    def _account(self, response) -> None:
        self.usage.llm_calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.last_prompt_tokens = usage.prompt_tokens or 0
        self.usage.input_tokens += usage.prompt_tokens or 0
        self.usage.output_tokens += usage.completion_tokens or 0
        # LiteLLM passes Anthropic's counters through; prompt_tokens is the
        # total including both. Fall back to OpenAI's cached_tokens elsewhere.
        read = getattr(usage, "cache_read_input_tokens", None)
        if read is None:
            details = getattr(usage, "prompt_tokens_details", None)
            read = getattr(details, "cached_tokens", 0) if details is not None else 0
        self.usage.cache_read_tokens += read or 0
        self.usage.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            self.usage.thinking_tokens += getattr(details, "reasoning_tokens", 0) or 0

    # -- context management ---------------------------------------------------

    async def _maybe_compact(self) -> None:
        if self.last_prompt_tokens < COMPACT_TRIGGER * self.cfg.context_limit:
            return
        log.info("Auto-compacting %s at %d prompt tokens", self.key, self.last_prompt_tokens)
        await self.compact()

    async def compact(self) -> str:
        """Replace the transcript with a summary of itself. Returns the summary."""
        if not self.messages:
            return "Nothing to compact yet."

        response = await self.client.chat.completions.create(
            model=self.model,
            # A summary needs no pixels.
            messages=[self._system_prompt()] + self._plain_messages(without_images=True) + [
                {"role": "user", "content": COMPACT_INSTRUCTION}
            ],
            temperature=0.0,
            max_tokens=self.cfg.max_tokens,
        )
        self._account(response)
        summary = response.choices[0].message.content or ""

        self.messages = [
            {"role": "user", "content": f"Notes from earlier in this thread:\n\n{summary}"},
            {"role": "assistant", "content": "Noted -- carrying on from there."},
        ]
        return summary

    def reset(self) -> None:
        self.messages = []
        self.last_prompt_tokens = 0

    # -- backend protocol (see backend.py) ------------------------------------

    def set_model(self, name: str) -> None:
        self.model = name

    def status(self) -> dict:
        u = self.usage
        return {
            "model": self.model, "endpoint": self.cfg.endpoint,
            "turns": u.turns, "llm_calls": u.llm_calls, "tool_calls": u.tool_calls,
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens, "cache_write_tokens": u.cache_write_tokens,
            "last_prompt_tokens": self.last_prompt_tokens, "context_limit": self.cfg.context_limit,
            "tool_notifications": self.tool_notifications,
        }

    def links(self) -> dict[str, str]:
        return {}

    def usage_snapshot(self) -> dict:
        u = self.usage
        return {
            "endpoint_url": self.cfg.endpoint, "model": self.model,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "turns": u.turns, "llm_calls": u.llm_calls, "tool_calls": u.tool_calls,
            "tool_breakdown": dict(u.tool_breakdown),
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
            "thinking_tokens": u.thinking_tokens,
        }


def _marked(message: dict) -> dict:
    """A copy of ``message`` whose last content block carries a cache mark.
    Plain-string content becomes a single text block; a message with no text
    (an assistant turn that only calls tools) is returned unmarked."""
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content:
        blocks = list(content)
    else:
        return message
    blocks[-1] = {**blocks[-1], "cache_control": CACHE_MARK}
    return {**message, "content": blocks}


def _is_bad_request(exc: BaseException) -> bool:
    """An HTTP 400 from the model endpoint (openai.BadRequestError or alike)."""
    return getattr(exc, "status_code", None) == 400 or type(exc).__name__ == "BadRequestError"
