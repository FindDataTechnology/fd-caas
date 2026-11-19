#!/usr/bin/env python3
"""content → assets 换表迁移（VAAS 运营库）。

问题：代码（mcp_server/db/database.py）的 distribution 引用 asset_id→assets，
运营库却是 content_id→content。父表都不存在，不是加列能解决的。

做法（严格加性、可回滚）：
  1. 用代码自己的 init_schema 建出 assets / variants / distribution 新形状
     —— 但那会在旧 distribution 上炸（no such column: asset_id），
     所以先给旧 distribution 改名留档，再建新表。
  2. 把 content 的 15 行搬进 assets（逐列映射，status→stage）。
  3. 建 distribution 新表（带 recipient/ack/idem_key）。
  4. content 表**不删**，改名 content_legacy_<ts> 留档；迁移成功与否都能回退。

用法：
    python3 migrate_content_to_assets.py <db-path> [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'draft',
    title TEXT,
    parent_id TEXT REFERENCES assets(id),
    lineage_root TEXT,
    provider TEXT,
    file_path TEXT,
    metadata JSON,
    video_id INTEGER REFERENCES videos(id),
    owner TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

DISTRIBUTION_DDL = """
CREATE TABLE IF NOT EXISTS distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    video_id INTEGER,
    platform TEXT NOT NULL,
    url TEXT,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    published_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    recipient TEXT,
    ack_declared TEXT,
    ack_actual TEXT,
    downgraded INTEGER DEFAULT 0,
    idem_key TEXT
)
"""


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def run(db_path: Path, dry_run: bool) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    dist_cols = columns(conn, "distribution") if "distribution" in tables else []

    if "asset_id" in dist_cols:
        print("ALREADY-MIGRATED: distribution 已有 asset_id，无需换表")
        return 0
    if "content_id" not in dist_cols:
        print(f"UNEXPECTED: distribution 没有 content_id（列={dist_cols}）", file=sys.stderr)
        return 2

    content_rows = conn.execute("SELECT * FROM content").fetchall()
    print(f"计划：content {len(content_rows)} 行 → assets；旧表改名留档")

    if dry_run:
        for r in content_rows[:3]:
            print(f"  sample: {r['slug']} ({r['type']}/{r['status']}) → {r['file_path']}")
        print("  …")
        for r in content_rows:
            required = ("id", "slug", "type", "stage")
            vals = {
                "id": r["id"], "slug": r["slug"], "type": r["type"],
                "stage": r["status"], "title": None,
                "parent_id": None, "lineage_root": None, "provider": None,
                "file_path": r["file_path"], "metadata": r["metadata"],
                "video_id": None, "owner": None,
                "created_at": r["created_at"], "updated_at": r["updated_at"],
            }
            missing = [k for k in required if not vals.get(k)]
            if missing:
                print(f"  ROW-INVALID {r['id']}: 缺 {missing}", file=sys.stderr)
                return 1
        print("DRY-RUN OK：15 行全部能映射（id/slug/type/stage 非空）")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        conn.execute("BEGIN")
        # 1) 旧 distribution 改名留档（它引的是 content，不能影响新 assets）
        conn.execute(f'ALTER TABLE distribution RENAME TO "distribution_legacy_{ts}"')
        # 2) 建新 assets / distribution
        conn.execute(ASSETS_DDL)
        conn.execute(DISTRIBUTION_DDL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_distribution_asset ON distribution(asset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_distribution_platform ON distribution(platform)")
        # 3) content → assets
        n = 0
        for r in content_rows:
            conn.execute(
                """INSERT INTO assets
                   (id, slug, type, stage, title, parent_id, lineage_root, provider,
                    file_path, metadata, video_id, owner, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["id"], r["slug"], r["type"], r["status"], None, None, None, None,
                 r["file_path"], r["metadata"], None, None, r["created_at"], r["updated_at"]),
            )
            n += 1
        # 4) 旧 content 改名留档（不删）
        conn.execute(f'ALTER TABLE content RENAME TO "content_legacy_{ts}"')
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"FAILED（已回滚）: {e}", file=sys.stderr)
        return 1

    # 验收
    got = conn.execute("SELECT count(*) FROM assets").fetchone()[0]
    print(f"✓ 迁移完成：assets {got} 行；旧表改名 content_legacy_{ts} / distribution_legacy_{ts}")
    print(f'  回退：ALTER TABLE "content_legacy_{ts}" RENAME TO content; '
          f'ALTER TABLE "distribution_legacy_{ts}" RENAME TO distribution; DROP TABLE assets;')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return run(Path(a.db), a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
