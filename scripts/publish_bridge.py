#!/usr/bin/env python3
"""发布桥：wire artifact → VAAS publish → distribution 行（带 CaaS 投递维度）。

close-marketing-funnel E 组。这条桥只解决一件事：让「哪篇稿、谁拍的板、
发到哪、什么回执」可回查。它不新增接口——`publish_article` 与
`publish_record` 都已存在，缺的从来是没人真传过 `recipient` / `idem_key`。

**拍板门是硬门**：不给 --confirmed-by 就拒绝执行。发布是撤不回的动作，
这条纪律在舰队里（发布草稿岗不持直发权）和 VAAS 里（发布前必须人工确认）
各写了一遍，桥这里不能成第三处漏口。

用法：
    # 只看会做什么，不发
    python3 publish_bridge.py --artifact-id <id> --slug <vaas-slug> --platform zhihu \
        --confirmed-by aloadtree@gmail.com --dry-run

    # 真发
    python3 publish_bridge.py --artifact-id <id> --slug vaas-tutorial-v8 \
        --platform zhihu --confirmed-by aloadtree@gmail.com --body "正文…"

    # 按 artifact 回查
    python3 publish_bridge.py --lookup <artifact-id>
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

VAAS_ROOT = Path(__file__).resolve().parents[1]
# 真库是 data/ 下那个（代码默认 DB_PATH）；根目录的 vaas.db 是更早的迁移残留，
# 2026-11-19 实测：根部的 15 assets/0 投递，data 的 20 assets/18 投递。
DB_PATH = VAAS_ROOT / "data" / "vaas.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def asset_for_slug(conn, slug: str):
    return conn.execute("SELECT * FROM assets WHERE slug = ?", (slug,)).fetchone()


def lookup(conn, artifact_id: str) -> list[sqlite3.Row]:
    """按 artifact id 回查投递行（idem_key 就是 artifact id）。"""
    return conn.execute(
        """SELECT d.id, d.platform, d.recipient, d.status, d.url,
                  d.ack_declared, d.ack_actual, d.downgraded, d.idem_key,
                  a.slug
             FROM distribution d JOIN assets a ON a.id = d.asset_id
            WHERE d.idem_key = ?
            ORDER BY d.id""",
        (artifact_id,),
    ).fetchall()


def publish(*, artifact_id, slug, platform, confirmed_by, body, recipient, ack_actual, dry_run) -> int:
    conn = connect()
    asset = asset_for_slug(conn, slug)
    if not asset:
        print(f"FAIL: slug 不存在 {slug}", file=sys.stderr)
        return 2
    if not artifact_id.strip():
        print("FAIL: --artifact-id 为空（它是幂等键，必须有）", file=sys.stderr)
        return 2

    existing = lookup(conn, artifact_id)
    if existing:
        # 幂等：同一 artifact 重复发布不产生第二行（5.5 判据）
        print(f"IDEMPOTENT: artifact {artifact_id} 已有 {len(existing)} 条投递行，不重复写")
        for r in existing:
            print(f"  {r['platform']} -> {r['recipient']} [{r['status']}]")
        return 0

    plan = {
        "asset": asset["slug"],
        "asset_stage": asset["stage"],
        "platform": platform,
        "recipient": recipient or "(platform broadcast)",
        "idem_key": artifact_id,
        "ack_declared": "delivered",
        "ack_actual": ack_actual,
        "confirmed_by": confirmed_by,
        "body_len": len(body or ""),
    }
    if dry_run:
        print("DRY-RUN（不会发布）:")
        for k, v in plan.items():
            print(f"  {k}: {v}")
        return 0

    # 真发：只加载 db 子包，绕开 `mcp_server/__init__.py`——
    # 那个 __init__ 会 import .main，进而拖起 fastmcp 与整个 MCP 服务面，
    # 发布桥只需要一个 INSERT。db/ 与 db.models 之间是相对 import，
    # 所以先把它们注册进 sys.modules 再 exec（包上下文必须存在）。
    import importlib.util
    import types

    pkg_root = VAAS_ROOT / "mcp-server" / "mcp_server"
    db_dir = pkg_root / "db"

    def load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=[str(path.parent)] if path.is_dir() else None
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    # 桩：一个不执行 __init__ 的包壳，只为让相对 import 有父包
    for pkg in ("_vaas_pkg", "_vaas_pkg.db"):
        m = types.ModuleType(pkg)
        m.__path__ = [str(db_dir)] if pkg.endswith(".db") else [str(pkg_root)]
        sys.modules[pkg] = m
    load_module("_vaas_pkg.db.database", db_dir / "database.py")
    dbm = load_module("_vaas_pkg.db.models", db_dir / "models.py")

    res = dbm.insert_distribution(
        asset["id"],
        platform,
        status="uploaded",
        recipient=recipient,
        ack_declared="delivered",
        ack_actual=ack_actual,
        idem_key=artifact_id,
    )
    print(f"✓ 已记投递行 id={res}（asset={asset['slug']} platform={platform} "
          f"recipient={recipient or 'broadcast'} idem_key={artifact_id}）")
    print(f"  拍板人：{confirmed_by}")
    print("  注意：本桥只负责留痕与幂等；真正的平台上传仍走 publish_article/发布面。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-id", help="wire artifact id（同时是幂等键）")
    ap.add_argument("--slug", help="VAAS asset slug")
    ap.add_argument("--platform", help="目标平台（如 zhihu / weixin）")
    ap.add_argument("--recipient", default=None, help="定向收件方（省略=平台广播）")
    ap.add_argument("--ack-actual", default=None, help="实际回执强度")
    ap.add_argument("--body", default="", help="稿件正文（长度会记录）")
    ap.add_argument("--confirmed-by", default=None, help="拍板人 email（必填，除非 --dry-run/--lookup）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lookup", metavar="ARTIFACT_ID", help="按 artifact 回查投递行")
    a = ap.parse_args()

    if a.lookup:
        rows = lookup(connect(), a.lookup)
        if not rows:
            print(f"无投递记录：{a.lookup}")
            return 1
        for r in rows:
            print(f"{r['slug']} | {r['platform']} | recipient={r['recipient'] or 'broadcast'} "
                  f"| status={r['status']} | ack={r['ack_declared']}/{r['ack_actual'] or '-'} "
                  f"| downgraded={r['downgraded']} | idem={r['idem_key']}")
        return 0

    if not a.artifact_id or not a.slug or not a.platform:
        print("用法错误：--artifact-id / --slug / --platform 必填", file=sys.stderr)
        return 2
    if not a.dry_run and not (a.confirmed_by or "").strip():
        print("拒绝：发布是撤不回的动作，必须给 --confirmed-by <拍板人>", file=sys.stderr)
        return 3

    return publish(
        artifact_id=a.artifact_id, slug=a.slug, platform=a.platform,
        confirmed_by=a.confirmed_by or "", body=a.body, recipient=a.recipient,
        ack_actual=a.ack_actual, dry_run=a.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
