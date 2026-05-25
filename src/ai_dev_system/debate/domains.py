"""Canonical 12-domain registry for Phase 1 v2.

Locked by Decision #23 (cap = 12) and Appendix A of
docs/superpowers/specs/2026-05-23-phase1-v2-locked-decisions.md.

Both M4 Question Gen (Decision.domain_hints) and M5 Debate (agent loader,
diversity guardrail) import from this module. Do not duplicate the list
elsewhere.
"""

from typing import Final

DOMAINS: Final[tuple[str, ...]] = (
    "backend",
    "frontend",
    "mobile",
    "data",
    "ml",
    "security",
    "infra",
    "devops",
    "qa",
    "product",
    "design",
    "legal",
)

DOMAIN_ALIASES: Final[dict[str, str]] = {
    "api": "backend",
    "server": "backend",
    "service": "backend",
    "microservice": "backend",
    "web": "frontend",
    "ui": "frontend",
    "client": "frontend",
    "spa": "frontend",
    "react": "frontend",
    "vue": "frontend",
    "ios": "mobile",
    "android": "mobile",
    "react-native": "mobile",
    "flutter": "mobile",
    "database": "data",
    "db": "data",
    "etl": "data",
    "analytics": "data",
    "warehouse": "data",
    "ai": "ml",
    "llm": "ml",
    "model": "ml",
    "ml-ops": "ml",
    "auth": "security",
    "authn": "security",
    "authz": "security",
    "crypto": "security",
    "compliance": "security",
    "cloud": "infra",
    "aws": "infra",
    "gcp": "infra",
    "azure": "infra",
    "k8s": "infra",
    "kubernetes": "infra",
    "ci": "devops",
    "cd": "devops",
    "monitoring": "devops",
    "sre": "devops",
    "observability": "devops",
    "testing": "qa",
    "test": "qa",
    "qa-automation": "qa",
    "performance": "qa",
    "pm": "product",
    "prd": "product",
    "mvp": "product",
    "roadmap": "product",
    "ux": "design",
    "ui-design": "design",
    "a11y": "design",
    "figma": "design",
    "privacy": "legal",
    "gdpr": "legal",
    "pdpa": "legal",
    "license": "legal",
    "compliance-legal": "legal",
}

DEFAULT_DOMAIN: Final[str] = "backend"


def resolve_domain(raw: str) -> tuple[str, bool]:
    """Resolve an LLM-emitted domain string to a canonical id.

    Returns (canonical_id, recognized). When `recognized` is False the caller
    should emit a `DOMAIN_UNRECOGNIZED` event carrying the original `raw`
    string for audit, then proceed with the returned canonical_id
    (DEFAULT_DOMAIN).
    """
    key = raw.strip().lower()
    if key in DOMAINS:
        return key, True
    if key in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[key], True
    return DEFAULT_DOMAIN, False
