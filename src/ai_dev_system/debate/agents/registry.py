"""AgentRegistry — in-memory index over all dense agent .md files.

Spec M5 (D3). The registry loads every .md file under
`references/agency-agents/` exactly once at construction, builds
key→spec and domain→[specs] indexes, and exposes pair_suggestion()
which is the primary anti-echo-chamber lever for the debate engine.

Caching: the registry is meant to be constructed once at process
start (e.g. by `debate_pipeline`) and re-used. Re-instantiating is
safe but re-parses every file. There is no file-watch / hot reload —
prompt changes ship with code (locked decision #23).
"""

from collections import defaultdict
from pathlib import Path

from ai_dev_system.debate.agents.loader import (
    AGENCY_AGENTS_DIR,
    AgentSpec,
    parse_agent_md,
)


class AgentNotFoundError(KeyError):
    """Requested agent key is not in the registry."""


class PairSuggestionError(RuntimeError):
    """pair_suggestion exhausted all strategies without finding a
    counter-agent that satisfies the anti-echo guarantee (different
    domain than primary).
    """


class AgentRegistry:
    """Index over dense agent .md files.

    Construct with `AgentRegistry.from_directory(...)` (default) or
    `AgentRegistry.from_specs([...])` (tests).
    """

    def __init__(self, specs: list[AgentSpec]):
        self._by_key: dict[str, AgentSpec] = {}
        self._by_domain: dict[str, list[AgentSpec]] = defaultdict(list)
        for spec in specs:
            if spec.key in self._by_key:
                raise ValueError(
                    f"Duplicate agent key in registry: {spec.key!r}"
                )
            self._by_key[spec.key] = spec
            self._by_domain[spec.domain].append(spec)

    # ---- factories ----

    @classmethod
    def from_specs(cls, specs: list[AgentSpec]) -> "AgentRegistry":
        return cls(list(specs))

    @classmethod
    def from_directory(
        cls,
        agents_dir: Path | None = None,
    ) -> "AgentRegistry":
        """Scan a directory for *.md files, parse each, build registry.

        Files that fail to parse are skipped with their exception
        propagated (registry construction is fail-fast — a broken
        agent file should not silently degrade pairing decisions).
        """
        base = agents_dir or AGENCY_AGENTS_DIR
        specs: list[AgentSpec] = []
        for md_file in sorted(base.glob("*.md")):
            specs.append(
                parse_agent_md(md_file.read_text(encoding="utf-8"), file_path=md_file)
            )
        return cls(specs)

    # ---- lookups ----

    def get(self, key: str) -> AgentSpec:
        try:
            return self._by_key[key]
        except KeyError:
            raise AgentNotFoundError(key) from None

    def by_domain(self, domain: str) -> list[AgentSpec]:
        return list(self._by_domain.get(domain, ()))

    def list_all(self) -> list[AgentSpec]:
        return list(self._by_key.values())

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    # ---- pairing ----

    def pair_suggestion(
        self,
        primary: str,
        decision_domains: list[str],
    ) -> str:
        """Suggest a counterparty agent maximising lens diversity.

        Strategy chain (first match wins):
            1. typical_paired_with ∩ (decision_domains minus primary's domain)
               — strongest signal: spec author already grouped these
                 agents AND the decision spans the candidate's domain.
            2. typical_paired_with with a different domain than primary
               — fall back when no remaining hint matches a paired key,
                 still safer than echo.
            3. Any agent whose domain is in remaining decision_domains.
            4. Any agent with a different domain than primary
               (anti-echo last resort).

        Raises:
            AgentNotFoundError: `primary` not in registry.
            PairSuggestionError: no agent in any other domain exists.
        """
        primary_spec = self.get(primary)
        primary_domain = primary_spec.domain
        remaining_hints = [d for d in decision_domains if d != primary_domain]

        # 1. typical_paired_with ∩ remaining hints (by domain)
        for partner_key in primary_spec.typical_paired_with:
            partner = self._by_key.get(partner_key)
            if partner is None:
                continue
            if partner.domain in remaining_hints:
                return partner.key

        # 2. typical_paired_with with different domain than primary
        for partner_key in primary_spec.typical_paired_with:
            partner = self._by_key.get(partner_key)
            if partner is None:
                continue
            if partner.domain != primary_domain:
                return partner.key

        # 3. Any agent in remaining_hints domains
        for hint in remaining_hints:
            candidates = self.by_domain(hint)
            for candidate in candidates:
                if candidate.key != primary:
                    return candidate.key

        # 4. Any agent in a different domain (anti-echo last resort)
        for spec in self._by_key.values():
            if spec.domain != primary_domain:
                return spec.key

        raise PairSuggestionError(
            f"No counter-agent found for {primary!r} "
            f"(decision_domains={decision_domains!r}); "
            f"registry only contains agents in domain {primary_domain!r}"
        )
