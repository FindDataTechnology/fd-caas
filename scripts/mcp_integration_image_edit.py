#!/usr/bin/env python3
"""MCP 协议级集成测试（task 5.3 的协议层）。

与 `scripts/e2e_image_edit.py` 的分工：
  - e2e_image_edit.py：直接调 Python 函数，验证「驱动 + 账本 + 上游」。
  - 本脚本：经 **FastMCP 协议**（tools/list → tools/call）调，验证
    「工具注册 + 参数序列化 + 错误分类 + 响应形状」——这是 e2e 覆盖不到的层。
    真实事故里就有「函数能调、工具面没注册」和「注册了但参数名对不上」两类。

两个模式：
  --surface          只查工具面（离线，零成本）：edit_image 是否在列、schema 是否正确、
                     编辑车道健康是否透出。这是每次改动后都能跑的哨兵。
  --call             再真调一次编辑（要真 key + 上游可达，会产生计费）。

用法：
  .venv/bin/python scripts/mcp_integration_image_edit.py --surface
  CIYUAN_API_KEY=... GROK_BASE_URL=... .venv/bin/python scripts/... --call

退出码：0 通过 / 1 断言失败 / 2 无法执行（缺凭据或上游不可达）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp-server"))

OK, FAILED, CANNOT_RUN = 0, 1, 2


async def _surface() -> dict:
    import importlib
    from fastmcp import Client

    M = importlib.import_module("mcp_server.main")
    async with Client(M.mcp) as client:
        tools = await client.list_tools()
        by_name = {t.name: t for t in tools}
        edit = by_name.get("edit_image")
        schema = (edit.inputSchema if edit else {}) or {}
        props = set((schema.get("properties") or {}).keys())

        # Registry discovery: the edit entry must carry its OWN lane, not borrow
        # the generation lane's verdict (spec generation-registry「生成可用而
        # 编辑不可用」是合法状态).
        listing = await client.call_tool("registry_list_generators", {"type": "image"})
        items = _payload(listing).get("items", [])
        edit_entries = [e for e in items if e.get("driver") == "edit_image"]
        gen_entries = [e for e in items if e.get("driver") == "generate_image"]

        return {
            "edit_tool_present": edit is not None,
            "edit_params": sorted(props),
            "edit_required": sorted(schema.get("required") or []),
            "edit_entries": [e.get("name") for e in edit_entries],
            "edit_lanes": {e["name"]: (e.get("lane") or {}).get("health") for e in edit_entries},
            "gen_lanes": {e["name"]: (e.get("lane") or {}).get("health") for e in gen_entries},
        }


def _payload(result) -> dict:
    """FastMCP returns content blocks; the tool's dict is in structured/text."""
    for attr in ("structured_content", "data"):
        v = getattr(result, attr, None)
        if isinstance(v, dict):
            return v
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


async def _call(source: str) -> dict:
    import importlib
    from fastmcp import Client

    M = importlib.import_module("mcp_server.main")
    async with Client(M.mcp) as client:
        res = await client.call_tool("edit_image", {
            "source": source,
            "instruction": "change the square to solid blue, keep everything else",
            "slug": f"mcp-it-{int(__import__('time').time())}",
        })
        return _payload(res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MCP protocol-level integration test")
    ap.add_argument("--surface", action="store_true", help="tool surface only (offline)")
    ap.add_argument("--call", action="store_true", help="also perform one real edit (billed)")
    ap.add_argument("--source", default=None, help="source asset id / path for --call")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report: dict = {"checks": [], "verdict": None}

    def check(name: str, ok: bool, detail=None) -> bool:
        report["checks"].append({"check": name, "ok": ok, "detail": detail})
        return ok

    # ── surface ────────────────────────────────────────────────────────────
    surf = asyncio.run(_surface())
    ok = check("edit_image registered on tool surface", surf["edit_tool_present"])
    if ok:
        check("edit_image exposes source+instruction",
              {"source", "instruction"} <= set(surf["edit_params"]),
              surf["edit_params"])
        check("both params are required",
              {"source", "instruction"} <= set(surf["edit_required"]),
              surf["edit_required"])
        check("edit entries registered", bool(surf["edit_entries"]), surf["edit_entries"])
        # The whole point of a separate ledger kind: lanes must be independent.
        check("edit lane resolves independently of generation lane",
              set(surf["edit_lanes"]) != set(surf["gen_lanes"]),
              {"edit": surf["edit_lanes"], "gen": surf["gen_lanes"]})

    if not args.call:
        report["verdict"] = "surface-ok" if all(c["ok"] for c in report["checks"]) else "surface-failed"
        _emit(report, args.json)
        return OK if report["verdict"] == "surface-ok" else FAILED

    # ── real call over the protocol ────────────────────────────────────────
    source = args.source
    if not source:
        report["verdict"] = "cannot-run (--call needs --source)"
        _emit(report, args.json)
        return CANNOT_RUN

    try:
        out = asyncio.run(_call(source))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        lane_down = "lane_down" in msg or "unavailable" in msg.lower()
        check("edit_image call", False, {"error": msg[:300]})
        report["verdict"] = "cannot-run (lane unavailable)" if lane_down else "call-failed"
        _emit(report, args.json)
        return CANNOT_RUN if lane_down else FAILED

    check("call returns id/slug", bool(out.get("id") and out.get("slug")),
          {"id": out.get("id"), "slug": out.get("slug")})
    check("call returns a file path", bool(out.get("file_paths")), out.get("file_paths"))
    check("call carries lineage", "source_asset_id" in out,
          {"source_asset_id": out.get("source_asset_id")})

    report["verdict"] = "ok" if all(c["ok"] for c in report["checks"]) else "failed"
    _emit(report, args.json)
    return OK if report["verdict"] == "ok" else FAILED


def _emit(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    for c in report["checks"]:
        mark = "ok  " if c["ok"] else "FAIL"
        print(f"{mark} {c['check']}" + (f" — {c['detail']}" if c["detail"] else ""))
    print(f"\nverdict: {report['verdict']}")


if __name__ == "__main__":
    raise SystemExit(main())