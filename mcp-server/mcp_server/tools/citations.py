"""
citation_* namespace: serious-text citation store + draft gate.

Owner-scoped (current_owner) like every other content tool. Recording a
citation binds it to an asset (provenance triple + status); gating a draft
strips unresolvable/blocked anchors and reports what stays 待核. The
verification engine itself is fd-shilun's — not duplicated here.
"""

from typing import Optional

from ..citations import gate_draft as _gate
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
