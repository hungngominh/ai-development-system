"""Debate agent prompts package.

Phase 1 v2 introduces dense .md-based agent prompts loaded from
`references/agency-agents/` (locked decision #23). The legacy 3-line
prompts remain in `.legacy` and are re-exported here so existing
callers (rounds.py, materializer.py, questions/legacy.py, tests)
keep working unchanged.

Public surface:

- Legacy (v1):
    AGENT_PROMPTS, MODERATOR_PROMPT, VALID_AGENT_KEYS
- v2 loader (M5.A):
    AgentSpec, AgentLoadError, parse_agent_md, load_agent_prompt,
    snake_case
"""

from ai_dev_system.debate.agents.legacy import (
    AGENT_PROMPTS,
    MODERATOR_PROMPT,
    VALID_AGENT_KEYS,
)
from ai_dev_system.debate.agents.loader import (
    AGENCY_AGENTS_DIR,
    AgentLoadError,
    AgentSpec,
    load_agent_prompt,
    parse_agent_md,
    snake_case,
)

__all__ = [
    "AGENT_PROMPTS",
    "MODERATOR_PROMPT",
    "VALID_AGENT_KEYS",
    "AGENCY_AGENTS_DIR",
    "AgentLoadError",
    "AgentSpec",
    "load_agent_prompt",
    "parse_agent_md",
    "snake_case",
]
