# src/ai_dev_system/finalize_spec.py
import json
from pathlib import Path
from ai_dev_system.spec_bundle import SpecBundle

SYSTEM_PROMPT = (
    "You are a technical writer generating a structured spec from approved design decisions. "
    "Given approved_answers (question_id → answer), write a complete spec in 5 sections. "
    "Return ONLY a JSON object with these exact keys: "
    '"proposal", "design", "functional", "non_functional", "acceptance_criteria". '
    "Each value is a Markdown string. Write coherent prose — not template substitution."
)

_FILE_MAP = {
    "proposal": "proposal.md",
    "design": "design.md",
    "functional": "functional.md",
    "non_functional": "non-functional.md",
    "acceptance_criteria": "acceptance-criteria.md",
}


def finalize_spec(
    approved_answers: dict,
    run_id: str,
    llm_client,
    output_dir: Path,
) -> SpecBundle:
    """Single LLM call: approved_answers → 5-file SpecBundle."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    response = llm_client.complete(
        system=SYSTEM_PROMPT,
        user=json.dumps({"run_id": run_id, "approved_answers": approved_answers}, ensure_ascii=False),
    )

    try:
        sections = json.loads(response)
    except json.JSONDecodeError:
        sections = {k: f"# {k}\n\n{response}" for k in _FILE_MAP}

    files: dict[str, Path] = {}
    for key, filename in _FILE_MAP.items():
        content = sections.get(key, f"# {filename}\n\n(Not generated)")
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = path

    return SpecBundle(version=1, root_dir=output_dir, files=files)
