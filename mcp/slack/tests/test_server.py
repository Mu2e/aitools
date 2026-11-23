"""slack-mcp tests with a fake Slack client -- no network."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from slack_mcp import server as S


# -- pure helpers ----------------------------------------------------------

def test_allowlist_parsing(monkeypatch):
    monkeypatch.setenv("SLACK_MCP_CHANNELS", "#mu2e-shift, C0123456789 test_llm")
    assert S.allowlist() == ["mu2e-shift", "C0123456789", "test_llm"]
    monkeypatch.setenv("SLACK_MCP_CHANNELS", "")
    assert S.allowlist() == []


def test_parse_time_forms():
    now = time.time()
    assert abs((now - S.parse_time("24h")) - 86400) < 5
    assert abs((now - S.parse_time("-7d")) - 7 * 86400) < 5
    assert abs((now - S.parse_time("now-30m")) - 1800) < 5
    assert S.parse_time(1700000000) == 1700000000.0
    assert S.parse_time("1700000000000") == 1700000000.0  # ms
    assert S.parse_time("2026-10-05T12:00:00+00:00") == 1791201600.0
    assert abs(S.parse_time(None) - (now - 86400)) < 5  # default: 24h ago
    with pytest.raises(ValueError):
        S.parse_time("yesterday")


def test_render_text_resolves_markup():
    names = {"U1": "Alice", "U2": "Bob"}
    out = S.render_text("<@U1> see <#C9|mu2e-shift> and <https://x.y/z|the log> or <https://a.b> &lt;ok&gt; <!here>",
                        lambda uid: names.get(uid, uid))
    assert out == "@Alice see #mu2e-shift and the log (https://x.y/z) or https://a.b <ok> @here"


def test_permalink():
    assert S.permalink("https://mu2e.slack.com/", "C1", "1791227465.254249") == \
        "https://mu2e.slack.com/archives/C1/p1791227465254249"
    assert S.permalink("https://mu2e.slack.com", "C1", "1791227470.1", "1791227465.254249").endswith(
        "?thread_ts=1791227465.254249&cid=C1")


# -- reader with a fake client -------------------------------------------

class FakeClient:
    def __init__(self):
        self.calls = []
        self.channels = [
            {"id": "C0000000001", "name": "mu2e-shift", "is_private": False, "topic": {"value": "shift"}, "purpose": {"value": ""}, "num_members": 40},
            {"id": "G0000000002", "name": "test_llm", "is_private": True, "topic": {"value": ""}, "purpose": {"value": "tests"}, "num_members": 3},
            {"id": "C0000000003", "name": "secret", "is_private": False, "topic": {"value": ""}, "purpose": {"value": ""}, "num_members": 9},
        ]
        self.history_rows = [
            {"ts": "100.000001", "user": "U1", "text": "run 125037 started <@U2>", "reply_count": 2},
            {"ts": "200.000002", "user": "U2", "text": "BR corruption again", "thread_ts": "200.000002", "reactions": [{"name": "eyes", "count": 2}]},
            {"ts": "150.000003", "bot_id": "B1", "username": "watchdog", "text": "report", "subtype": "bot_message"},
        ]

    def auth_test(self):
        return {"user": "mu2eshifterbot", "user_id": "UB", "team": "Mu2e", "url": "https://mu2e.slack.com/"}

    def conversations_list(self, types, limit, cursor, exclude_archived):
        self.calls.append(("list", types))
        return {"channels": [c for c in self.channels if c["is_private"] == (types == "private_channel")],
                "response_metadata": {"next_cursor": ""}}

    def conversations_info(self, channel):
        return {"channel": next(c for c in self.channels if c["id"] == channel)}

    def users_info(self, user):
        return {"user": {"real_name": {"U1": "Alice", "U2": "Bob"}[user]}}

    def conversations_history(self, channel, oldest, limit, cursor, latest=None):
        self.calls.append(("history", channel, oldest, latest, limit))
        rows = [r for r in self.history_rows if float(r["ts"]) >= float(oldest)]
        return {"messages": rows[:limit], "has_more": False}

    def conversations_replies(self, channel, ts, limit, cursor):
        self.calls.append(("replies", channel, ts))
        return {"messages": [{"ts": ts, "user": "U1", "text": "parent"},
                             {"ts": "101.0", "user": "U2", "text": "reply 1", "thread_ts": ts},
                             {"ts": "102.0", "user": "U2", "text": "reply 2", "thread_ts": ts}], "has_more": False}

    def chat_postMessage(self, channel, text, thread_ts=None, **options):
        self.calls.append(("post", channel, text, thread_ts, options))
        return {"ts": f"{900 + len([c for c in self.calls if c[0] == 'post'])}.000001"}

    def chat_delete(self, channel, ts):
        self.calls.append(("delete", channel, ts))
        if ts == "100.000001":
            raise RuntimeError("cant_delete_message")
        return {"ok": True}


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.setenv("SLACK_MCP_CHANNELS", "mu2e-shift,G0000000002")
    r = S.SlackReader(client=FakeClient())
    monkeypatch.setattr(S, "_reader", r)
    return r


def test_list_channels_only_allowlisted(reader):
    chans = S.list_channels()
    assert [c["name"] for c in chans] == ["mu2e-shift", "test_llm"]
    assert chans[0]["topic"] == "shift" and chans[1]["is_private"]
    assert not any(c["name"] == "secret" for c in chans)


def test_resolve_refuses_non_allowlisted(reader):
    assert reader.resolve("#mu2e-shift")["id"] == "C0000000001"
    assert reader.resolve("G0000000002")["name"] == "test_llm"
    with pytest.raises(ValueError) as e:
        reader.resolve("secret")
    assert "#mu2e-shift (C0000000001)" in str(e.value) and "#secret is not readable" in str(e.value)
    with pytest.raises(ValueError) as e:
        reader.resolve("C0000000003")  # by id either — and the id is named in the message
    assert "#secret (C0000000003) is not readable" in str(e.value)


def test_read_channel_formats_and_orders(reader):
    out = S.read_channel("mu2e-shift", since=0, limit=50)
    assert out["channel"] == {"id": "C0000000001", "name": "mu2e-shift"} and out["count"] == 3
    texts = [m["text"] for m in out["messages"]]
    assert texts == ["run 125037 started @Bob", "report", "BR corruption again"]  # oldest first
    first = out["messages"][0]
    assert first["user"] == "Alice" and first["reply_count"] == 2 and first["thread_ts"] == "100.000001"
    assert first["permalink"] == "https://mu2e.slack.com/archives/C0000000001/p100000001"
    assert out["messages"][1]["user"] == "watchdog" and out["messages"][1]["subtype"] == "bot_message"
    assert out["messages"][2]["reactions"] == {"eyes": 2}
    assert "replies" not in first


def test_read_channel_with_threads_and_thread_tool(reader):
    out = S.read_channel("mu2e-shift", since=0, include_threads=True)
    first = out["messages"][0]
    assert [r["text"] for r in first["replies"]] == ["reply 1", "reply 2"]
    assert first["replies"][0]["in_thread"] == "100.000001"
    th = S.read_thread("mu2e-shift", "100.000001")
    assert th["count"] == 3 and th["messages"][0]["text"] == "parent"


def test_tool_errors_are_tool_errors(reader):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError) as e:
        S.read_channel("secret")
    assert "bad_request" in str(e.value)


# -- posting -----------------------------------------------------------------

@pytest.fixture
def poster(reader, monkeypatch):
    monkeypatch.setenv("SLACK_MCP_POST_CHANNELS", "test_llm")
    monkeypatch.delenv("SLACK_MCP_POST_DRY_RUN", raising=False)
    monkeypatch.setenv("SLACK_MCP_POST_FOOTER", "")
    return reader


def posts(reader):
    return [c for c in reader.client.calls if c[0] == "post"]


def test_post_tool_only_with_a_post_list(monkeypatch):
    monkeypatch.delenv("SLACK_MCP_POST_CHANNELS", raising=False)
    assert S._register_post() is False and S.post_allowlist() == []
    assert S.get_server_info()["read_only"] is True


def test_post_refused_without_a_post_list(reader, monkeypatch):
    monkeypatch.delenv("SLACK_MCP_POST_CHANNELS", raising=False)
    with pytest.raises(ValueError):
        S.post_message("mu2e-shift", "hello")
    assert not posts(reader)


def test_post_only_to_the_post_list(poster):
    out = S.post_message("#test_llm", "**Digest**\n\n- see [the note](https://docdb.example/1)")
    assert out["posted"] and out["permalinks"] == ["https://mu2e.slack.com/archives/G0000000002/p901000001"]
    (_, channel, text, thread, options), = posts(poster)
    assert channel == "G0000000002" and thread is None
    assert text == "*Digest*\n\n\u2022 see <https://docdb.example/1|the note>"
    assert options == {"link_names": False, "unfurl_links": False, "unfurl_media": False}
    for readable_but_not_postable in ("mu2e-shift", "C0000000001", "secret"):
        with pytest.raises(ValueError) as e:
            S.post_message(readable_but_not_postable, "hello")
        assert "posts only to: #test_llm" in str(e.value)
    assert len(posts(poster)) == 1


def test_post_notifies_nobody(poster):
    S.post_message("test_llm", "<!channel> <@U1> <!subteam^S1> @here a < b & c\n```\n<!everyone>\n```")
    text = posts(poster)[0][2]
    assert "<" not in text and "&lt;!channel&gt; &lt;@U1&gt;" in text
    assert "@\u200bhere" in text and "a &lt; b &amp; c" in text and "&lt;!everyone&gt;" in text


def test_post_limits(poster, monkeypatch):
    with pytest.raises(ValueError):
        S.post_message("test_llm", "  ")
    with pytest.raises(ValueError):
        S.post_message("test_llm", "x" * (S.POST_MAX_CHARS + 1))
    with pytest.raises(ValueError):
        S.post_message("test_llm", "hi", thread_ts="../x")
    monkeypatch.setattr(S, "POST_PER_HOUR", 2)
    S.post_message("test_llm", "one")
    S.post_message("test_llm", "two", thread_ts="100.000001")
    assert posts(poster)[1][3] == "100.000001"
    with pytest.raises(ValueError) as e:
        S.post_message("test_llm", "three")
    assert "at most 2 messages an hour" in str(e.value) and len(posts(poster)) == 2


def test_post_long_text_is_split_and_fences_kept(poster):
    text = "intro\n```\n" + "\n".join(f"line {i:04d} " + "x" * 60 for i in range(120)) + "\n```\nend"
    out = S.post_message("test_llm", text)
    pieces = [c[2] for c in posts(poster)]
    assert out["messages"] == len(pieces) > 1 and all(len(p) <= 3500 for p in pieces)
    assert all(p.count("```") % 2 == 0 for p in pieces)
    assert "line 0000" in pieces[0] and "line 0119" in pieces[-1] and pieces[-1].rstrip().endswith("end")


def test_post_dry_run_and_log(poster, monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_MCP_POST_DRY_RUN", "1")
    out = S.post_message("test_llm", "**x**")
    assert "not a refusal" in out.pop("note")
    assert out == {"channel": {"id": "G0000000002", "name": "test_llm"}, "posted": False, "dry_run": True,
                   "ts": S.DRY_RUN_TS, "messages": ["*x*"]} and not posts(poster)
    # the ts of a dry run is accepted for a reply, which is not posted either
    assert S.post_message("test_llm", "more", thread_ts=out["ts"])["posted"] is False and not posts(poster)
    monkeypatch.delenv("SLACK_MCP_POST_DRY_RUN")
    monkeypatch.setenv("SLACK_MCP_POST_LOG", str(tmp_path / "posts.jsonl"))
    S.post_message("test_llm", "secret words")
    line = (tmp_path / "posts.jsonl").read_text()
    assert "p901000001" in line and "test_llm" in line and "secret words" not in line


def test_list_channels_marks_post_channels(poster, monkeypatch):
    assert [(c["name"], c["post"]) for c in S.list_channels()] == [("mu2e-shift", False), ("test_llm", True)]
    monkeypatch.setenv("SLACK_MCP_CHANNELS", "")
    rows = S.list_channels(refresh=True)                    # a copy that only posts
    assert [(c["name"], c["read"], c["post"]) for c in rows] == [("test_llm", False, True)]


def test_delete_by_permalink(poster, capsys):
    assert S.parse_permalink("https://mu2e.slack.com/archives/C0000000001/p1791227465254249?thread_ts=1.2&cid=C1") \
        == ("C0000000001", "1791227465.254249")
    with pytest.raises(ValueError):
        S.parse_permalink("https://mu2e.slack.com/archives/C0000000001")
    ok = "https://mu2e.slack.com/archives/G0000000002/p901000001"
    assert S._delete([ok]) == 0
    assert ("delete", "G0000000002", "901.000001") in poster.client.calls
    assert S._delete([ok, "https://mu2e.slack.com/archives/C0000000001/p100000001", "nonsense"]) == 1
    err = capsys.readouterr().err
    assert "not a message of this bot" in err and "not a link to a Slack message" in err


def test_http_refuses_a_post_list(poster):
    with pytest.raises(SystemExit) as e:
        S.main([])
    assert e.value.code == 2


def test_same_text_is_not_posted_twice(poster, monkeypatch, tmp_path):
    S.post_message("test_llm", "the digest")
    with pytest.raises(ValueError) as e:
        S.post_message("test_llm", " the digest\n")
    assert "already posted" in str(e.value) and len(posts(poster)) == 1
    S.post_message("test_llm", "the digest", thread_ts="100.000001")    # a reply is another post
    S.post_message("test_llm", "another digest")
    assert len(posts(poster)) == 3
    # A retry in a new process knows it from the post log.
    monkeypatch.setenv("SLACK_MCP_POST_LOG", str(tmp_path / "posts.jsonl"))
    S.post_message("test_llm", "logged digest")
    fresh = S.SlackReader(client=FakeClient())
    monkeypatch.setattr(S, "_reader", fresh)
    with pytest.raises(ValueError):
        S.post_message("test_llm", "logged digest")
    assert not posts(fresh)


def test_footer(poster, monkeypatch):
    monkeypatch.delenv("SLACK_MCP_POST_FOOTER")
    S.post_message("test_llm", "one\n")
    assert posts(poster)[-1][2] == "one\n\n_AI-generated; may contain mistakes._"
    monkeypatch.setenv("SLACK_MCP_POST_FOOTER", "_Daily digest, AI-generated._ [about](https://x.example/a)")
    S.post_message("test_llm", "two")
    assert posts(poster)[-1][2] == "two\n\n_Daily digest, AI-generated._ <https://x.example/a|about>"


def test_no_empty_lines_around_a_code_block():
    from slack_mcp.mrkdwn import to_mrkdwn
    assert to_mrkdwn("a\n\n```\nx < y\n```\n\n\nb") == "a\n```\nx &lt; y\n```\nb"


def test_post_returns_the_ts_for_a_reply(poster):
    first = S.post_message("test_llm", "the short post")
    assert first["ts"] and first["permalinks"]
    reply = S.post_message("test_llm", "more on one topic", thread_ts=first["ts"])
    assert reply["posted"] is True and reply["ts"] != first["ts"]
    assert [c[3] for c in posts(poster)] == [None, first["ts"]]
