"""
Video generator wrapper.

Wraps litellm-bridge.py video command via subprocess. Two flows:
- generate(): synchronous submit+poll (local mode default; double-billing-safe
  because the caller already waited)
- submit()/poll(): async two-step for hosted mode — submit returns a job id in
  seconds, status polling completes the job; a client timeout then retries the
  *status* call, never the paid submission.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from ..db import (
    insert_content, insert_job, update_job, get_job, expire_stale_jobs,
    generate_id, scoped_asset_slug,
)
from ..utils import generate_slug, ensure_directory_rel, get_relative_path, VAAS_ROOT
from .runtime import finish, prepare, run_bridge
from .errors import classify_upstream_error, is_transient

# Path to litellm-bridge.py
SCRIPTS_DIR = VAAS_ROOT / "scripts"
LITELLM_BRIDGE = SCRIPTS_DIR / "litellm-bridge.py"


def _probe_video_seconds(path: Path) -> Optional[float]:
    """Measured duration via ffprobe; None when the tool can't run/parse.

    Honest billing rule (fix-hosted-generation-correctness): callers bill
    ONLY measured values and disclose a missing measurement instead of
    falling back to upstream claims or hardcoded constants.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        doc = json.loads(out.stdout or "{}")
        raw = (doc.get("format") or {}).get("duration")
        if raw is None:
            return None
        val = float(raw)
        return val if val > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _slug_for(prompt: Optional[str], slug: Optional[str], fallback: str) -> str:
    if slug:
        return slug
    prefix = (prompt or "")[:20].lower().replace(" ", "-")
    prefix = "".join(c for c in prefix if c.isalnum() or c == "-")
    return generate_slug(prefix or fallback)



# ── 首帧形态解析（change expose-video-generation-lanes 2.1/2.3）───────────────
#
# 上游要的是能内联/能取的图。调用方手里的东西有三种：URL、本地路径、VAAS 资产 id。
# 这里统一成上游能吃的形态，并且**在提交前校验**——绝不提交一个没有首帧的付费任务。
#
# 形态选择：本地图转 data URI。2026-11-22 探针实测该形态可用（首帧产物首帧 RGB
# (7,8,242) vs 源图 (0,0,255)，距 16.79；无首帧时距 286.38）。

_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def _looks_like_asset_id(value: str) -> bool:
    s = (value or "").strip()
    return len(s) == 36 and s.count("-") == 4 and "/" not in s and "." not in s


def resolve_first_frame(value: Optional[str], owner: Optional[str] = None) -> Optional[str]:
    """URL / 本地路径 / 资产 id → 上游可接受的字符串。

    - URL 与 data URI 原样返回；
    - 资产 id 先查账本取 file_path；
    - 本地路径读字节、按魔数校验、转 data URI。

    读不到 / 认不出 / 资产无文件 **一律抛 ValueError**，且发生在提交之前——
    这条纪律是「别为一个没有首帧的任务付钱」（spec 里写成 MUST NOT）。
    """
    if not value:
        return None
    v = value.strip()
    if v.startswith(("http://", "https://", "data:")):
        return v

    path: Optional[Path] = None
    if _looks_like_asset_id(v):
        from ..db import get_asset
        asset = get_asset(v, owner=owner)
        rel = (asset or {}).get("file_path") or ""
        if not rel:
            raise ValueError(
                f"invalid_input: first_frame asset {v} not found in the ledger "
                f"(or has no file) — cannot use it as a first frame"
            )
        path = Path(VAAS_ROOT) / "downloads" / rel
    else:
        cand = Path(v).expanduser()
        path = cand if cand.is_absolute() else (Path(VAAS_ROOT) / "downloads" / cand)
        if not path.is_file():
            cand2 = Path(v).expanduser()
            path = cand2 if cand2.is_file() else path

    if not path or not path.is_file():
        raise ValueError(f"invalid_input: first_frame file not found: {value}")

    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"invalid_input: first_frame file is empty: {path}")
    fmt = next((f for sig, f in _IMAGE_MAGIC if raw.startswith(sig)), None)
    if fmt is None:
        raise ValueError(
            "invalid_input: first_frame is not a recognised image "
            f"(first bytes: {raw[:12].hex()}) — refusing to submit"
        )
    # **不**在这里内联成 data URI：1MB 的图 base64 后 1.4MB，作为 argv 传给
    # subprocess 既撞参数上限、又让桥接启动极慢（2026-11-22 实测被判 timeout）。
    # 传路径，让桥接自己读文件——同机同 volume，不需要跨进程搬运字节。
    return f"@file:{path}"


