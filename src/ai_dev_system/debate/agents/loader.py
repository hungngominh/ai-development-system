"""Agent prompt loader — parses dense agent .md files from
`references/agency-agents/<snake_case>.md`.

Per locked decision #23 the agent prompts live in-repo and are
versioned with code. Per spec M5 (D1, D2) each file uses YAML
frontmatter + markdown body:

    ---
    agent_key: SecuritySpecialist
    domain: security
    version: 1
    aliases: [sec, compliance, security_engineer]
    debate_role: critic_first
    typical_paired_with: [BackendArchitect, ProductManager]
    ---

    # Identity
    ...
    # Mission
    ...

If a file is missing, the loader falls back to the legacy 3-line
prompt from `debate.agents.legacy.AGENT_PROMPTS`, warns loudly, and
flags the result via `AgentSpec.is_fallback = True` so callers can
surface degraded mode to telemetry.
"""

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ai_dev_system.debate.agents.legacy import AGENT_PROMPTS

AGENCY_AGENTS_DIR = (
    Path(__file__).resolve().parents[4]
    / "references"
    / "agency-agents"
)

VALID_DEBATE_ROLES = {"critic_first", "advocate_first", "neutral"}


@dataclass
class AgentSpec:
    """Parsed agent prompt + metadata.

    `system_prompt` is the body markdown (everything after the
    frontmatter), used directly as the LLM system message.
    """

    key: str
    domain: str
    version: int
    aliases: list[str] = field(default_factory=list)
    debate_role: str = "neutral"
    typical_paired_with: list[str] = field(default_factory=list)
    system_prompt: str = ""
    file_path: Path | None = None
    is_fallback: bool = False


class AgentLoadError(RuntimeError):
    """Malformed agent .md file (frontmatter missing required fields,
    invalid YAML, unknown debate_role, etc).
    """


def snake_case(name: str) -> str:
    """Convert CamelCase / PascalCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def parse_agent_md(text: str, *, file_path: Path | None = None) -> AgentSpec:
    """Parse a single agent .md file into an AgentSpec.

    Raises:
        AgentLoadError: missing frontmatter, malformed YAML, missing
            required keys, or invalid debate_role.
    """
    if not text.startswith("---\n"):
        raise AgentLoadError(
            f"Agent file{(' ' + str(file_path)) if file_path else ''} "
            f"must start with YAML frontmatter ('---' on its own line)"
        )
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise AgentLoadError(
            f"Agent file{(' ' + str(file_path)) if file_path else ''} "
            f"frontmatter missing closing '---' marker"
        )

    frontmatter_text = parts[0][4:]  # strip leading "---\n"
    body = parts[1].lstrip("\n")

    try:
        meta = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as e:
        raise AgentLoadError(f"Frontmatter YAML invalid: {e}") from e
    if not isinstance(meta, dict):
        raise AgentLoadError(
            f"Frontmatter must be a YAML mapping, got {type(meta).__name__}"
        )

    for required in ("agent_key", "domain", "version"):
        if required not in meta:
            raise AgentLoadError(f"Frontmatter missing required key {required!r}")

    debate_role = str(meta.get("debate_role", "neutral"))
    if debate_role not in VALID_DEBATE_ROLES:
        raise AgentLoadError(
            f"Invalid debate_role={debate_role!r}; "
            f"expected one of {sorted(VALID_DEBATE_ROLES)}"
        )

    return AgentSpec(
        key=str(meta["agent_key"]),
        domain=str(meta["domain"]),
        version=int(meta["version"]),
        aliases=[str(a) for a in (meta.get("aliases") or [])],
        debate_role=debate_role,
        typical_paired_with=[str(p) for p in (meta.get("typical_paired_with") or [])],
        system_prompt=body.strip(),
        file_path=file_path,
        is_fallback=False,
    )


def _fallback_spec(agent_key: str) -> AgentSpec:
    """Build a degraded AgentSpec from the legacy 3-line prompt.

    The legacy prompts predate the canonical 12-domain registry; we
    default domain to "backend" and debate_role to "neutral" so the
    caller can still route through pair_suggestion. is_fallback=True
    lets telemetry surface the degraded prompt.
    """
    legacy_prompt = AGENT_PROMPTS.get(
        agent_key,
        "You are an analyst. Argue your position concisely.",
    )
    return AgentSpec(
        key=agent_key,
        domain="backend",
        version=0,
        aliases=[],
        debate_role="neutral",
        typical_paired_with=[],
        system_prompt=legacy_prompt,
        file_path=None,
        is_fallback=True,
    )


def load_agent_prompt(
    agent_key: str,
    *,
    agents_dir: Path | None = None,
) -> AgentSpec:
    """Load one agent .md file by key; fall back to legacy on miss.

    Args:
        agent_key: e.g. "SecuritySpecialist".
        agents_dir: override directory (used by tests). Defaults to
            `references/agency-agents/` at repo root.

    Returns:
        AgentSpec. `is_fallback` is True when the .md file is missing
        and the legacy prompt is in use.
    """
    base = agents_dir or AGENCY_AGENTS_DIR
    path = base / f"{snake_case(agent_key)}.md"
    if not path.exists():
        warnings.warn(
            f"Agent prompt file missing for {agent_key!r} "
            f"(expected at {path}); using legacy fallback",
            stacklevel=2,
        )
        return _fallback_spec(agent_key)
    return parse_agent_md(path.read_text(encoding="utf-8"), file_path=path)
