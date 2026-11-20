"""Images a tool returns: posted to the Slack thread, shown to a vision model,
never pasted into the model's text as base64 -- no LLM, no network."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace as NS

import pytest
from slack_sdk.errors import SlackApiError

from mu2e_slack import agent, mcp_tools, slackbot
from mu2e_slack.agent import Conversation
from mu2e_slack.config import parse_args
from mu2e_slack.mcp_tools import ToolImage, _result_text

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 2000
B64 = base64.b64encode(PNG).decode()


def _image_item(data=B64, mime="image/png"):
    return NS(type="image", data=data, mimeType=mime)


def _text_item(text):
    return NS(type="text", text=text)


# -- the tool result ----------------------------------------------------------

def test_an_image_goes_to_the_list_not_into_the_text():
    images: list[ToolImage] = []
    result = NS(content=[_text_item("Figure 23 of DocDB 59019"), _image_item()], isError=False)
    text = _result_text(result, images, {"identifier": "mu2e-docdb/59019",
                                         "image_filename": "59019-sesaps_mu2e_genesis-_page_15_Figure_23.png"})
    assert B64[:40] not in text
    assert "Figure 23 of DocDB 59019" in text and "[image 59019-sesaps_mu2e_genesis-_page_15_Figure_23.png" in text
    (img,) = images
    assert img.data == PNG and img.mime == "image/png"
    assert img.name == "59019-sesaps_mu2e_genesis-_page_15_Figure_23.png"
    assert img.data_url.startswith("data:image/png;base64,")


def test_without_a_collector_the_image_is_only_named():
    text = _result_text(NS(content=[_image_item()], isError=False))
    assert "not shown" in text and B64[:40] not in text


def test_several_images_from_one_call_get_distinct_names():
    images: list[ToolImage] = []
    _result_text(NS(content=[_image_item(), _image_item(mime="image/jpeg"), _image_item()], isError=False),
                 images, {"identifier": "doc/fig"})
    assert [i.name for i in images] == ["fig.png", "fig-2.jpg", "fig-3.png"]


def test_an_undecodable_image_is_reported_not_raised():
    images: list[ToolImage] = []
    text = _result_text(NS(content=[_image_item(data="not base64!!")], isError=False), images)
    assert "could not be decoded" in text and images == []


# -- the conversation ---------------------------------------------------------

class Tools:
    schemas = [{"type": "function", "function": {"name": "kb__kb_get_image", "parameters": {}}}]

    def __init__(self, images=(PNG,)):
        self._images = images

    async def retry_failed(self):
        return []

    async def call(self, name, arguments, images=None):
        for data in self._images:
            images.append(ToolImage(data=data, mime="image/png", name="fig.png"))
        return "[image fig.png (2 KB, image/png)]"


def _response(content="", tool_calls=None):
    msg = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=msg)], usage=None)


def _tool_call(i="c1"):
    return NS(id=i, function=NS(name="kb__kb_get_image", arguments=json.dumps({"identifier": "fig"})))


def _has_image(messages):
    return any(isinstance(m.get("content"), list) and any(b.get("type") == "image_url" for b in m["content"])
               for m in messages)


class BadRequest(Exception):
    status_code = 400


class Client:
    """Answers the first request with a tool call, every later one with text;
    records what it was sent. ``reject_images`` mimics a text-only model."""

    def __init__(self, reject_images=False):
        self.sent: list[list[dict]] = []
        self.reject_images = reject_images
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        messages = kwargs["messages"]
        self.sent.append(messages)
        if self.reject_images and _has_image(messages):
            raise BadRequest("this model does not support image input")
        if not any(m.get("role") == "tool" for m in messages):
            return _response(tool_calls=[_tool_call()])
        return _response("Here is the diagram.")


def _conv(client, tools=None, vision=True, post_images=True):
    cfg = NS(model="azure/claude-sonnet-5-5", prompt_cache="off", command_prefix="!", system_prompt="You help.",
             tool_notifications=True, max_tool_iterations=4, temperature=0.0, max_tokens=100,
             context_limit=128000, vision_tool_images=vision, post_images=post_images)
    return Conversation("k", cfg, client=client, tools=tools or Tools(), context={"channel": "C"})


def test_a_vision_model_gets_the_image_after_the_tool_results():
    client = Client()
    conv = _conv(client)
    answer = asyncio.run(conv.ask("show me the MCP diagram"))
    assert answer == "Here is the diagram."
    assert [i.name for i in conv.turn_images] == ["fig.png"]
    second = client.sent[1]
    roles = [m["role"] for m in second]
    assert roles[-3:] == ["assistant", "tool", "user"]        # tool answer first, then the image
    blocks = second[-1]["content"]
    assert blocks[0]["type"] == "text" and blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert all("tool_images" not in m for m in second)          # our marker never reaches the API


def test_without_vision_the_model_gets_only_the_note():
    client = Client()
    conv = _conv(client, vision=False)
    asyncio.run(conv.ask("show me"))
    assert conv.turn_images and not any(isinstance(m.get("content"), list) for m in client.sent[1])


def test_a_model_that_rejects_images_falls_back_to_text():
    client = Client(reject_images=True)
    conv = _conv(client)
    answer = asyncio.run(conv.ask("show me"))
    assert answer == "Here is the diagram."
    assert conv.vision is False and conv.turn_images            # Slack still gets the image
    last = client.sent[-1]
    assert not any(isinstance(m.get("content"), list) for m in last)
    assert any(m.get("content") == agent._NO_VISION_NOTE for m in last)


def test_oversized_images_are_not_shown_to_the_model():
    client = Client()
    conv = _conv(client, tools=Tools(images=(b"x" * (agent.MAX_VISION_BYTES + 1),)))
    asyncio.run(conv.ask("show me"))
    assert conv.turn_images and not any(isinstance(m.get("content"), list) for m in client.sent[1])


def test_compaction_sends_no_pixels():
    client = Client()
    conv = _conv(client)
    asyncio.run(conv.ask("show me"))
    client.sent.clear()

    async def summarise(**kwargs):
        client.sent.append(kwargs["messages"])
        return _response("summary")
    conv.client = NS(chat=NS(completions=NS(create=summarise)))
    asyncio.run(conv.compact())
    assert "base64" not in json.dumps(client.sent[0])


# -- posting to Slack ---------------------------------------------------------

class Web:
    def __init__(self, error=None):
        self.uploads = []
        self.error = error

    async def files_upload_v2(self, **kwargs):
        if self.error:
            raise SlackApiError("nope", {"error": self.error})
        self.uploads.append(kwargs)


def _bot(web, post_images=True):
    bot = slackbot.SlackBot.__new__(slackbot.SlackBot)
    bot.cfg = NS(post_images=post_images)
    bot.web = web
    bot._images_disabled = False
    return bot


def test_images_are_uploaded_to_the_thread():
    web = Web()
    images = [ToolImage(data=PNG, mime="image/png", name=f"f{i}.png") for i in range(slackbot.MAX_SLACK_IMAGES + 2)]
    asyncio.run(_bot(web)._post_images("C1", "123.4", images))
    assert len(web.uploads) == slackbot.MAX_SLACK_IMAGES
    first = web.uploads[0]
    assert first["channel"] == "C1" and first["thread_ts"] == "123.4"
    assert first["content"] == PNG and first["filename"] == "f0.png"


def test_a_missing_scope_turns_posting_off_without_failing():
    bot = _bot(Web(error="missing_scope"))
    asyncio.run(bot._post_images("C1", "1", [ToolImage(PNG, "image/png", "a.png")]))
    assert bot._images_disabled is True
    bot.web = Web()
    asyncio.run(bot._post_images("C1", "1", [ToolImage(PNG, "image/png", "a.png")]))
    assert bot.web.uploads == []


@pytest.mark.parametrize("value", [None, []])
def test_nothing_to_post(value):
    web = Web()
    asyncio.run(_bot(web)._post_images("C1", "1", value))
    assert web.uploads == []


def test_switches(monkeypatch):
    for name in ("MU2E_SLACK_POST_IMAGES", "MU2E_SLACK_VISION"):
        monkeypatch.delenv(name, raising=False)
    cfg, _ = parse_args([])
    assert cfg.post_images is True and cfg.vision_tool_images is True
    monkeypatch.setenv("MU2E_SLACK_VISION", "0")
    cfg, _ = parse_args(["--no-post-images"])
    assert cfg.post_images is False and cfg.vision_tool_images is False


# -- the model decides what the user sees (slack__show_image) -----------------

class ScriptedClient:
    """Plays back a fixed list of replies; records the requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent: list[list[dict]] = []
        self.tools_sent: list[list[dict]] = []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        self.sent.append(kwargs["messages"])
        self.tools_sent.append(kwargs.get("tools") or [])
        return self.replies.pop(0)