def _build_cmd(prompt: Optional[str], output_path: Path, duration, ratio,
               resolution, first_frame, last_frame, wait, timeout) -> list[str]:
    cmd = ["python", str(LITELLM_BRIDGE), "video"]
    if prompt:
        cmd.extend(["--prompt", prompt])
    cmd.extend(["--output", str(output_path)])
    if duration is not None:
        cmd.extend(["--duration", str(duration)])
    if ratio:
        cmd.extend(["--ratio", ratio])
    if resolution:
        cmd.extend(["--resolution", resolution])
    if first_frame:
        cmd.extend(["--first-frame", first_frame])
    if last_frame:
        cmd.extend(["--last-frame", last_frame])
    if not wait:
        cmd.append("--async")
    if timeout:
        cmd.extend(["--timeout", str(timeout)])
    return cmd


def generate(
    prompt: str,
    duration: Optional[int] = None,
    ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    wait: bool = True,
    timeout: int = 600,
    slug: Optional[str] = None
) -> dict:
    """
    Generate video from text prompt (synchronous).

    Returns dict with id, slug, file_path, status (rendered|pending), model.
    """
    if not prompt:
        raise ValueError("prompt is required")

    ctx = prepare("video", {
        "prompt": prompt, "first_frame": first_frame, "last_frame": last_frame,
        "slug": slug,
    })
    owner = ctx["owner"]
    # 首帧形态解析必须在提交前 —— 提交之后失败等于白付一次任务费。
    # 抛 ValueError 即拒绝；这里不吞异常。
    first_frame = resolve_first_frame(first_frame, owner=owner)
    last_frame = resolve_first_frame(last_frame, owner=owner)
    slug = _slug_for(prompt, slug, "video")

    output_dir = ensure_directory_rel("videos", slug)
    output_path = output_dir / f"{slug}.mp4"
    cmd = _build_cmd(prompt, output_path, duration, ratio, resolution,
                     first_frame, last_frame, wait, timeout)

    try:
        stdout = run_bridge(cmd, ctx)
    except subprocess.CalledProcessError as e:
        raise classify_upstream_error("generate_video", e.stderr) from e

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"upstream_error: video lane returned unparsable output: {e}") from e

    status = "rendered" if wait and output_path.exists() else "pending"
    content_id = generate_id()
    # Bill measured seconds from the landed file; the requested value is
    # kept as metadata only (never silently trusted as the billing number).
    measured = _probe_video_seconds(output_path) if status == "rendered" else None
    metadata = {
        "prompt": prompt,
        "duration": duration,
        "ratio": ratio,
        "resolution": resolution,
        "model": output.get("model"),
    }
    if measured is not None:
        metadata["duration_sec"] = measured
        metadata["duration_source"] = "measured"
    insert_content(
        id=content_id,
        type="video",
        slug=scoped_asset_slug(slug, owner),
        file_path=get_relative_path("videos", slug, f"{slug}.mp4"),
        metadata=metadata,
        status=status,
        owner=owner,
    )

    seconds = float(measured) if measured is not None else 0.0
    result = {
        "id": content_id,
        "slug": scoped_asset_slug(slug, owner),
        "file_path": get_relative_path("videos", slug, f"{slug}.mp4"),
        "status": status,
        "duration_sec": measured if measured is not None else duration,
        "model": output.get("model"),
    }
    result.update(finish(ctx, "generate_video", ctx["resolved"].get("provider") or "volcengine",
                         output.get("model"), seconds, "second", content_id))
    return result


def submit(
    prompt: str,
    duration: Optional[int] = None,
    ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    slug: Optional[str] = None,
) -> dict:
    """
    Submit an async video job: returns within seconds with a job id.

    No asset is created here — the asset appears when the job succeeds.
    """
    if not prompt:
        raise ValueError("prompt is required")

    ctx = prepare("video", {
        "prompt": prompt, "first_frame": first_frame, "last_frame": last_frame,
        "slug": slug,
    })
    owner = ctx["owner"]
    # 首帧形态解析必须在提交前 —— 提交之后失败等于白付一次任务费。
    # 抛 ValueError 即拒绝；这里不吞异常。
    first_frame = resolve_first_frame(first_frame, owner=owner)
    last_frame = resolve_first_frame(last_frame, owner=owner)
    slug = _slug_for(prompt, slug, "video")
    output_dir = ensure_directory_rel("videos", slug)
    output_path = output_dir / f"{slug}.mp4"

    cmd = _build_cmd(prompt, output_path, duration, ratio, resolution,
                     first_frame, last_frame, wait=False, timeout=None)
    try:
        stdout = run_bridge(cmd, ctx)
    except subprocess.CalledProcessError as e:
        raise classify_upstream_error("generate_video_submit", e.stderr) from e

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"upstream_error: video submission returned unparsable output: {e}") from e

    task_id = output.get("task_id") or ""
    if not task_id:
        raise RuntimeError(f"No task id in submission output: {stdout[:300]}")

    job_id = insert_job(owner=owner, kind="video", provider_task_id=task_id, slug=slug)
    return {
        "job_id": job_id,
        "status": "running",
        "provider_task_id": task_id,
        "slug": slug,
        "model": output.get("model"),
        "note": "poll video_job_status until succeeded/failed",
    }


