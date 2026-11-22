"""
registry_* namespace: generation method discovery.

Lane health (deliver-hosted-surface W3): every lane-dependent entry carries
its lane's health sub-dict (health/reason_class/last_verified) from the
machine-readable ledger lane_health.json; a down lane must never be
advertised as usable (spec generation-registry「方式可发现性」).
"""

from typing import Optional

from ..registry import list_generators as _list, get_generator

# registry type -> health-lane kind (cloud lanes; local compute maps to the
# always-healthy render.local entry)
_KIND_BY_TYPE = {"voice": "voice", "image": "image", "video": "video",
                 "cover": "render", "chart": "render", "doc": "render"}

# Same type, different upstream endpoint => different lane. Image editing is a
# separate endpoint from generation and dies independently (spec
# generation-registry「生成可用而编辑不可用」), so it must not inherit the
# generation lane's verdict.
_KIND_BY_DRIVER = {"edit_image": "image_edit"}


def _kind_for(type_name: str, entry: dict) -> str | None:
    return _KIND_BY_DRIVER.get(entry.get("driver") or "") or _KIND_BY_TYPE.get(type_name)


def _annotate_items(items: list, type_name: str) -> None:
    """Attach each entry's own lane — entries sharing a type may not share a lane."""
    from ..lane_health import lane_health as _lane
    for e in items:
        kind = _kind_for(type_name, e)
        prov = (e.get("provider") or "").lower()
        if kind and prov:
            e["lane"] = _lane(kind, prov)


def registry_list_generators(type: Optional[str] = None) -> dict:
    """
    List available generation methods, optionally filtered by asset type.

    Args:
        type: Optional filter (voice/image/video/cover/chart/doc)

    Returns:
        Dict with items[] (or a single type's items) listing name/provider/
        model — lane-dependent entries additionally carry
        `lane: {health, reason_class, reason, last_verified}`.
    """
    out = _list(type)
    if out.get("type"):
        if isinstance(out.get("items"), list):
            _annotate_items(out["items"], out["type"])
    else:
        for tname, items in out.items():
            if isinstance(items, list):
                _annotate_items(items, tname)
    return out


def registry_describe_generator(type: str, name: str) -> dict:
    """
    Describe a specific generation method.

    Args:
        type: Asset type (voice/image/video/cover/chart/doc)
        name: Method name (e.g. seedance, voiceover, echarts, chromium)

    Raises:
        ValueError: Unknown type or method
    """
    entry = get_generator(type, name)
    if not entry:
        available = [e["name"] for e in _list(type).get("items", [])]
        raise ValueError(f"Unknown generator: {type}/{name}. Available: {available}")
    _annotate_items([entry], type)
    return {"type": type, **entry}
