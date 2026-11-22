"""
citation_* namespace: serious-text citation store + draft gate.

Owner-scoped (current_owner) like every other content tool. Recording a
citation binds it to an asset (provenance triple + status); gating a draft
strips unresolvable/blocked anchors and reports what stays 待核. The
verification engine itself is fd-shilun's — not duplicated here.
"""

from typing import Optional

from ..citations import gate_draft as _gate
from ..verify_client import verify_refs as _verify
from ..db import (
    insert_citation, list_citations, get_citation, update_citation_status,
    CITATION_STATUSES,
)
from ..hosting import current_owner


def citation_record(
    asset_id: str,
    source: str,
    stable_id: str,
    as_of: str,
    status: str = "resolvable",
    ref_meta: Optional[dict] = None,
) -> dict:
    """Record one citation (provenance triple + status) bound to an asset.

    status is one of resolvable|authentic|supported|pending|blocked. The
    caller is responsible for having run verification (fd-shilun engine); this
    tool only persists the conclusion, owner-scoped to the current tenant.
    """
    owner = current_owner()
    cid = insert_citation(asset_id, source, stable_id, as_of,
                          owner=owner, status=status, ref_meta=ref_meta)
    return {"id": cid, "asset_id": asset_id, "status": status}


def citation_list(asset_id: str) -> dict:
    """List an asset's citations (owner-scoped)."""
    owner = current_owner()
    items = list_citations(asset_id, owner=owner)
    return {"asset_id": asset_id, "citations": items, "count": len(items)}


def citation_gate(text: str, asset_id: str) -> dict:
    """Apply the citation gate to a draft against an asset's citations.

    Strips anchors whose id is unknown OR whose citation is blocked (宁删勿假),
    keeps pending citations flagged 待核, and reports the disposition. Returns
    cleaned_text (the user's source text is unchanged), stripped ids, and the
    passed/pending/blocked split.
    """
    owner = current_owner()
    cites = list_citations(asset_id, owner=owner)
    return _gate(text, cites)


def citation_verify(text: str, asset_id: str, mode: Optional[str] = None) -> dict:
    """Run the fd-shilun 3-level verify engine on a draft and persist the
    resulting per-citation statuses.

    The engine is called over HTTP (not re-implemented). Returned statuses are
    written back to this asset's citations (blocked/pending/supported) so a
    subsequent citation_gate reflects them. If the engine is not configured or
    unreachable, nothing is overwritten and a `reason` is returned — a failed
    verify MUST NOT silently mark citations as passed.
    """
    owner = current_owner()
    cites = list_citations(asset_id, owner=owner)
    result = _verify(text, cites, mode=mode)
    updated = 0
    if result.get("ok"):
        for cid, status in (result.get("statuses") or {}).items():
            # Only update citations this tenant owns (get_citation enforces it).
            if get_citation(cid, owner=owner) is not None:
                update_citation_status(cid, status)
                updated += 1
    return {
        "asset_id": asset_id,
        "ok": bool(result.get("ok")),
        "updated": updated,
        "blocked": result.get("blocked", []),
        "pending": result.get("pending", []),
        "reason": result.get("reason"),
        "detail": result.get("detail"),
        "counts": result.get("counts"),
        "mode": result.get("mode"),
    }
