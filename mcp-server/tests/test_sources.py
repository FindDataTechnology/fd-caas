"""
Source lane tests (openspec: strengthen-caas-serious-text / source-lane).

Offline: the remote MCP call is stubbed, so these assert dispatch, provenance
normalization, and the reason-on-empty contract without any network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server.db.database as database
from mcp_server.db import init_schema
from mcp_server import sources as S


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    init_schema(db)
    return db


def test_unknown_domain_lists_available():
    with pytest.raises(ValueError) as exc:
        S.source_search("does-not-exist", "q")
    assert "papers" in str(exc.value)  # available domains echoed


def test_list_domains_has_first_batch():
    assert {"papers", "gov", "news", "legal", "philosophy"} <= set(S.list_domains())


def test_search_normalizes_provenance(monkeypatch):
    """Federated hit without provenance gets a stable_id + as_of."""
    def fake(url, tool, args, timeout, bearer=None):
        return {"hits": [{"source": "openalex", "title": "T", "doi": "10.1/x"}]}

    monkeypatch.setattr(S, "_fetch", fake)
    out = S.source_search("papers", "graph")
    assert out["count"] == 1
    hit = out["hits"][0]
    assert hit["stable_id"] == "10.1/x"
    assert hit["as_of"]
    assert hit["source"] == "openalex"


def test_search_keeps_existing_provenance(monkeypatch):
    """Indexed hit already carrying a provenance dict keeps its triple."""
    def fake(url, tool, args, timeout, bearer=None):
        return {"results": [{"provenance": {"source": "law_db.laws",
                                            "stable_id": "law-7",
                                            "as_of": "2026-11-01"}}]}

    monkeypatch.setattr(S, "_fetch", fake)
    out = S.source_search("legal", "合同法")
    hit = out["hits"][0]
    assert hit["stable_id"] == "law-7"
    assert hit["as_of"] == "2026-11-01"


def test_lane_uses_declared_query_param(monkeypatch):
    """legal's law_search takes `title_query`, not `query`; the lane declares it."""
    captured = {}

    def fake(url, tool, args, timeout, bearer=None):
        captured.update(args)
        return {"results": []}

    monkeypatch.setattr(S, "_fetch", fake)
    S.source_search("legal", "合同")
    assert "title_query" in captured and captured["title_query"] == "合同"
    assert "query" not in captured


def test_text_core_lane_uses_query(monkeypatch):
    captured = {}

    def fake(url, tool, args, timeout, bearer=None):
        captured.update(args)
        return {"hits": []}

    monkeypatch.setattr(S, "_fetch", fake)
    S.source_search("papers", "gnn")
    assert captured.get("query") == "gnn"


def test_empty_result_carries_reason(monkeypatch):
    monkeypatch.setattr(S, "_fetch", lambda *a, **k: {"hits": []})
    out = S.source_search("papers", "nothing")
    assert out["hits"] == []
    assert out["reason"] == "not_indexed"


def test_upstream_failure_is_visible_not_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(S, "_fetch", boom)
    out = S.source_search("papers", "x")
    assert out["hits"] == []
    assert out["reason"] == "unreachable"
    assert "connection refused" in out["detail"]


def test_upstream_error_envelope_maps_to_reason(monkeypatch):
    monkeypatch.setattr(S, "_fetch", lambda *a, **k: {
        "ok": False, "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "PG down"}})
    out = S.source_search("gov", "x")
    assert out["reason"] == "upstream"


def test_tool_wrapper_records_usage(monkeypatch, isolated_db):
    """source_search tool writes one usage row, owner-scoped."""
    from mcp_server.tools.source import source_search as tool
    from mcp_server import hosting
    monkeypatch.setattr(S, "_fetch", lambda *a, **k: {"hits": []})
    monkeypatch.setattr(hosting, "current_owner", lambda: "tenant-a")
    import mcp_server.tools.source as src_mod
    monkeypatch.setattr(src_mod, "current_owner", lambda: "tenant-a")

    tool("papers", "q")
    conn = database.get_connection()
    rows = conn.execute("SELECT owner, tool, unit_type FROM generation_usage").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["owner"] == "tenant-a"
    assert rows[0]["tool"] == "source_search"
    assert rows[0]["unit_type"] == "source"
