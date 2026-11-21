"""Entries carry the address of their page -- no network, a stand-in client."""

from __future__ import annotations

from ecl_mcp import server as S


class FakeECL:
    _url = "https://dbweb9.fnal.gov:8443/ECL/mu2e/E"

    def search(self, **kwargs):
        return [{"id": 6219, "text": "a"}, {"id": 6220, "text": "b"}]

    def get_entry(self, entry_id):
        return {"id": entry_id, "text": "a"}


def test_entries_carry_their_url(monkeypatch):
    monkeypatch.setattr(S, "_ecl_instance", FakeECL())
    found = S.search_entries(after="1days")
    assert [e["url"] for e in found] == [
        "https://dbweb9.fnal.gov:8443/ECL/mu2e/E/show?e=6219",
        "https://dbweb9.fnal.gov:8443/ECL/mu2e/E/show?e=6220"]
    assert S.get_entry(6219)["url"].endswith("/E/show?e=6219")


def test_no_url_without_an_address_or_an_id(monkeypatch):
    fake = FakeECL()
    fake._url = None
    monkeypatch.setattr(S, "_ecl_instance", fake)
    assert "url" not in S.get_entry(1)
    assert S._with_url({"text": "no id"}) == {"text": "no id"}
    assert S._with_url("not a dict") == "not a dict"
