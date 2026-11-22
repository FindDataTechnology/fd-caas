"""
Source lane: dispatch a content-domain query to an external source MCP face.

Mirror of the generation registry (registry.py): a lane is one entry in
sources.json; adding a domain is one JSON entry + (at most) one driver line,
never a core-dispatch change. The driver is an ADAPTER — it only wraps a call
to an already-running source MCP face; it never ingests or crawls (proposal
"source-lane": CaaS stays an orchestration layer).

Every hit is normalized to the unified provenance triple from the workspace
contract `text-query-surface` (source/stable_id/as_of). Empty results MUST
carry a reason; an upstream failure MUST be reported, never disguised as "no
content".
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

SOURCES_PATH = Path(__file__).parent / "sources.json"

# Tool names for source points may share one base endpoint; a lane names only
# its service and the tenant-neutral gateway turns it into a URL.
DEFAULT_BASE = "https://mcp.finddatatech.cloud"
_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _base_url() -> str:
    return os.getenv("VAAS_SOURCE_BASE", DEFAULT_BASE).rstrip("/")


def _resolve_endpoint(service: str) -> str:
    """Explicit per-service override wins; otherwise base/service/mcp."""
    override = os.getenv("VAAS_SOURCE_" + service.upper().replace("-", "_") + "_URL")
    if override:
        return override
    return f"{_base_url()}/{service}/mcp"


def load_sources() -> dict:
    with open(SOURCES_PATH, encoding="utf-8") as f:
        return json.load(f)


def list_domains() -> list[str]:
    return sorted(load_sources())


def _get_lane(domain: str) -> dict:
    lanes = load_sources()
    if domain not in lanes:
        raise ValueError(f"Unknown source domain: {domain}. Available: {sorted(lanes)}")
    return lanes[domain]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_of_from(value) -> Optional[str]:
    if not value:
        return None
    return value if isinstance(value, str) else str(value)


def _normalize_hit(raw: dict, default_source: str) -> dict:
    """Fold one upstream hit into {source, stable_id, as_of, ...} .

    Accepts either an already-provenanced item (has a `provenance` dict, e.g.
    legal/law_search and text-core local-corpus reads) or a federated hit with
    loose ids (doi/arxiv_id/work_id/url) whose stable_id we derive.
    """
    prov = raw.get("provenance") if isinstance(raw, dict) else None
    if isinstance(prov, dict) and prov.get("stable_id"):
        out = dict(raw)
        out.setdefault("source", prov.get("source", default_source))
        out["stable_id"] = prov.get("stable_id")
        out["as_of"] = _as_of_from(prov.get("as_of"))
        return out

    raw = dict(raw or {})
    stable = (
        raw.get("stable_id")
        or raw.get("doi")
        or raw.get("arxiv_id")
        or raw.get("work_id")
        or raw.get("id")
        or raw.get("url")
    )
    raw["source"] = raw.get("source") or default_source
    # Open-web federated results are not indexed documents: as_of is the
    # retrieval moment. Indexed hits already carried provenance above.
    raw["stable_id"] = str(stable) if stable else ""
    raw["as_of"] = _as_of_from(raw.get("as_of")) or _now()
    return raw


# ─── Drivers ──────────────────────────────────────────────────────────────

async def _call_mcp_tool(url: str, tool: str, arguments: dict, timeout: float) -> dict:
    """Call one tool on a remote streamable-HTTP MCP face; return its structured data.

    fastmcp runs sync tools inside a worker thread, so there is no running
    event loop on this path — asyncio.run is safe here (verified: the tools are
    registered as sync functions).
    """
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(url)
    async with Client(transport) as client:
        result = await client.call_tool(tool, arguments, timeout=timeout)
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise RuntimeError(f"upstream tool {tool} returned an error")
    return getattr(result, "data", None) or {}


def _fetch(url: str, tool: str, arguments: dict, timeout: float) -> dict:
    """Sync boundary over the async MCP client (patchable in tests)."""
    return asyncio.run(_call_mcp_tool(url, tool, arguments, timeout))


def source_mcp(lane: dict, domain: str, query: str, limit: int) -> dict:
    """Adapter driver: one source_search, normalized + reason-bearing on empty."""
    url = _resolve_endpoint(lane["service"])
    timeout = float(os.getenv("VAAS_SOURCE_TIMEOUT", "30"))
    tool = lane["search_tool"]
    arguments = {"query": query, "limit": limit}

    try:
        data = _fetch(url, tool, arguments, timeout)
    except Exception as exc:  # noqa: BLE001 — upstream failure must be visible, not empty
        return {"domain": domain, "service": lane["service"], "hits": [],
                "reason": "unreachable", "detail": f"{type(exc).__name__}: {exc}"}

    # Upstream error envelope (text-core uses {ok:false, error:{code}}).
    if isinstance(data, dict) and data.get("ok") is False:
        err = data.get("error") or {}
        return {"domain": domain, "service": lane["service"], "hits": [],
                "reason": _reason_from_code(err.get("code")),
                "detail": err.get("message") or "upstream reported failure"}

    raw_hits = []
    if isinstance(data, dict):
        raw_hits = data.get("hits") or data.get("results") or []
    hits = [_normalize_hit(h, lane["service"]) for h in raw_hits if isinstance(h, dict)]

    # Surface upstream degradation as a reason even when some hits came back.
    degraded = data.get("degraded") if isinstance(data, dict) else None
    out = {"domain": domain, "service": lane["service"], "hits": hits,
           "count": len(hits)}
    if not hits:
        out["reason"] = "not_indexed"
        out["detail"] = "no hits from source"
    elif degraded:
        out["degraded"] = degraded
    return out


def _reason_from_code(code) -> str:
    c = (code or "").upper()
    if "UPSTREAM" in c:
        return "upstream"
    if "QUOTA" in c or "RATE" in c:
        return "quota"
    if "NOT_FOUND" in c or "NOT_INDEXED" in c:
        return "not_indexed"
    return "upstream"


SOURCE_DRIVERS: dict[str, Callable] = {
    "source_mcp": source_mcp,
}


def source_search(domain: str, query: str, limit: int = 10) -> dict:
    """Search one content domain through its registered source lane.

    Raises ValueError for an unknown domain (lists available domains).
    """
    lane = _get_lane(domain)
    driver = SOURCE_DRIVERS.get(lane["driver"])
    if not driver:
        raise ValueError(f"Driver not implemented: {lane['driver']}")
    return driver(lane, domain, query, limit)
