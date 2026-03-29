import hashlib
from uuid import uuid4

SCOPE_TYPES = {"product", "feature", "experiment", "unknown"}
COMPLEXITY_HINTS = {"low", "medium", "high", "unknown"}


def normalize_idea(raw_text: str) -> dict:
    """Parse raw text into structured brief skeleton."""
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("raw_idea must be non-empty")
    return {
        "id": str(uuid4()),
        "version": 1,
        "raw_idea": stripped,
        "source_hash": hashlib.sha256(stripped.encode()).hexdigest(),
        "problem": "",
        "target_users": "",
        "goal": "",
        "constraints": {"hard": [], "soft": []},
        "assumptions": [],
        "scope": {"type": "unknown", "complexity_hint": "unknown"},
        "success_signals": [],
    }


def validate_brief(brief: dict) -> list[str]:
    """Validate brief against schema. Returns list of errors (empty = valid)."""
    errors = []
    if not brief.get("id"):
        errors.append("id is required")
    v = brief.get("version")
    if not isinstance(v, int) or v < 1:
        errors.append("version must be int >= 1")
    if not brief.get("raw_idea", "").strip():
        errors.append("raw_idea must be non-empty")
    if brief.get("scope", {}).get("type") not in SCOPE_TYPES:
        errors.append(f"scope.type must be one of {SCOPE_TYPES}")
    if brief.get("scope", {}).get("complexity_hint") not in COMPLEXITY_HINTS:
        errors.append(f"scope.complexity_hint must be one of {COMPLEXITY_HINTS}")
    # Strict: no extra keys (top-level)
    allowed = {"id", "version", "raw_idea", "source_hash", "problem", "target_users",
               "goal", "constraints", "assumptions", "scope", "success_signals"}
    extra = set(brief.keys()) - allowed
    if extra:
        errors.append(f"Extra keys not allowed: {extra}")
    # Strict: no extra keys (nested)
    constraints = brief.get("constraints", {})
    if isinstance(constraints, dict):
        extra_c = set(constraints.keys()) - {"hard", "soft"}
        if extra_c:
            errors.append(f"Extra keys in constraints: {extra_c}")
    scope = brief.get("scope", {})
    if isinstance(scope, dict):
        extra_s = set(scope.keys()) - {"type", "complexity_hint"}
        if extra_s:
            errors.append(f"Extra keys in scope: {extra_s}")
    return errors