def _call(name, args, i):
    return NS(id=i, function=NS(name=name, arguments=json.dumps(args)))


def test_the_show_tool_is_offered_only_when_images_are_posted():
    for post, expected in ((True, True), (False, False)):
        client = ScriptedClient(_response("hi"))
        asyncio.run(_conv(client, post_images=post).ask("hello"))
        names = [t["function"]["name"] for t in client.tools_sent[0]]
        assert (agent.SHOW_IMAGE_TOOL in names) is expected


def test_a_fetched_image_reaches_the_thread_only_when_the_model_shows_it():
    client = ScriptedClient(
        _response(tool_calls=[_call("kb__kb_get_image", {"identifier": "fig"}, "c1")]),
        _response(tool_calls=[_call(agent.SHOW_IMAGE_TOOL, {"names": ["fig.png"]}, "c2")]),
        _response("Here is the diagram."),
    )
    conv = _conv(client)
    assert asyncio.run(conv.ask("show me the diagram")) == "Here is the diagram."
    assert [i.name for i in conv.turn_images] == ["fig.png"] and [i.name for i in conv.turn_shown] == ["fig.png"]
    tool_results = [m["content"] for m in conv.messages if m.get("role") == "tool"]
    assert agent.SHOW_IMAGE_TOOL in tool_results[0]                     # the hint after the image
    assert "Will be posted to the thread" in tool_results[1]


