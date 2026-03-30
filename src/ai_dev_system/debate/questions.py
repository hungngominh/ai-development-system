# src/ai_dev_system/debate/questions.py
import json
from ai_dev_system.debate.report import Question
from ai_dev_system.debate.agents import VALID_AGENT_KEYS

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


def generate_questions(brief: dict, llm_client) -> list[Question]:
    """Single LLM call: brief → list[Question]."""
    response = llm_client.complete(
        system=SYSTEM_PROMPT,
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
        questions.append(Question(
            id=item["id"],
            text=item["text"],
            classification=item["classification"],
            domain=item.get("domain", "backend"),
            agent_a=agent_a,
            agent_b=agent_b,
        ))
    return questions
