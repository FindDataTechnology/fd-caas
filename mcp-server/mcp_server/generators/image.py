"""
Image generator wrapper.

Wraps litellm-bridge.py image command via subprocess.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from ..db import insert_content, insert_asset, generate_id, scoped_asset_slug, get_asset
from ..utils import generate_slug, ensure_directory_rel, get_relative_path, VAAS_ROOT
from .runtime import finish, prepare, provider_for, run_bridge
from .errors import classify_upstream_error

# Path to litellm-bridge.py
SCRIPTS_DIR = VAAS_ROOT / "scripts"
LITELLM_BRIDGE = SCRIPTS_DIR / "litellm-bridge.py"

# Output filename extension whitelist (path-traversal guard; same discipline
# as the render plane's still_chart). This is the **request** contract: what a
# caller may ask for. The artifact is named after the container the upstream
# actually returned, which can fall outside this set (e.g. tiff/heic/avif);
# that case is delivered truthfully AND disclosed, see `unsupported_containers`.
IMAGE_FORMATS = {"png", "jpeg", "jpg", "webp"}

# Providers whose image request actually honors `n` (else the call must
# disclose the silent clamp, not fake multi-image support).
N_SUPPORTED_PROVIDERS = {"ciyuan"}


def edit(
    source: str,
    instruction: str,
    mask: Optional[str] = None,
    references: Optional[list] = None,
    n: int = 1,
    slug: Optional[str] = None,
    name: str = "seedream-grok",
) -> dict:
    """Edit one image with a natural-language instruction (POST /v1/images/edits).

    Distinct from `generate` on purpose (design D1): the two endpoints differ in
    payload shape, billing, and in what "success" means — an edit that returns
    the source unchanged is a failure, not a success. Sharing one entry would
    force those four differences into one function.

    Args:
        source: The image to edit — local path, http(s) URL, or data URI.
        instruction: What to change. Must be judgeable ("change the background
            to solid blue"), because an edit that cannot be told apart from the
            source is rejected downstream as `echoed_source`.
        mask: Optional region mask. Optional enhancement only — an upstream may
            ignore it; we never promise that a masked edit stays local.
        references: Optional supplementary reference images (paths/URLs/data
            URIs). source + references must not exceed the upstream limit (3).
        slug: Custom slug (auto-generated if not provided).

    Returns the same shape as `generate`, plus `source_asset_id`/`source_path`
    and `echo_check` so the caller can audit the lineage.
    """
    if not source:
        raise ValueError("source is required")
    if not instruction or not instruction.strip():
        raise ValueError("instruction is required")

    # registry imports this module at load time, so the lookup is deferred
    # (module-level import here would be circular).
    from ..registry import get_generator
    entry = get_generator("image", name) or {}
    provider_actual = (entry.get("provider") or "ciyuan").lower()

    # Lane health gate for the EDIT lane specifically (design D4). Generation
    # and editing are separate upstream endpoints with separate failure modes;
    # one being down must not be read as the other being down.
    from ..lane_health import lane_health as _lane
    lane = _lane("image_edit", provider_actual)
    if lane.get("health") == "down":
        from .credentials import CredentialUnavailable
        raise CredentialUnavailable(
            f"lane_down (image_edit/{provider_actual}): lane is down per the "
            f"lane_health ledger ({lane.get('reason_class')}); check "
            f"registry.list_generators for health"
        )

    ctx = prepare("image", {"source": source, "instruction": instruction, "slug": slug})

    if not slug:
        prefix = instruction[:20].lower().replace(" ", "-")
        prefix = "".join(c for c in prefix if c.isalnum() or c == "-")
        slug = generate_slug((prefix or "edit") + "-edit")

    output_dir = ensure_directory_rel("images", slug)
    output_path = output_dir / f"{slug}_00.png"

    cmd = [
        "python", str(LITELLM_BRIDGE), "image",
        "--prompt", instruction,
        "--source", source,
        "--output", str(output_path),
    ]
    if mask:
        cmd.extend(["--mask", mask])
    for ref in (references or []):
        cmd.extend(["--reference", ref])
    if n and n > 1:
        cmd.extend(["-n", str(n)])
    if entry.get("model"):
        cmd.extend(["--model", entry["model"]])

    try:
        stdout = run_bridge(cmd, ctx)
    except subprocess.CalledProcessError as e:
        raise classify_upstream_error("edit_image", e.stderr, provider_actual) from e

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"upstream_error: image edit lane returned unparsable output: {e}") from e

    images = output.get("images") or []
    if not images:
        raise RuntimeError(
            "upstream_error: image edit produced zero artifacts — nothing was written or billed"
        )

    produced: list[str] = []
    actual_format = "png"
    for i, img in enumerate(images):
        src_path = Path(img.get("local_path", ""))
        actual_format = (img.get("format") or "png").lower()
        if actual_format == "jpg":
            actual_format = "jpeg"
        dst_path = output_dir / f"{slug}_{i:02d}.{actual_format}"
        if src_path and src_path != dst_path and src_path.exists():
            src_path.rename(dst_path)
        produced.append(get_relative_path("images", slug, dst_path.name))

    rel_path = produced[0]

    # Lineage (design D2): the edited asset must point at what it was edited
    # from, so "which round of edits produced this" is answerable from the
    # ledger alone. A source given as a bare path (not a ledger asset) simply
    # has no parent — recorded in metadata instead of invented.
    source_asset_id = None
    source_asset = get_asset(source, owner=ctx["owner"]) if not source.startswith(
        ("http://", "https://", "data:", "/", "./", "../")) else None
    if source_asset:
        source_asset_id = source_asset.get("id")

    content_id = generate_id()
    metadata = {
        "instruction": instruction,
        "source": source,
        "source_asset_id": source_asset_id,
        "has_mask": bool(mask),
        "reference_count": len(references or []),
        "format": actual_format,
        "model": output.get("model"),
        "echo_check": output.get("echo_check"),
        "file_paths": produced,
        "count": len(produced),
    }

    scoped = scoped_asset_slug(slug, ctx["owner"])
    insert_asset(
        type="image",
        slug=scoped,
        file_path=rel_path,
        metadata=metadata,
        stage="rendered",
        id=content_id,
        parent_id=source_asset_id,
        provider=provider_actual,
        owner=ctx["owner"],
    )

    warnings: list[str] = list(output.get("warnings") or [])
    if mask:
        warnings.append(
            "mask was supplied but upstream mask support is unverified: "
            "the edit may apply to the whole image"
        )
    if output.get("echo_check") == "not_compared":
        warnings.append(
            "source was not read locally (URL/data URI) — the echoed-source "
            "check did not run for this call"
        )

    result = {
        "id": content_id,
        "slug": scoped,
        "file_paths": produced,
        "count": len(produced),
        "model": output.get("model"),
        "source_asset_id": source_asset_id,
        "instruction": instruction,
        "warnings": warnings,
    }
    result.update(finish(ctx, "edit_image", provider_actual,
                         output.get("model"), len(produced), "image", content_id))
    return result


def generate(
    prompt: str,
    size: Optional[str] = None,
    output_format: str = "jpeg",
    n: int = 1,
    slug: Optional[str] = None
) -> dict:
    """
    Generate images from text prompt.

    Args:
        prompt: Image description (required)
        size: Image size (2K/3K/4K or WxH, default 2K)
        output_format: Output format (png/jpeg/jpg/webp, default jpeg)
        n: Number of images to generate (1-15, default 1). Providers that
           ignore n are reported via warnings (never silently one-for-many).
        slug: Custom slug (auto-generated if not provided)

    Returns:
        Dict with:
            - id: Content UUID
            - slug: Human-readable identifier
            - file_paths: List of relative paths from downloads/
            - count: Number of images generated
            - model: Model name used
            - warnings: disclosure list (provider count clamp etc.)

    Raises:
        ValueError: If prompt not provided or n out of range
        RuntimeError: If image generation fails (classified, upstream-safe)
    """
    if not prompt:
        raise ValueError("prompt is required")

    if n < 1 or n > 15:
        raise ValueError("n must be between 1 and 15")

    if output_format not in IMAGE_FORMATS:
        raise ValueError(
            f"invalid_input: output_format {output_format!r} not allowed "
            f"(supported: {sorted(IMAGE_FORMATS)})"
        )

    ctx = prepare("image", {
        "prompt": prompt, "slug": slug,
    })
    provider_actual = provider_for(ctx, "IMAGE_PROVIDER")

    # Generate slug if not provided
    if not slug:
        prefix = prompt[:20].lower().replace(" ", "-")
        prefix = "".join(c for c in prefix if c.isalnum() or c == "-")
        slug = generate_slug(prefix or "image")

    # Create output directory
    output_dir = ensure_directory_rel("images", slug)

    # Build command
    cmd = [
        "python", str(LITELLM_BRIDGE), "image",
        "--prompt", prompt,
        "--output-format", output_format,
        "-n", str(n)
    ]

    if size:
        cmd.extend(["--size", size])

    # Generation stays text-only: image-to-image is its own registered method
    # (`edit_image`, /v1/images/edits) because the two endpoints differ in
    # payload shape, billing, and what "success" means. Do not fold a source
    # image into this entry.
    output_path = output_dir / f"{slug}_00.{output_format}"
    cmd.extend(["--output", str(output_path)])

    # Execute subprocess
    try:
        stdout = run_bridge(cmd, ctx)
    except subprocess.CalledProcessError as e:
        raise classify_upstream_error("generate_image", e.stderr, provider_actual) from e

    # Parse output
    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"upstream_error: image lane returned unparsable output: {e}") from e

    # Collect generated image paths
    file_paths = []
    format_mismatches: list[tuple[str, str]] = []
    unsupported_containers: set[str] = set()
    images = output.get("images", [])

    for i, img in enumerate(images):
        local_path = img.get("local_path")
        if local_path:
            # Rename to our naming convention
            src_path = Path(local_path)
            # Extension follows the ACTUAL container, not the requested format:
            # the driver reports `format` from magic bytes. Naming a PNG
            # `_00.jpeg` lies to every downstream consumer (publish skills pick
            # the upload type by extension), so we prefer the detected value and
            # disclose the divergence below instead of pretending it matched.
            actual_format = (img.get("format") or output_format).lower()
            if actual_format == "jpg":
                actual_format = "jpeg"
            dst_name = f"{slug}_{i:02d}.{actual_format}"
            dst_path = output_dir / dst_name

            if actual_format != output_format:
                format_mismatches.append((actual_format, output_format))
            if actual_format not in IMAGE_FORMATS:
                unsupported_containers.add(actual_format)

            if src_path != dst_path:
                src_path.rename(dst_path)

            file_paths.append(get_relative_path("images", slug, dst_name))

    # If no images in output, check if the output file exists
    if not file_paths and output_path.exists():
        file_paths.append(get_relative_path("images", slug, f"{slug}_00.{output_format}"))

    # Parameter honesty: a zero-image "success", a provider that ignored n, and
    # a container that differs from the requested format are all disclosed,
    # never billed silently as if the request landed as asked.
    warnings: list[str] = []
    if format_mismatches:
        pairs = sorted({f"{a}→{r}" for a, r in format_mismatches})
        warnings.append(
            "upstream returned a different container than requested "
            f"({', '.join(pairs)}); files are named by their actual format"
        )
    if unsupported_containers:
        # 契约外容器：产物保留并如实命名（丢掉它等于让上游白计费一次），
        # 但必须点名——下游（发布技能按扩展名选上传类型）可能不认。
        warnings.append(
            "artifact container(s) outside the supported output formats "
            f"{sorted(unsupported_containers)} (contract: {sorted(IMAGE_FORMATS)}); "
            "kept and named truthfully — convert before publishing"
        )
    if len(file_paths) < n:
        if provider_actual not in N_SUPPORTED_PROVIDERS:
            warnings.append(
                f"provider '{provider_actual}' ignores n: {len(file_paths)} image(s) "
                f"produced for a request of {n}"
            )
        else:
            warnings.append(
                f"upstream returned {len(file_paths)} of {n} requested images"
            )
    if not file_paths:
        raise RuntimeError(
            "upstream_error: image generation produced zero artifacts — nothing "
            "was written or billed"
        )

    # Ledger: the row's file_path is the primary image; every produced file
    # is registered in metadata.file_paths. GC reclaims the whole slug
    # directory when this row ages out, so no artifact leaks untracked.
    content_id = generate_id()
    metadata = {
        "prompt": prompt,
        "size": size,
        "format": output_format,
        "model": output.get("model"),
        "count": len(file_paths),
        "file_paths": file_paths,
        "warnings": warnings,
    }

    scoped = scoped_asset_slug(slug, ctx["owner"])
    insert_content(
        id=content_id,
        type="image",
        slug=scoped,
        file_path=file_paths[0],
        metadata=metadata,
        status="rendered",
        owner=ctx["owner"],
    )

    result = {
        "id": content_id,
        "slug": scoped,
        "file_paths": file_paths,
        "count": len(file_paths),
        "model": output.get("model"),
        "warnings": warnings,
    }
    result.update(finish(ctx, "generate_image", provider_actual,
                         output.get("model"), len(file_paths), "image", content_id))
    return result
