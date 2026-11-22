"""Image-edit lane: the parts that must fail closed.

Three behaviours are load-bearing and each has cost a real incident family
elsewhere in this repo, so they get tests rather than trust:

1. Source-count ceiling — an over-limit request must be refused *before* the
   call, not truncated into a paid request that silently edits fewer images.
2. Echoed source — an upstream that returns the source unchanged must be a
   failure. (Same failure family as `image.ciyuan` billing $0.134 for HTML.)
3. Zero-priced cloud lane — a boot-time refusal, because the symptom (a usage
   rollup totalling 0 for real traffic) never announces itself.
"""
from __future__ import annotations

import importlib
import json

import pytest

from scripts import _ciyuan_image as drv

def _main_module():
    """mcp_server/__init__ re-exports the `main` FUNCTION, shadowing the module."""
    return importlib.import_module("mcp_server.main")


# ─── driver: source ceiling ────────────────────────────────────────────────

def test_edit_rejects_more_sources_than_upstream_allows():
    with pytest.raises(ValueError) as e:
        drv.edit(
            source="a.png", instruction="x", model="m",
            api_key="k", base_url="https://h", output_path="/tmp/o.png",
            references=["b.png", "c.png", "d.png"],  # 1 + 3 = 4 > 3
        )
    msg = str(e.value)
    assert "4 source images" in msg and "3" in msg


def test_edit_refusal_happens_before_any_request(monkeypatch):
    """The ceiling must fire before urllib is touched (no paid call escapes)."""
    called = []
    monkeypatch.setattr(drv.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError):
        drv.edit(
            source="a.png", instruction="x", model="m",
            api_key="k", base_url="https://h", output_path="/tmp/o.png",
            references=["b.png", "c.png", "d.png"],
        )
    assert called == []


# ─── driver: echoed source is a failure ────────────────────────────────────

class _FakeResp:
    def __init__(self, body: bytes, ctype="application/json"):
        self._body = body
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_edit_rejects_echoed_source(tmp_path, monkeypatch):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"original" * 8)

    import base64
    same = base64.b64encode(src.read_bytes()).decode()
    monkeypatch.setattr(
        drv.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(json.dumps({"data": [{"b64_json": same}]}).encode()),
    )

    with pytest.raises(RuntimeError) as e:
        drv.edit(
            source=str(src), instruction="make it blue", model="m",
            api_key="k", base_url="https://h",
            output_path=str(tmp_path / "out.png"),
        )
    assert "echoed_source" in str(e.value)


def test_edit_accepts_a_different_image(tmp_path, monkeypatch):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"original" * 8)
    edited = b"\x89PNG\r\n\x1a\n" + b"edited!!" * 8

    import base64
    monkeypatch.setattr(
        drv.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(
            json.dumps({"data": [{"b64_json": base64.b64encode(edited).decode()}]}).encode()
        ),
    )
    out = tmp_path / "out.png"
    result = drv.edit(
        source=str(src), instruction="make it blue", model="m",
        api_key="k", base_url="https://h", output_path=str(out),
    )
    assert result["echo_check"] == "different"
    assert out.read_bytes() == edited


def test_edit_refuses_unreadable_local_source(tmp_path):
    with pytest.raises(RuntimeError) as e:
        drv.edit(
            source=str(tmp_path / "nope.png"), instruction="x", model="m",
            api_key="k", base_url="https://h", output_path=str(tmp_path / "o.png"),
        )
    assert "not found" in str(e.value)


# ─── pricing: zero-priced cloud lanes refuse boot ──────────────────────────

def test_zero_priced_cloud_lane_is_refused(tmp_path, monkeypatch, caplog):
    m = _main_module()

    table = {
        "currency": "CNY",
        "generate_image": {"image": 0.0},
        "render_video": {"second": 0.0, "render": 0.0},
    }
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv("VAAS_PRICING_FILE", str(p))

    with pytest.raises(RuntimeError) as e:
        m._load_pricing(strict=True)
    assert "generate_image" in str(e.value)


