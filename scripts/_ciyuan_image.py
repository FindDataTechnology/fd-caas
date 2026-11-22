"""
萬星·词元 (Ciyuan) image helper for litellm-bridge.

Calls the ciyuan lane `POST /v1/images/generations` (OpenAI images shape),
decodes data[].b64_json (or downloads data[].url when that is all the upstream
returns) and writes the bytes to disk. Result dict mirrors the volcengine path:
{ok, provider, model, images:[{local_path, file_size_bytes}], ...}

产物形态（add-image-lane-fallback）：`b64_json` 优先，缺失时才取 `url`。url 形态是
新增的网络面，自带安全包封（scheme 白名单 / 超时 / 体积上限 / Content-Type + 魔数双校验），
失败信息只带主机名不带查询串（签名 URL 的凭据常在 query 里）。

实测白名单（2026-10-16，VAAS 专用账号 user94 真调）：
  可用: gemini-3-pro-image → 200 合规 JSON，data[].b64_json 解码得真 JPEG
  禁用: gpt-image-1 / gpt-image-2 → HTTP 200 但 Content-Type: text/html
        （openai-next 网页 UI；两个不同 prompt 返回同一 favicon URL——
        不是生成结果，是上游缺陷）；本客户端对非 JSON 应答一律报错，
        绝不从 HTML 里猜图片 URL 充当产物。
        doubao-seedream-* / flux-* / imagen-4 → 400 "requires an image model"
换模型前先实调——/v1/models 列的是「可识别」不是「可服务」。
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# url-form产物 downloads: envelope (see design D2). Both knobs are env-overridable
# so an operator can widen them without a code change.
MAX_URL_BYTES = int(os.environ.get("CIYUAN_IMAGE_MAX_URL_BYTES") or 24 * 1024 * 1024)
URL_TIMEOUT_S = float(os.environ.get("CIYUAN_IMAGE_URL_TIMEOUT_S") or 60)

# Magic numbers, not just Content-Type: an upstream can label HTML as image/jpeg.
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)


def _detect_format(raw: bytes) -> str | None:
    """Actual image container from magic bytes, or None if unrecognised.

    Used by BOTH product shapes. Before this, only the url path was validated;
    a `b64_json` payload was decoded and written with no check at all, so an
    upstream returning base64-of-HTML produced a file that looked like an image
    to everything downstream. Same lane, two shapes, one trust level.
    """
    for sig, fmt in _IMAGE_MAGIC:
        if raw.startswith(sig):
            return fmt
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    # ISOBMFF (HEIF/AVIF family): ....ftyp<brand>
    if raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "heic"
    return None


def _looks_like_image(raw: bytes) -> bool:
    return _detect_format(raw) is not None


def _host_only(url: str) -> str:
    """Hostname for error messages — never the query string (signed URLs)."""
    try:
        return urllib.parse.urlparse(url).hostname or "<unknown>"
    except Exception:
        return "<unparseable>"


def _download_image(url: str, dest: str) -> tuple[int, str]:
    """Download one image URL into `dest`; return (bytes written, detected format).

    The format comes from the bytes we already have in memory — the caller must
    NOT re-open the file to sniff it (that leaked a descriptor per image on a
    long-lived server, and re-read what was just written).

    Fails closed on every envelope violation. Reads in bounded chunks so an
    over-size body is cut off mid-stream instead of after the fact.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(
            f"ciyuan image url has unsupported scheme {parsed.scheme!r} "
            f"(host={parsed.hostname or '<none>'}) — only http/https allowed"
        )
    req = urllib.request.Request(url, method="GET", headers={"Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT_S) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("image/"):
                raise RuntimeError(
                    f"ciyuan image url host={parsed.hostname} returned "
                    f"Content-Type {content_type or '<none>'} — not an image"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_URL_BYTES:
                    raise RuntimeError(
                        f"ciyuan image url host={parsed.hostname} exceeded "
                        f"{MAX_URL_BYTES} bytes — refusing oversized产物"
                    )
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"ciyuan image url host={parsed.hostname} download failed (HTTP {e.code})"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"ciyuan image url host={parsed.hostname} download failed ({e.reason})"
        ) from e

    raw = b"".join(chunks)
    if not raw:
        raise RuntimeError(f"ciyuan image url host={parsed.hostname} returned an empty body")
    if not _looks_like_image(raw):
        raise RuntimeError(
            f"ciyuan image url host={parsed.hostname} body is not a known image format "
            "(magic number check failed)"
        )
    fmt = _detect_format(raw)
    with open(dest, "wb") as f:
        f.write(raw)
    return len(raw), fmt or ""


# 上游编辑端点的源图上限（sub2api `grokMediaMaxEditSourceImages = 3`）。
MAX_EDIT_SOURCE_IMAGES = 3


def _as_image_url(source: str) -> str:
    """Turn a source spec into an `image_url` string the upstream accepts.

    Accepts an http(s) URL as-is; otherwise treats it as a local file path and
    inlines it as a data URI. Fails closed: an unreadable or non-image file must
    never reach the request body (that would be a paid call with a broken source).
    """
    if source.startswith(("http://", "https://")):
        return source
    if source.startswith("data:"):
        return source
    raw = Path(source).expanduser().read_bytes() if Path(source).expanduser().is_file() else b""
    if not raw:
        raise RuntimeError(f"ciyuan image edit: source image not found or empty: {source}")
    fmt = _detect_format(raw)
    if fmt is None:
        raise RuntimeError(
            "ciyuan image edit: source is not a known image format "
            f"(first bytes: {raw[:12].hex()}) — refusing to submit"
        )
    mime = "image/jpeg" if fmt == "jpeg" else f"image/{fmt}"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _edit_once(
    *,
    source: str,
    instruction: str,
    model: str,
    api_key: str,
    base_url: str,
    output_path: str,
    mask: str | None = None,
    references: list | None = None,
) -> dict:
    """Edit one image with a natural-language instruction (POST /v1/images/edits).

    Result shape mirrors `generate()`; additionally carries `source_instruction`
    and `echo_check` so the caller can audit what was actually asked for.

    判据（design D3）：产物字节必须与源图不同。相同 = 上游没编辑、把原图回显了，
    按失败处理。该判据判得了「原图回显」，判不了「几乎没改」——
    极保守的指令返回近似图是合法的，不在这里拦。
    """
    if not api_key:
        raise ValueError("CIYUAN_API_KEY is required for ciyuan image edit")
    if not base_url:
        raise ValueError("CIYUAN_BASE_URL is required for ciyuan image edit")
    if not instruction or not instruction.strip():
        raise ValueError("ciyuan image edit: instruction is required")

    sources = [source, *(references or [])]
    if len(sources) > MAX_EDIT_SOURCE_IMAGES:
        raise ValueError(
            f"ciyuan image edit: {len(sources)} source images "
            f"(source + references) exceed the upstream limit of "
            f"{MAX_EDIT_SOURCE_IMAGES} — refusing rather than truncating"
        )

    # 源图字节留一份，用于产物对比（只比第一张，即被编辑的那张）
    source_first = sources[0]
    source_bytes = None
    if not source_first.startswith(("http://", "https://", "data:")):
        p = Path(source_first).expanduser()
        if p.is_file():
            source_bytes = p.read_bytes()

    start = time.time()
    # 上游 edits 的契约是 `image`（单数，被编辑的那张）+ 可选 `images`（补充参考图），
    # 不是 OpenAI 标准的 `images[]`。sub2api 的 multipart 分支正是产出这个形状
    # （payload["image"] = images[0]，多于一张才带 images）；JSON 分支没有这层转换，
    # 所以我们必须自己按同一形状发，否则上游回 400「改图要带 image」。
    urls = [_as_image_url(s) for s in sources]
    payload: dict = {
        "model": model,
        "prompt": instruction.strip(),
        "image": {"url": urls[0], "type": "image_url"},
    }
    if len(urls) > 1:
        payload["images"] = [{"url": u, "type": "image_url"} for u in urls[1:]]
    if mask:
        payload["mask"] = {"url": _as_image_url(mask), "type": "image_url"}

    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/images/edits",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"ciyuan image edit failed (HTTP {e.code}): {err_body}") from e

    if "json" not in content_type.lower():
        raise RuntimeError(
            f"ciyuan image edit model '{model}' returned non-JSON response "
            f"(Content-Type: {content_type}) — 应答形状不合规"
        )
    try:
        doc = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(f"ciyuan image edit response is not valid JSON: {e}") from e

    items = doc.get("data") or []
    if not items:
        raise RuntimeError(f"ciyuan image edit returned no data items: {json.dumps(doc)[:300]}")

    item = items[0]
    raw = None
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
        fmt = _detect_format(raw)
        if fmt is None:
            raise RuntimeError(
                "ciyuan image edit b64 payload is not a known image format "
                f"(first bytes: {raw[:12].hex()}) — 不写盘"
            )
    elif item.get("url"):
        written, fmt = _download_image(str(item["url"]), output_path)
        raw = Path(output_path).read_bytes()
    else:
        raise RuntimeError(
            "ciyuan image edit item has neither b64_json nor url (keys: "
            f"{sorted(item.keys())}) — 产物形态不合规"
        )

    if source_bytes is not None and raw == source_bytes:
        raise RuntimeError(
            "ciyuan image edit returned bytes identical to the source image "
            "(echoed_source) — the upstream did not edit; treating as failure"
        )

    if not Path(output_path).exists():
        with open(output_path, "wb") as f:
            f.write(raw)

    return {
        "ok": True,
        "provider": "ciyuan",
        "model": model,
        "source_instruction": instruction.strip(),
        "source_count": len(sources),
        "has_mask": bool(mask),
        "echo_check": "different" if source_bytes is not None else "not_compared",
        "images": [{"local_path": output_path, "file_size_bytes": len(raw), "format": fmt}],
        "usage": doc.get("usage"),
        "api_latency_ms": round((time.time() - start) * 1000),
    }



def edit(
    *,
    source: str,
    instruction: str,
    model: str,
    api_key: str,
    base_url: str,
    output_path: str,
    mask: str | None = None,
    references: list | None = None,
    n: int = 1,
) -> dict:
    """Edit one image n times (the upstream edit endpoint is single-image).

    `n > 1` is satisfied by **n separate upstream calls**, not by an upstream
    multi-image response — nobody has proven the edit endpoint honours `n`, and
    faking multi-image support would be a lie the caller cannot see. The result
    carries a warning saying so.

    Output paths for n>1: `<stem>_00<ext>`, `<stem>_01<ext>`, … (index 0 keeps
    the caller's exact path so the single-image case is unchanged).
    """
    if n < 1:
        raise ValueError("ciyuan image edit: n must be >= 1")
    if n == 1:
        return _edit_once(
            source=source, instruction=instruction, model=model,
            api_key=api_key, base_url=base_url, output_path=output_path,
            mask=mask, references=references,
        )

    base = Path(output_path)
    images: list[dict] = []
    warnings: list[str] = [
        f"the upstream edit endpoint is single-image: {n} images were produced "
        f"by {n} separate calls, not by one multi-image response"
    ]
    merged: dict = {}
    for i in range(n):
        dest = base.with_name(f"{base.stem}_{i:02d}{base.suffix}")
        res = _edit_once(
            source=source, instruction=instruction, model=model,
            api_key=api_key, base_url=base_url, output_path=str(dest),
            mask=mask, references=references,
        )
        images.extend(res.get("images") or [])
        merged = merged or {k: v for k, v in res.items() if k != "images"}
    merged["images"] = images
    merged["warnings"] = warnings
    return merged

def generate(
    *,
    prompt: str,
    model: str,
    size: str | None,
    n: int,
    output_paths: list,  # one path per image
    api_key: str,
    base_url: str,
    extra: dict | None = None,
) -> dict:
    if not api_key:
        raise ValueError("CIYUAN_API_KEY is required for ciyuan image")
    if not base_url:
        raise ValueError("CIYUAN_BASE_URL is required for ciyuan image (e.g. <CIYUAN_GATEWAY_BASE>)")
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")

    start = time.time()
    payload: dict = {"model": model, "prompt": prompt, "n": max(1, min(n, len(output_paths)))}
    if size:
        payload["size"] = size
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})

    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # 明确要 JSON：上游网页缺陷会把 HTML 当 200 返回
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"ciyuan image failed (HTTP {e.code}): {err_body}") from e

    # 200 ≠ 成功：必须校验 Content-Type 与 JSON 可解析性，不能只看状态码。
    if "json" not in content_type.lower():
        raise RuntimeError(
            f"ciyuan image model '{model}' returned non-JSON response "
            f"(Content-Type: {content_type}) — 应答形状不合规，该模型不可用于图像车道"
        )
    try:
        doc = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(f"ciyuan image response is not valid JSON: {e}") from e

    items = doc.get("data") or []
    if not items:
        raise RuntimeError(f"ciyuan image returned no data items: {json.dumps(doc)[:300]}")

    images = []
    for i, item in enumerate(items[: len(output_paths)]):
        path = str(output_paths[i])
        b64 = item.get("b64_json")
        if b64:
            raw = base64.b64decode(b64)
            # 与 url 形态同等级别的校验：解码出来的必须真的是图像。
            # 只在「认不出」时失败，并带上首字节，便于把新格式补进魔数表。
            fmt = _detect_format(raw)
            if fmt is None:
                raise RuntimeError(
                    "ciyuan image b64 payload is not a known image format "
                    f"(first bytes: {raw[:12].hex()}) — 不写盘，避免产出打不开的假产物"
                )
            with open(path, "wb") as f:
                f.write(raw)
            images.append({"local_path": path, "file_size_bytes": len(raw), "format": fmt})
            continue
        url = item.get("url")
        if url:
            # 只在缺 b64 时才走 url（design D1：b64 优先，顺序固定）
            written, fmt = _download_image(str(url), path)
            images.append({"local_path": path, "file_size_bytes": written, "format": fmt})
            continue
        # 两种形态都缺：如实报错，绝不猜
        raise RuntimeError(
            "ciyuan image item has neither b64_json nor url (keys: "
            f"{sorted(item.keys())}) — 产物形态不合规"
        )

    return {
        "ok": True,
        "provider": "ciyuan",
        "model": model,
        "prompt": prompt,
        "images": images,
        "usage": doc.get("usage"),
        "api_latency_ms": round((time.time() - start) * 1000),
    }
