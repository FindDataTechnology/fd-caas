"""
Citation store tests (openspec: strengthen-caas-serious-text /
serious-text-citation): a citation is an asset-bound material-store object
with the unified provenance triple, owner isolation, and a verification status.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server.db.database as database
from mcp_server.db import (
    init_schema, insert_asset, insert_citation, get_citation, list_citations,
    update_citation_status, CITATION_STATUSES,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    init_schema(db)
    return db


def _asset(owner="service"):
    return insert_asset(type="article", slug=f"a-{owner}", owner=owner)


def test_insert_and_get_roundtrip():
    asset = _asset()
    cid = insert_citation(asset, source="openalex", stable_id="W123",
                          as_of="2026-11-01", ref_meta={"title": "T"})
    got = get_citation(cid)
    assert got["stable_id"] == "W123"
    assert got["status"] == "resolvable"
    assert got["ref_meta"]["title"] == "T"


def test_owner_isolation():
    asset = _asset(owner="tenant-a")
    cid = insert_citation(asset, "openalex", "W1", "2026-11-01", owner="tenant-a")
    assert get_citation(cid, owner="tenant-a") is not None
    assert get_citation(cid, owner="tenant-b") is None  # cross-tenant invisible


def test_list_by_asset():
    asset = _asset()
    insert_citation(asset, "openalex", "W1", "2026-11-01")
    insert_citation(asset, "openalex", "W2", "2026-11-01")
    assert {c["stable_id"] for c in list_citations(asset)} == {"W1", "W2"}


def test_status_update_and_validation():
    asset = _asset()
    cid = insert_citation(asset, "openalex", "W1", "2026-11-01")
    update_citation_status(cid, "pending")
    assert get_citation(cid)["status"] == "pending"
    with pytest.raises(ValueError):
        update_citation_status(cid, "bogus")
    assert "supported" in CITATION_STATUSES


def test_insert_rejects_unknown_status():
    asset = _asset()
    with pytest.raises(ValueError):
        insert_citation(asset, "openalex", "W1", "2026-11-01", status="nope")


# ─── Draft gate (anchor truncation + three-way disposition) ────────────────

from mcp_server.citations import resolve_anchors, adjudicate, gate_draft


def test_resolve_strips_unknown_anchor():
    out = resolve_anchors("Claim A[[cite:known]] and B[[cite:ghost]].",
                          {"known"})
    assert out["stripped"] == ["ghost"]
    assert "ghost" not in out["cleaned_text"]
    assert "[[cite:known]]" in out["cleaned_text"]


def test_adjudicate_three_way():
    d = adjudicate([
        {"id": "a", "status": "supported"},
        {"id": "b", "status": "pending"},
        {"id": "c", "status": "blocked"},
    ])
    assert d["passed"] == ["a"]
    assert d["pending"] == ["b"]
    assert d["blocked"] == ["c"]
    assert d["all_resolved"] is False


def test_gate_strips_blocked_and_unknown_keeps_pending():
    cites = [
        {"id": "ok", "status": "supported"},
        {"id": "ask", "status": "pending"},
        {"id": "bad", "status": "blocked"},
    ]
    out = gate_draft(
        "X[[cite:ok]] Y[[cite:ask]] Z[[cite:bad]] W[[cite:ghost]].", cites)
    # ok and pending survive; blocked and ghost are stripped
    assert "[[cite:ok]]" in out["cleaned_text"]
    assert "[[cite:ask]]" in out["cleaned_text"]
    assert "bad" not in out["cleaned_text"]
    assert "ghost" not in out["cleaned_text"]
    assert out["pending"] == ["ask"]
    assert out["passed"] == ["ok"]
    assert out["blocked"] == ["bad"]


def test_gate_source_text_unchanged():
    text = "A[[cite:ghost]]."
    gate_draft(text, [])
    assert text == "A[[cite:ghost]]."  # caller's source is never mutated



# ─── verify client + citation_verify (task 2.3, offline) ────────────────────

import io
import json as _json


class _Resp:
    def __init__(self, body):
        self._b = body
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_verify_refs_not_configured(monkeypatch):
    import mcp_server.verify_client as V
    monkeypatch.delenv("VAAS_VERIFY_URL", raising=False)
    monkeypatch.delenv("VAAS_VERIFY_TOKEN", raising=False)
    r = V.verify_refs("x", [{"id": "c1"}])
    assert r["ok"] is False and r["reason"] == "not_configured"


def test_verify_refs_maps_statuses(monkeypatch):
    import mcp_server.verify_client as V
    monkeypatch.setenv("VAAS_VERIFY_URL", "http://verify.local/api/v1/verify")
    monkeypatch.setenv("VAAS_VERIFY_TOKEN", "tok")
    body = {"blocked_citation_ids": ["ghost"], "pending_citation_ids": ["c2"],
            "counts": {"total": 2}, "mode": "shadow",
            "cleaned_text": "x", "report": {}, "annotations": []}

    def fake_urlopen(req, timeout=None):
        return _Resp(_json.dumps(body).encode())
    monkeypatch.setattr(V.urllib.request, "urlopen", fake_urlopen)
    r = V.verify_refs("text", [{"id": "c1"}, {"id": "c2"}, {"id": "ghost"}])
    assert r["ok"] is True
    assert r["statuses"] == {"c1": "supported", "c2": "pending", "ghost": "blocked"}


def test_verify_refs_unreachable_is_visible(monkeypatch):
    import mcp_server.verify_client as V
    monkeypatch.setenv("VAAS_VERIFY_URL", "http://verify.local/api/v1/verify")
    monkeypatch.setenv("VAAS_VERIFY_TOKEN", "tok")

    def boom(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(V.urllib.request, "urlopen", boom)
    r = V.verify_refs("text", [{"id": "c1"}])
    assert r["ok"] is False and r["reason"] == "unreachable"


def test_citation_verify_persists_and_is_owner_scoped(monkeypatch):
    """citation_verify writes statuses back only when the engine answers ok."""
    from mcp_server.tools import citations as T
    asset = _asset()
    cid = insert_citation(asset, "openalex", "W1", "2026-11-01")
    monkeypatch.setattr(T, "current_owner", lambda: "service")
    monkeypatch.setattr(T, "_verify", lambda text, cites, mode=None: {
        "ok": True, "statuses": {cid: "blocked"}, "blocked": [cid], "pending": [],
        "reason": None, "counts": {}, "mode": "shadow"})
    out = T.citation_verify("x", asset)
    assert out["ok"] and out["updated"] == 1
    assert get_citation(cid)["status"] == "blocked"


def test_citation_verify_failure_does_not_overwrite(monkeypatch):
    from mcp_server.tools import citations as T
    asset = _asset()
    cid = insert_citation(asset, "openalex", "W1", "2026-11-01")
    monkeypatch.setattr(T, "current_owner", lambda: "service")
    monkeypatch.setattr(T, "_verify", lambda text, cites, mode=None: {
        "ok": False, "statuses": {}, "reason": "unreachable", "blocked": [], "pending": []})
    out = T.citation_verify("x", asset)
    assert out["ok"] is False and out["updated"] == 0
    assert get_citation(cid)["status"] == "resolvable"  # unchanged
