"""Spec self-review critic — reviews an authored spec on the four superpowers
self-review dimensions (placeholder / internal-consistency / scope-decomposition /
ambiguity). Complementary to spec/grounding.py (traceability axis). Non-blocking:
any failure yields no findings and never breaks spec generation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_ENV = "AI_DEV_SPEC_SELF_REVIEW"
_DIMENSIONS = ("placeholder", "internal_consistency", "scope_decomposition", "ambiguity")
AUTO_REPAIR_DIMENSIONS = {"placeholder", "ambiguity"}


@dataclass
class Finding:
    section: str
    dimension: str
    severity: str
    message: str
    fix: str = ""


def self_review_enabled() -> bool:
    return os.environ.get(_ENV, "1").strip().lower() not in {"0", "false", "no", "off", ""}


_SYSTEM = (
    "You are a meticulous spec critic. Review the authored spec on EXACTLY these four "
    "dimensions: placeholder (TBD/TODO/'to be decided'/vague authored content), "
    "internal_consistency (contradictions across sections/facets), scope_decomposition "
    "(does this fit ONE implementation plan / one task graph, or must it be split? for a "
    "single task: is it truly ONE atomic task?), ambiguity (a requirement readable two ways). "
    "Do NOT check traceability or measurability — another tool does that. "
    'Return STRICT JSON: {"findings":[{"section":str,"dimension":str,"severity":"error"|"warning",'
    '"message":str,"fix":str}]}. section is the spec section name or "global" for cross-section. '
    "Empty findings list if the spec is clean. No prose outside the JSON."
)


def self_review(payload: dict, kind: str, llm_client) -> list[Finding]:
    if not self_review_enabled():
        return []
    try:
        user = (
            f"KIND: {kind}\n"
            f"DIMENSIONS: {', '.join(_DIMENSIONS)}\n"
            f"SPEC PAYLOAD (JSON):\n{json.dumps(payload, ensure_ascii=False)[:24000]}"
        )
        raw = llm_client.complete(system=_SYSTEM, user=user)
        data = json.loads(raw)
        out: list[Finding] = []
        for f in data.get("findings", []):
            try:
                out.append(Finding(
                    section=str(f["section"]), dimension=str(f["dimension"]),
                    severity=str(f.get("severity", "warning")),
                    message=str(f["message"]), fix=str(f.get("fix", "")),
                ))
            except (KeyError, TypeError):
                continue  # skip malformed individual findings
        return out
    except Exception:  # noqa: BLE001 - critic must never break spec generation
        return []
