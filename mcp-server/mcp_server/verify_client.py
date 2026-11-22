"""
Thin client to the fd-shilun verification engine (openspec:
strengthen-caas-serious-text / task 2.3).

The engine is NOT re-implemented here — it lives in fd-shilun's
`/api/v1/verify` (stateless, service-token guarded). CaaS posts text +
citations and maps the returned blocked/pending/pass split back onto the
material-store citation statuses. Absent config or an unreachable engine must
surface as a reason, never as a silent "all pass".
"""

import json
import os
import urllib.request
from typing import Optional

DEFAULT_TIMEOUT = float(os.getenv("VAAS_VERIFY_TIMEOUT", "30"))


def _endpoint() -> Optional[str]:
    url = (os.getenv("VAAS_VERIFY_URL") or "").strip()
    return url or None


def _token() -> Optional[str]:
    tok = (os.getenv("VAAS_VERIFY_TOKEN") or "").strip()
    return tok or None


def verify_refs(text: str, citations: list[dict], mode: Optional[str] = None) -> dict:
    """Call the verify engine. Returns {ok, statuses{id->status}, blocked,
    pending, reason?}. Each citation dict must carry id + kind + meta/ref_meta.
    """
    url = _endpoint()
    token = _token()
    if not url or not token:
        return {"ok": False, "statuses": {}, "blocked": [], "pending": [],
                "reason": "not_configured",
                "detail": "VAAS_VERIFY_URL/VAAS_VERIFY_TOKEN 未配置"}

    payload = {
        "text": text,
        "citations": [
            {"id": c.get("id"), "kind": c.get("kind") or c.get("source") or "openalex",
             "meta": c.get("meta") or c.get("ref_meta") or {}}
            for c in citations if c.get("id")
        ],
    }
    if mode:
        payload["mode"] = mode
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "statuses": {}, "blocked": [], "pending": [],
                "reason": "unauthorized" if exc.code == 401 else "upstream",
                "detail": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 — unreachable must be visible
        return {"ok": False, "statuses": {}, "blocked": [], "pending": [],
                "reason": "unreachable", "detail": f"{type(exc).__name__}: {exc}"}

    blocked = body.get("blocked_citation_ids") or []
    pending = body.get("pending_citation_ids") or []
    blocked_set, pending_set = set(blocked), set(pending)
    statuses = {}
    for c in payload["citations"]:
        cid = c["id"]
        if cid in blocked_set:
            statuses[cid] = "blocked"
        elif cid in pending_set:
            statuses[cid] = "pending"
        else:
            statuses[cid] = "supported"
    return {"ok": True, "statuses": statuses, "blocked": blocked,
            "pending": pending, "mode": body.get("mode"),
            "counts": body.get("counts", {})}
