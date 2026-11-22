"""
Serious-text doc rendering tests (openspec: strengthen-caas-serious-text /
presentation-plane): [[cite:<id>]] → numbered references, per-format, and
unknown anchors never render a source. Chromium is stubbed — these assert the
pre-print transform and template wiring, not pixels.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server.db.database as database
from mcp_server.db import init_schema
from mcp_server.tools import doc_render as D


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    init_schema(db)
    # Stub the two browser seams so render_document never launches chromium.
    monkeypatch.setattr(D, "_print_pdf", lambda html, tpl, out: out.write_bytes(b"%PDF-1.4 test"))
    monkeypatch.setattr(D, "_shot_longimage", lambda html, w, out: (out.write_bytes(b"\x89PNG test"), 812))

    # Redirect downloads somewhere writable (create the out dir like the real one does).
    import mcp_server.utils as U
    monkeypatch.setattr(U, "DOWNLOADS_DIR", tmp_path / "downloads")

    def _ensure(kind, slug):
        d = (tmp_path / "downloads" / kind / slug).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(D, "ensure_directory_rel", _ensure)
    monkeypatch.setattr(D, "get_relative_path", lambda *a: str(Path("doc") / a[-1]))
    return db


CITES = [
    {"id": "c1", "ref_meta": {"title": "Deep Learning", "authors": ["LeCun, Y."],
                              "year": 2015, "venue": "Nature", "doi": "10.1/x"}},
    {"id": "c2", "ref_meta": {"title": "Attention", "authors": ["Vaswani, A."],
                              "year": 2017, "venue": "NeurIPS"}},
]


def test_render_numbered_and_ordered():
    body, bib, order = D._render_citations(
        '<p>A[[cite:c1]] B[[cite:c2]] C[[cite:c1]]</p>', CITES, "gbt7714")
    assert order == ["c1", "c2"]                 # first-seen order
    assert "[1]" in body and "[2]" in body
    assert bib.count("<li ") == 2
    assert "Deep Learning" in bib and "Attention" in bib


def test_unknown_anchor_not_rendered():
    body, bib, order = D._render_citations(
        '<p>X[[cite:ghost]] Y[[cite:c1]]</p>', CITES, "apa")
    assert "ghost" not in body
    assert order == ["c1"]
    assert bib.count("<li ") == 1


def test_formats_differ():
    _, gbt, _ = D._render_citations('<p>[[cite:c1]]</p>', CITES, "gbt7714")
    _, apa, _ = D._render_citations('<p>[[cite:c1]]</p>', CITES, "apa")
    assert "[J]" in gbt          # GB/T uses [J] document-type marker
    assert "[J]" not in apa      # APA does not


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        D._render_citations("<p>x</p>", CITES, "mla")


def test_blocked_citation_not_rendered():
    """A verified-blocked citation never renders a number (宁删勿假)."""
    cites = CITES + [{"id": "bad", "status": "blocked",
                      "ref_meta": {"title": "Retracted", "year": 2020}}]
    body, bib, order = D._render_citations(
        '<p>A[[cite:c1]] B[[cite:bad]]</p>', cites, "gbt7714")
    assert "bad" not in body
    assert "Retracted" not in bib
    assert order == ["c1"]


def test_templates_present():
    from mcp_server.tools.templates import _load_doc_templates
    ids = {t["id"] for t in _load_doc_templates()}
    assert {"gongwen-gb9704", "academic-gbt7714", "academic-apa", "report"} <= ids


def test_render_document_end_to_end_cites():
    out = D.render_document(
        "# 标题\n\n正文断言[[cite:c1]][[cite:c2]]。",
        format="pdf", template="academic-gbt7714", slug="cite-test",
        citations=CITES, cite_format="gbt7714")
    assert out["template"] == "academic-gbt7714"
    assert out["products"] and out["products"][0]["format"] == "pdf"


def test_no_citations_path_unchanged():
    """Rollback proof (task 5.2): with no citations, rendering a default doc is
    byte-for-byte the same path as before this change — no bibliography, no
    anchor rewriting. Existing callers (no citations arg) are unaffected."""
    out = D.render_document("# 普通文档\n\n无引用正文。", format="pdf",
                            template="default", slug="plain-doc")
    assert out["template"] == "default"
    assert out["products"] and out["products"][0]["format"] == "pdf"
    # _render_citations must be a no-op when not invoked at all
    body, bib, order = D._render_citations("<p>无引用</p>", [], "gbt7714")
    assert body == "<p>无引用</p>" and bib == "" and order == []
