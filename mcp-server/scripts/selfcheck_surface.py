#!/usr/bin/env python3
"""tool-surface manifest 双向 selfcheck（mcp-surface-contract）。

vaas-wanxing 的工具面随 hosted 形态变化：hosted 模式由 HOSTED_EXCLUDED 排除 publish_* 六件。
本脚本按 hosted 形态（is_hosted()=True）比对：manifest 的 commercial 集 SHALL 等于运行时工具面，
internal 集（publish_*）SHALL 在该形态下不出现。

用法（在 fd-caas/mcp-server 下）：python3 scripts/selfcheck_surface.py
退出码：0 通过 / 1 失败。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]   # = mcp-server/
MANIFEST = REPO / "tool-surface.json"


def fail(problems):
    print("SELFCHECK FAIL:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)


def main() -> None:
    if not MANIFEST.exists():
        fail([f"manifest 缺失：{MANIFEST}（契约：缺失即未上牌）"])
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    levels: dict[str, str] = {}
    for g in m.get("tool_groups", []):
        level = g.get("level")
        if level not in ("commercial", "internal"):
            fail([f"tool_groups.{g.get('group')} 的 level 非法：{level!r}"])
        for t in g.get("tools", []):
            if t in levels and levels[t] != level:
                fail([f"工具 {t} 在多个 group 中级别冲突"])
            levels[t] = level

    commercial = {t for t, lv in levels.items() if lv == "commercial"}
    internal = {t for t, lv in levels.items() if lv == "internal"}

    # hosted 形态：设 VAAS_HOSTED=1 让 is_hosted() 为真，publish_* 被排除。
    os.environ["VAAS_HOSTED"] = "1"
    sys.path.insert(0, str(REPO))
    from mcp_server.main import mcp

    runtime_set = {t.name for t in asyncio.run(mcp.list_tools())}
    problems = []
    missing = commercial - runtime_set
    extra = runtime_set - commercial
    if missing:
        problems.append(f"hosted 运行时缺 commercial 声明的工具：{sorted(missing)}")
    if extra:
        problems.append(f"hosted 运行时多出未声明的工具：{sorted(extra)}")
    leaked = internal & runtime_set
    if leaked:
        problems.append(f"hosted 形态下 internal 工具不应出现：{sorted(leaked)}")
    if problems:
        fail(problems)
    print(f"SELFCHECK OK（hosted）：{len(runtime_set)} 工具全部 commercial；publish_* 正确排除")


if __name__ == "__main__":
    main()