def poll(job_id: str, owner: Optional[str] = None) -> dict:
    """
    Poll one video job; on success archive the asset and finish the job.

    owner=None means local/admin (any job); hosted callers pass their sub.
    Expired non-terminal jobs are flipped to 'expired' first.
    """
    expire_stale_jobs()
    job = get_job(job_id, owner=owner)
    if not job:
        raise ValueError(f"Job not found or not visible: {job_id}")
    if job["status"] in ("succeeded", "failed", "expired"):
        return _job_view(job)

    ctx = prepare("video", {})
    slug = job["slug"] or job_id
    output_path = ensure_directory_rel("videos", slug) / f"{slug}.mp4"

    cmd = [
        "python", str(LITELLM_BRIDGE), "video",
        "--status", job["provider_task_id"],
        "--output", str(output_path),
    ]
    try:
        stdout = run_bridge(cmd, ctx)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        # Transient poll failure must NEVER terminally fail a paid job:
        # the provider task keeps running upstream and the next poll may
        # still land the artifact. Only explicit provider terminal states
        # (below) write `failed`. (fix-hosted-generation-correctness)
        # NOTE: classify on e.stderr — CalledProcessError's own str() does
        # not carry the body.
        detail = getattr(e, "stderr", None) or str(e)
        err = classify_upstream_error("video_job_status", str(detail))
        if is_transient(e) or is_transient(err):
            view = _job_view(job)
            view["last_poll_error"] = str(err)[:300]
            view["note"] = "job stays running after a transient poll failure; keep polling, do not resubmit"
            return view
        update_job(job_id, status="failed", error=str(err)[:500])
        return _job_view(get_job(job_id, owner=owner))

    try:
        output = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as e:
        # unparsable status read (truncated/corrupted response): retryable,
        # job stays running; TTL is the backstop.
        view = _job_view(job)
        view["last_poll_error"] = f"upstream_transient: unparsable status response ({e})"[:300]
        view["note"] = "job stays running after a malformed status read; keep polling"
        return view

    status = output.get("status")
    if status in ("succeeded", "completed", "success", "done"):
        asset_id = generate_id()
        rel = get_relative_path("videos", slug, f"{slug}.mp4") if output.get("local_path") else ""
        # Bill measured seconds from the downloaded artifact, never the
        # hardcoded 5s or the upstream self-report.
        measured = _probe_video_seconds(output_path) if rel else None
        duration_source = "measured" if measured else "unavailable"
        metadata = {"provider_task_id": job["provider_task_id"],
                    "video_url": output.get("video_url")}
        if measured is not None:
            metadata["duration_sec"] = measured
            metadata["duration_source"] = duration_source
        insert_content(
            id=asset_id,
            type="video",
            slug=scoped_asset_slug(slug, job["owner"]),
            file_path=rel,
            metadata=metadata,
            status="rendered",
            owner=job["owner"],
        )
        update_job(job_id, status="succeeded", asset_id=asset_id)
        # No measured seconds => no billable artifact landed: bill 0 and say
        # so. The old hard-coded 5s is gone (never bill a claimed duration).
        billed = float(measured) if measured is not None else 0.0
        extras = finish(ctx, "generate_video_submit", ctx["resolved"].get("provider") or "volcengine",
                        None, billed, "second", asset_id)
        view = _job_view(get_job(job_id, owner=owner))
        view.update({"asset_id": asset_id, "duration_sec": measured, **extras})
        if measured is None:
            view["warnings"] = ["duration could not be measured (no artifact landed); billed 0 seconds"]
        return view
    if status in ("failed", "error", "cancelled"):
        update_job(job_id, status="failed",
                   error=str(output.get("error") or status)[:500])
    return _job_view(get_job(job_id, owner=owner))


def _job_view(job: dict) -> dict:
    return {
        "job_id": job["id"],
        "status": job["status"],
        "slug": job.get("slug"),
        "asset_id": job.get("asset_id"),
        "error": job.get("error"),
        "expires_at": job.get("expires_at"),
    }
