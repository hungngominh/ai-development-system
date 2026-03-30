import json
from typing import Protocol, Optional


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


ENRICHABLE_FIELDS = {"title", "objective", "description",
                     "done_definition", "verification_steps"}


def enrich_task(task: dict, spec_content: dict[str, str], llm: LLMClient) -> dict:
    prompt = _build_prompt(task, spec_content)
    try:
        response = llm.complete(prompt)
        enrichment = json.loads(response)
        if not isinstance(enrichment, dict):
            return task
    except (json.JSONDecodeError, Exception):
        task["llm_enriched"] = False
        return task

    for key in list(enrichment.keys()):
        if key not in ENRICHABLE_FIELDS:
            del enrichment[key]

    task.update(enrichment)
    task["llm_enriched"] = True
    task["enriched_by"] = "llm"
    return task


def enrich_all(graph: list[dict], spec_content: dict[str, str],
               llm: Optional[LLMClient] = None) -> list[dict]:
    if llm is None:
        for task in graph:
            task["llm_enriched"] = False
        return graph
    for task in graph:
        if task["execution_type"] == "atomic":
            enrich_task(task, spec_content, llm)
        else:
            task["llm_enriched"] = False
    return graph


def _build_prompt(task: dict, spec_content: dict[str, str]) -> str:
    problem = spec_content.get("problem.md", "")[:800]
    requirements = spec_content.get("requirements.md", "")[:800]
    constraints = spec_content.get("constraints.md", "")[:800]
    return f"""You are enriching a task in a software development execution plan.

## Task Context
- ID: {task['id']}
- Phase: {task['phase']}
- Type: {task['type']}
- Current title: {task['title']}
- Agent: {task['agent_type']}
- Inputs: {task['required_inputs']}
- Outputs: {task['expected_outputs']}

## Project Spec
### Problem
{problem}

### Requirements
{requirements}

### Constraints
{constraints}

## Instructions
Enrich this task with project-specific details. Return ONLY valid JSON:

```json
{{
  "title": "specific title mentioning the actual project",
  "objective": "1-2 sentences",
  "description": "detailed description with project-specific context",
  "done_definition": "measurable completion criteria",
  "verification_steps": ["step 1", "step 2", "step 3"]
}}
```

Rules:
- Be specific to THIS project
- done_definition must be measurable
- verification_steps must be actionable
- Do NOT add or reference tasks, dependencies, or execution structure"""
