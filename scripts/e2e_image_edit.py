#!/usr/bin/env python3
"""E2E acceptance: one generation -> one edit, end to end through the real lane.

Task 5.3 of `expose-image-edit-lane`. This is the acceptance script that must go
green before the lane can be announced; keeping it in the repo means the check is
a command, not a memory.

What it asserts (all four must hold):
  1. generation produced a readable image asset;
  2. the edit produced a *different* image (byte-level, not pixel-similar — the
     judgeable property, see design D3);
  3. the edited asset's parent_id points at the generated asset (lineage);
  4. both calls recorded a usage row — i.e. neither was a free/zero-priced call.

Exit codes: 0 ok / 1 assertion failed / 2 cannot-run (lane or creds unavailable).
Exit 2 is NOT a pass: it means the infrastructure could not be exercised.

Usage:
  # local venv, real lane keys in .env
  .venv/bin/python scripts/e2e_image_edit.py --slug e2e-edit-$(date +%s)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

OK, FAILED, CANNOT_RUN = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E2E: generate then edit, assert lineage + real charges")
    ap.add_argument("--slug", default=None, help="shared slug prefix (default: timestamped)")
    ap.add_argument("--provider", default=None, help="IMAGE_PROVIDER override (ciyuan/grok)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    slug = args.slug or f"e2e-edit-{int(__import__('time').time())}"
    if args.provider:
        os.environ["IMAGE_PROVIDER"] = args.provider

    from mcp_server.generators import image as gen
    from mcp_server.db import get_asset, get_connection

    report: dict = {"slug": slug, "steps": [], "verdict": None}

    def step(name: str, ok: bool, detail: dict | None = None) -> bool:
        report["steps"].append({"step": name, "ok": ok, **(detail or {})})
        return ok

    # ── 1. generate ────────────────────────────────────────────────────────
    try:
        gen_res = gen.generate(
            prompt="a plain red square on a white background, flat, minimal",
            slug=f"{slug}-gen",
        )
    except Exception as exc:  # noqa: BLE001
        step("generate", False, {"error": str(exc)[:300]})
        report["verdict"] = "cannot-run (generation lane unavailable)"
        _emit(report, args.json)
        return CANNOT_RUN

    gen_paths = gen_res.get("file_paths") or []
    if not step("generate", bool(gen_paths), {"file_paths": gen_paths}):
        report["verdict"] = "failed: generation produced no file"
        _emit(report, args.json)
        return FAILED

    gen_bytes = (Path(os.environ.get("VAAS_DOWNLOADS", "downloads")) / gen_paths[0]).read_bytes()
    src_asset = get_asset(gen_res["id"])
    step("generate-asset-exists", bool(src_asset), {"asset_id": gen_res["id"]})

    # ── 2. edit that image ─────────────────────────────────────────────────
    try:
        edit_res = gen.edit(
            source=gen_res["id"],
            instruction="change the square to solid blue, keep everything else",
            slug=f"{slug}-edit",
        )
    except Exception as exc:  # noqa: BLE001
        step("edit", False, {"error": str(exc)[:300]})
        report["verdict"] = "cannot-run (edit lane unavailable)"
        _emit(report, args.json)
        return CANNOT_RUN

    edit_paths = edit_res.get("file_paths") or []
    if not step("edit", bool(edit_paths), {"file_paths": edit_paths}):
        report["verdict"] = "failed: edit produced no file"
        _emit(report, args.json)
        return FAILED

    edit_bytes = (Path(os.environ.get("VAAS_DOWNLOADS", "downloads")) / edit_paths[0]).read_bytes()

    # ── 3. the two products must differ ────────────────────────────────────
    differs = edit_bytes != gen_bytes
    step("products-differ", differs,
         {"gen_size": len(gen_bytes), "edit_size": len(edit_bytes)})

    # ── 4. lineage ─────────────────────────────────────────────────────────
    edit_asset = get_asset(edit_res["id"])
    parent_id = (edit_asset or {}).get("parent_id")
    step("lineage-parent", parent_id == gen_res["id"],
         {"expected_parent": gen_res["id"], "actual_parent": parent_id})

    # ── 5. both calls charged ──────────────────────────────────────────────
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT tool, units, unit_type FROM generation_usage WHERE asset_id IN (?, ?)",
        (gen_res["id"], edit_res["id"]),
    )]
    conn.close()
    tools = sorted({r["tool"] for r in rows})
    step("usage-recorded", {"generate_image", "edit_image"} <= set(tools),
         {"tools_recorded": tools, "rows": rows})

    all_ok = all(s["ok"] for s in report["steps"])
    report["verdict"] = "ok" if all_ok else "failed"
    _emit(report, args.json)
    return OK if all_ok else FAILED


def _emit(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    for s in report["steps"]:
        mark = "ok  " if s["ok"] else "FAIL"
        extra = {k: v for k, v in s.items() if k not in ("step", "ok")}
        print(f"{mark} {s['step']}" + (f" — {extra}" if extra else ""))
    print(f"\nverdict: {report['verdict']}")


if __name__ == "__main__":
    raise SystemExit(main())
