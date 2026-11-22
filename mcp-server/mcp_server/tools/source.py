"""
source_* namespace: content-domain retrieval through registered source lanes.

Thin wrapper over sources.source_search — resolves the tenant owner and
records one usage row per call (hosted metering). The heavy lifting (lane
lookup, MCP adapter, provenance normalization, reason-on-empty) lives in
sources.py.
"""

from typing import Optional

from ..sources import source_search as _search
from ..sources import list_domains
from ..hosting import current_owner
from ..db import record_usage


def source_search(domain: str, query: str, limit: int = 10) -> dict:
    """Search one content domain (papers/gov/news/legal/philosophy/…) by query.

    Returns {domain, service, hits[], count, reason?}. Each hit carries the
    unified provenance triple (source/stable_id/as_of). An unknown domain
    raises with the available list; an empty result carries a reason — it is
    never a silent empty list.
    """
    owner = current_owner()
    result = _search(domain, query, limit)
    # Meter one call per (owner, domain); unit_type 'source' keeps it distinct
    # from generation units in the usage report.
    record_usage(
        owner=owner, tool="source_search", provider=result.get("service", domain),
        model=None, units=1, unit_type="source", credential_source="source-lane",
    )
    return result


def source_domains() -> dict:
    """List the content domains that currently have a registered source lane."""
    return {"domains": list_domains()}
