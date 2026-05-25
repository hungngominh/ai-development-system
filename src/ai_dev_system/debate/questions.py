# src/ai_dev_system/debate/questions.py
import json
import warnings
from ai_dev_system.debate.report import Question
from ai_dev_system.debate.agents import VALID_AGENT_KEYS
from ai_dev_system.debate.domains import resolve_domain

SYSTEM_PROMPT = (
    "You are an analyst. Generate clarifying questions needed to write a complete technical spec "
    "for the given project brief. Return ONLY a JSON array. Each element: "
    '{"id": "Q1", "text": "...", "classification": "REQUIRED"|"STRATEGIC"|"OPTIONAL", '
    '"domain": "security"|"backend"|"product"|"database"|"qa", '
    '"agent_a": "<AgentKey>", "agent_b": "<AgentKey>"}. '
    "Valid agent keys: SecuritySpecialist, BackendArchitect, DevOpsSpecialist, "
    "ProductManager, DatabaseSpecialist, QAEngineer. "
    "REQUIRED = must answer to ship. STRATEGIC = important but has defaults. OPTIONAL = nice to have."
)

SYSTEM_PROMPT_BRIEF_V2 = (
    "You are an analyst. You will receive a structured project brief (v2) containing "
    "problem_statement, primary_user, scope_in, scope_out, success_metric, nfr_priority, "
    "constraints, known_unknowns, and other fields. Each field has a `source` marker — "
    "treat 'user' and 'ai_suggested_confirmed' as authoritative.\n\n"
    "Generate clarifying questions for decisions the AI CANNOT make alone. Focus on:\n"
    "  - explicit known_unknowns the user listed,\n"
    "  - tension between scope_in items and constraints,\n"
    "  - missing technical choices implied by nfr_priority (e.g. high security → auth model).\n"
    "Do NOT ask things already decided in the brief. Do NOT ask about scope_out items.\n\n"
    "If brief.assumptions is non-empty, EACH assumption SHOULD be covered by at least one "
    "REQUIRED question — surface the missing critical context.\n\n"
    "Return ONLY a JSON array. Each element: "
    '{"id": "Q1", "text": "...", "classification": "REQUIRED"|"STRATEGIC"|"OPTIONAL", '
    '"domain": "security"|"backend"|"product"|"database"|"qa", '
    '"agent_a": "<AgentKey>", "agent_b": "<AgentKey>"}. '
    "Valid agent keys: SecuritySpecialist, BackendArchitect, DevOpsSpecialist, "
    "ProductManager, DatabaseSpecialist, QAEngineer. "
    "REQUIRED = must answer to ship. STRATEGIC = important but has defaults. OPTIONAL = nice to have."
)


def generate_questions(brief: dict, llm_client) -> list[Question]:
    """Single LLM call: brief → list[Question].

    Detects intake brief v2 via `brief.brief_version == 2` and switches to the
    brief-aware system prompt; otherwise uses the legacy (v1 skeleton) prompt.
    The function signature is unchanged so existing callers keep working.
    """
    use_brief_v2 = brief.get("brief_version") == 2
    system = SYSTEM_PROMPT_BRIEF_V2 if use_brief_v2 else SYSTEM_PROMPT

    response = llm_client.complete(
        system=system,
        user=json.dumps(brief, ensure_ascii=False),
    )
    raw = json.loads(response)
    questions = []
    for item in raw:
        agent_a = item["agent_a"]
        agent_b = item["agent_b"]
        if agent_a not in VALID_AGENT_KEYS:
            agent_a = "BackendArchitect"
        if agent_b not in VALID_AGENT_KEYS:
            agent_b = "ProductManager"
        raw_domain = item.get("domain", "backend")
        canonical_domain, recognized = resolve_domain(raw_domain)
        if not recognized:
            warnings.warn(
                f"DOMAIN_UNRECOGNIZED: question {item['id']} emitted "
                f"domain={raw_domain!r}; defaulted to {canonical_domain!r}",
                stacklevel=2,
            )
        questions.append(Question(
            id=item["id"],
            text=item["text"],
            classification=item["classification"],
            domain=canonical_domain,
            agent_a=agent_a,
            agent_b=agent_b,
        ))
    return questions
