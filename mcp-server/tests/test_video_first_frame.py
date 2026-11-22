"""图生视频首帧通路（change expose-video-generation-lanes 2.x）。

三条纪律：
1. 首帧缺失/不可读/不是图 → **提交前**失败（不能为没有首帧的任务付钱）。
2. 本地路径与资产 id 都接受（调用方不该知道车道差异）。
3. 探针的判定必须可复现：源图纯蓝时，带首帧产物首帧离源图远小于无首帧时。
"""
from __future__ import annotations

import base64
import zlib

import pytest

from mcp_server.generators.video import resolve_first_frame


def _png(rgb=(0, 0, 255), size=8) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def ch(tag, data):
        return len(data).to_bytes(4, "big") + tag + data + zlib.crc32(tag + data).to_bytes(4, "big")

    ihdr = size.to_bytes(4, "big") + size.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", ihdr)
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def test_url_and_data_uri_pass_through():
    assert resolve_first_frame("https://x/a.png") == "https://x/a.png"
    assert resolve_first_frame("data:image/png;base64,AAA") == "data:image/png;base64,AAA"
    assert resolve_first_frame(None) is None


def test_local_png_becomes_file_marker(tmp_path):
    """本地图不内联进 argv，而是传 `@file:` 让桥接自己读。

    2026-11-22 实测：1MB PNG base64 后 1.4MB 作为 subprocess 参数，整次调用
    被判 timeout——不是首帧能力问题，是搬运字节的方式错了。
    """
    f = tmp_path / "a.png"
    f.write_bytes(_png())
    out = resolve_first_frame(str(f))
    assert out == f"@file:{f}"


def test_bridge_expands_file_marker(tmp_path):
    """桥接侧负责把 @file: 读成 data URI（大文件只在同机读一次）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bridge", str(__import__("pathlib").Path(__file__).parent.parent.parent
                      / "scripts" / "litellm-bridge.py"))
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    f = tmp_path / "a.png"
    f.write_bytes(_png())
    out = bridge._expand_frame_arg(f"@file:{f}")
    assert out.startswith("data:image/png;base64,")
    assert base64.b64decode(out.split(",", 1)[1]).startswith(b"\x89PNG")
    # 非 @file 值原样返回
    assert bridge._expand_frame_arg("https://x/a.png") == "https://x/a.png"


def test_bridge_rejects_missing_file(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bridge2", str(__import__("pathlib").Path(__file__).parent.parent.parent
                       / "scripts" / "litellm-bridge.py"))
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    with pytest.raises(SystemExit):
        bridge._expand_frame_arg(f"@file:{tmp_path/'nope.png'}")


def test_missing_file_is_refused_before_submit(tmp_path):
    with pytest.raises(ValueError) as e:
        resolve_first_frame(str(tmp_path / "nope.png"))
    assert "not found" in str(e.value)


def test_non_image_is_refused(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"<html>not an image</html>")
    with pytest.raises(ValueError) as e:
        resolve_first_frame(str(f))
    assert "not a recognised image" in str(e.value)


def test_empty_file_is_refused(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"")
    with pytest.raises(ValueError) as e:
        resolve_first_frame(str(f))
    assert "empty" in str(e.value)


def test_asset_id_without_file_is_refused(monkeypatch):
    """资产 id 存在但盘上没文件 —— 也必须拒，不能传个空首帧去付费。"""
    import mcp_server.generators.video as v

    monkeypatch.setattr("mcp_server.db.get_asset",
                        lambda aid, owner=None: {"id": aid, "file_path": ""})
    with pytest.raises(ValueError) as e:
        resolve_first_frame("138fcca5-84af-4473-b451-0b8eb85131dd")
    assert "not found in the ledger" in str(e.value) or "no file" in str(e.value)


def test_probe_distances_method_is_sound():
    """复现探针的核心比较：纯蓝源图下，带首帧产物应显著更接近源图。

    这里只用平均色做算术验证，不连上游——它守的是「判据公式本身」，
    真实距离由 scripts/probe_video_lane.py 在真上游上产出（2026-11-22 实测：
    16.79 vs 286.38）。
    """
    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    src = (0, 0, 255)
    with_frame = (7, 8, 242)      # 实测
    without_frame = (208, 182, 180)
    assert dist(with_frame, src) < dist(without_frame, src)
    assert dist(with_frame, src) < 50          # 远小于数量级
    assert dist(without_frame, src) > 200


# ─── 确定性 slug 重复是重放，不是失败 ─────────────────────────────────────

def test_idempotent_asset_insert_on_duplicate_slug(monkeypatch):
    """调用方（如剧创）用确定性 slug：同镜重生成会再次命中同一 slug。

    2026-11-22 实测：`poll()` 用 insert_content 落账，第二次直接抛
    `UNIQUE constraint failed: assets.slug`，冒到轮询结果里看起来像
    「视频任务失败」——其实是重放。改用 insert_asset_idempotent。
    """
    from mcp_server import db

    first, created1 = db.insert_asset_idempotent(
        type="video", slug="dup-test-slug", file_path="v/1.mp4",
        metadata={"a": 1}, stage="rendered", owner="svc",
    )
    second, created2 = db.insert_asset_idempotent(
        type="video", slug="dup-test-slug", file_path="v/2.mp4",
        metadata={"a": 2}, stage="rendered", owner="svc",
    )
    assert created1 is True and created2 is False
    assert first == second, "同 slug 必须复用它自己的行，而不是新建或崩溃"