def test_local_compute_and_deferred_zeros_are_exempt():
    m = _main_module()

    ok = {
        "render_video": {"second": 0.0, "render": 0.0},
        "generate_video_submit": {"second": 0.0},
        "generate_image": {"image": 0.144},
    }
    assert m._zero_priced_lanes(ok) == []


def test_priced_repriced_lane_passes():
    m = _main_module()

    assert m._zero_priced_lanes({"edit_image": {"image": 0.144}}) == []
    assert m._zero_priced_lanes({"edit_image": {"image": 0.0}}) == ["edit_image"]


# ─── n>1: n separate calls, disclosed, never faked ─────────────────────────

def test_edit_n_calls_upstream_n_times(tmp_path, monkeypatch):
    """n>1 must be n real calls with distinct output files, and say so."""
    import base64
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"original" * 8)
    sent = []

    def fake_urlopen(req, *a, **k):
        sent.append(req.full_url)
        n = len(sent)
        body = b"\x89PNG\r\n\x1a\n" + f"edit{n:02d}".encode() * 8
        return _FakeResp(json.dumps(
            {"data": [{"b64_json": base64.b64encode(body).decode()}]}).encode())

    monkeypatch.setattr(drv.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "o.png"
    result = drv.edit(
        source=str(src), instruction="x", model="m", api_key="k",
        base_url="https://h", output_path=str(out), n=3,
    )
    assert len(sent) == 3, sent
    assert len(result["images"]) == 3
    assert all("separate calls" in w for w in result["warnings"])


# ─── lineage: an edit points at what it edited ─────────────────────────────

def test_edit_records_parent_asset(tmp_path, monkeypatch):
    """The edited asset must carry parent_id = source asset (design D2).

    Without it, "which round of edits produced this image" is unanswerable
    from the ledger — the same blind spot that made the image-lane billing
    disputes hard to settle.
    """
    from mcp_server.generators import image as gen_image
    import base64

    out_dir = tmp_path / "images" / "slug"
    out_dir.mkdir(parents=True)
    src_file = out_dir / "src.png"
    src_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"orig" * 8)
    edited = b"\x89PNG\r\n\x1a\n" + b"edited" * 8
    bridge_out = json.dumps({
        "images": [{"local_path": "/nonexistent/raw.png", "format": "png"}],
        "model": "m", "echo_check": "different",
    })
    # the driver writes the file; emulate that at the rename target
    (out_dir / "slug_00.png").write_bytes(edited)

    captured = {}

    monkeypatch.setattr(gen_image, "prepare", lambda *a, **k: {"owner": "svc", "resolved": {"source": "env"}, "env": {}})
    monkeypatch.setattr(gen_image, "run_bridge", lambda *a, **k: bridge_out)
    monkeypatch.setattr(gen_image, "ensure_directory_rel", lambda *a, **k: out_dir)
    monkeypatch.setattr(gen_image, "get_relative_path", lambda *a, **k: "/".join(map(str, a)))
    monkeypatch.setattr(gen_image, "generate_slug", lambda p: "slug")
    monkeypatch.setattr(gen_image, "get_asset", lambda aid, owner=None: {"id": "PARENT1"})
    monkeypatch.setattr(gen_image, "finish", lambda *a, **k: {})
    monkeypatch.setattr(gen_image, "insert_asset", lambda **kw: captured.update(kw) or kw.get("id"))
    # get_generator is imported *inside* edit() (registry imports this module,
    # so a module-level import would be circular) — patch it at its source.
    import mcp_server.registry as reg
    monkeypatch.setattr(reg, "get_generator", lambda t, n: {"provider": "grok", "model": "m"})

    gen_image.edit(source="PARENT1", instruction="make it blue")
    assert captured.get("parent_id") == "PARENT1"
    assert captured.get("metadata", {}).get("source_asset_id") == "PARENT1"