def test_an_image_the_model_only_looked_at_is_not_posted():
    client = ScriptedClient(
        _response(tool_calls=[_call("kb__kb_get_image", {"identifier": "fig"}, "c1")]),
        _response("The y axis is the momentum in MeV/c."),
    )
    conv = _conv(client)
    asyncio.run(conv.ask("what is on the y axis?"))
    assert conv.turn_images and conv.turn_shown == []


def test_only_recent_images_can_be_shown():
    conv = _conv(ScriptedClient())
    assert "nothing to show" in conv._show({"names": ["fig.png"]})
    conv.recent_images = {"fig.png": (1, ToolImage(PNG, "image/png", "fig.png"))}
    out = conv._show({"names": ["other.png", "fig.png", "fig.png"]})
    assert [i.name for i in conv.turn_shown] == ["fig.png"]
    assert "other.png" in out and "Available: fig.png" in out
    assert conv._show({"names": []}).startswith("Error")
    assert conv._show({"names": "fig.png"}) == "Already marked to be shown."   # a bare string works too


# -- how long images stay in the context --------------------------------------

def _fetch_then_answer():
    return [_response(tool_calls=[_call("kb__kb_get_image", {"identifier": "fig"}, "c1")]), _response("ok")]


def _images_in_context(conv):
    return sum(1 for m in conv.messages if "tool_images" in m)


def test_an_image_stays_for_two_more_turns_then_becomes_a_note():
    client = ScriptedClient(*_fetch_then_answer(), _response("a"), _response("b"), _response("c"))
    conv = _conv(client)
    asyncio.run(conv.ask("show me the figure"))            # turn 1: fetched
    for turn in ("and the inset?", "and the axis?"):       # turns 2 and 3: still there
        asyncio.run(conv.ask(turn))
        assert _images_in_context(conv) == 1 and "fig.png" in conv.recent_images
    asyncio.run(conv.ask("something else"))                # turn 4: gone
    assert _images_in_context(conv) == 0 and conv.recent_images == {}
    notes = [m["content"] for m in conv.messages if isinstance(m.get("content"), str) and "was shown here" in m["content"]]
    assert notes and "fig.png" in notes[0] and "kb_get_image" in notes[0]
    assert "base64" not in json.dumps(client.sent[-1])


def test_an_idle_thread_drops_its_images():
    client = ScriptedClient(*_fetch_then_answer(), _response("a"))
    conv = _conv(client)
    asyncio.run(conv.ask("show me the figure"))
    conv.last_active -= agent.IMAGE_IDLE_SECONDS + 1        # the prompt cache has gone cold
    asyncio.run(conv.ask("and the inset?"))
    assert _images_in_context(conv) == 0 and conv.recent_images == {}


def test_an_earlier_image_can_be_shown_while_it_is_in_context():
    client = ScriptedClient(*_fetch_then_answer(),
                            _response(tool_calls=[_call(agent.SHOW_IMAGE_TOOL, {"names": ["fig.png"]}, "c2")]),
                            _response("posted"))
    conv = _conv(client)
    asyncio.run(conv.ask("what is in this figure?"))       # looked at, not shown
    assert conv.turn_shown == []
    asyncio.run(conv.ask("post it please"))                # next turn: shown without a new fetch
    assert [i.name for i in conv.turn_shown] == ["fig.png"]


def test_reset_and_compaction_forget_the_images():
    conv = _conv(ScriptedClient(*_fetch_then_answer()))
    asyncio.run(conv.ask("show me"))
    assert conv.recent_images
    conv.reset()
    assert conv.recent_images == {}
    conv2 = _conv(ScriptedClient(*_fetch_then_answer(), _response("summary")))
    asyncio.run(conv2.ask("show me"))
    asyncio.run(conv2.compact())
    assert conv2.recent_images == {} and _images_in_context(conv2) == 0
