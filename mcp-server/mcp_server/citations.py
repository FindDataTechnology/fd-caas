"""
Serious-text citation gate (openspec: strengthen-caas-serious-text /
serious-text-citation).

Two responsibilities, both pure/deterministic here (the model-backed 2nd/3rd
level verification is delegated to the existing fd-shilun verify engine, not
re-implemented — spec "核验复用既有引擎不另造"):

  resolve_anchors   — [[cite:<id>]] 逐个对照 citations；解不出即从最终成稿
                      截除（宁删勿假），不改用户源稿。
  adjudicate        — 把每条引用的核验结论折成「拦下 / 待核 / 通过」三态，
                      确定不通过者不得进入成稿；无法确定者标「待核」而非静默删。

The marker format and the truncation semantics match fd-shilun's
writing.resolve_citations exactly, so the same draft means the same thing on
both sides.
"""

import re
from typing import Iterable, Optional

# Same marker as fd-shilun (writing.py::CITE_MARKER_RE). Keep them identical:
# a divergent marker here would silently drop legitimate citations.
CITE_MARKER_RE = re.compile(r"\[\[cite:([^\]]+)\]\]")

# Statuses that MUST NOT appear in the final draft (spec: 确定不通过即拦截).
BLOCKING_STATUSES = frozenset({"blocked"})


def resolve_anchors(text: str, valid_ids: Iterable[str]) -> dict:
    """Validate [[cite:<id>]] markers against known citation ids; strip the rest.

    Mirrors fd-shilun resolve_citations: unknown markers are removed from the
    returned cleaned_text (宁删勿假), the caller's source text is untouched.
    """
    valid_set = set(valid_ids)
    found = CITE_MARKER_RE.findall(text or "")
    valid, stripped = [], []
    for cid in dict.fromkeys(c.strip() for c in found):
        (valid if cid in valid_set else stripped).append(cid)

    def _keep(m: re.Match) -> str:
        return m.group(0) if m.group(1).strip() in valid_set else ""

    cleaned = CITE_MARKER_RE.sub(_keep, text or "")
    return {
        "marker_count": len(found),
        "valid": valid,
        "stripped": stripped,
        "cleaned_text": cleaned,
    }


def adjudicate(citations: Iterable[dict]) -> dict:
    """Fold per-citation verification into the three-way disposition.

    Each citation is a dict with at least `id` and `status`. Returns:
      blocked  — ids that must be removed from the final draft
      pending  — ids that stay but are marked 待核 (not counted as passed)
      passed   — ids verified through (resolvable/authentic/supported)
    A `blocked` status wins; a `pending` status is kept-and-flagged. Anything
    else (resolvable/authentic/supported) counts as passed.
    """
    blocked, pending, passed = [], [], []
    for c in citations:
        cid = c.get("id")
        status = c.get("status")
        if status in BLOCKING_STATUSES:
            blocked.append(cid)
        elif status == "pending":
            pending.append(cid)
        else:
            passed.append(cid)
    return {"blocked": blocked, "pending": pending, "passed": passed,
            "all_resolved": not blocked and not pending}


def gate_draft(text: str, citations: Iterable[dict]) -> dict:
    """Apply both steps: strip blocked citations' anchors, flag the rest.

    1. Citations with status=blocked contribute to the strip set — their
       anchors disappear from the final draft.
    2. resolve_anchors also strips any anchor whose id is not a known citation
       (解不出即截断).
    3. pending citations stay in the draft (待核), NOT counted as passed.
    """
    cit_list = list(citations)
    known_ids = {c.get("id") for c in cit_list if c.get("id")}
    disposition = adjudicate(cit_list)
    # Strip = blocked ids ∪ unknown ids (both resolved by resolve_anchors).
    valid_for_draft = known_ids - set(disposition["blocked"])
    resolved = resolve_anchors(text, valid_for_draft)
    return {
        "cleaned_text": resolved["cleaned_text"],
        "stripped": resolved["stripped"],
        "pending": disposition["pending"],
        "passed": disposition["passed"],
        "blocked": disposition["blocked"],
    }
