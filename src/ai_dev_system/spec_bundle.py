from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = ["problem.md", "requirements.md", "constraints.md",
                  "success_criteria.md", "assumptions.md"]

@dataclass
class SpecBundle:
    version: int
    root_dir: Path
    files: dict[str, Path]

def generate_spec_bundle(approved_brief: dict, output_dir: Path) -> SpecBundle:
    """Write 5 spec files from approved brief. Returns SpecBundle."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    files["problem.md"] = _write_problem(approved_brief, output_dir)
    files["requirements.md"] = _write_requirements(approved_brief, output_dir)
    files["constraints.md"] = _write_constraints(approved_brief, output_dir)
    files["success_criteria.md"] = _write_success_criteria(approved_brief, output_dir)
    files["assumptions.md"] = _write_assumptions(approved_brief, output_dir)
    return SpecBundle(version=1, root_dir=output_dir, files=files)

def validate_spec_bundle(spec_dir: Path) -> list[str]:
    warnings = []
    for filename in REQUIRED_FILES:
        path = spec_dir / filename
        if not path.exists():
            warnings.append(f"Missing: {filename}")
        elif path.stat().st_size == 0:
            warnings.append(f"Empty: {filename}")
    return warnings

def _or_placeholder(value: str) -> str:
    return value if value else "(not specified — to be refined)"

def _write_problem(brief: dict, out: Path) -> Path:
    p = out / "problem.md"
    p.write_text(
        f"# Problem Statement\n\n"
        f"## Raw Idea (Original Input — Do Not Interpret)\n{brief['raw_idea']}\n\n"
        f"## Problem\n{_or_placeholder(brief['problem'])}\n\n"
        f"## Target Users\n{_or_placeholder(brief['target_users'])}\n"
    )
    return p

def _write_requirements(brief: dict, out: Path) -> Path:
    p = out / "requirements.md"
    scope = brief.get("scope", {})
    p.write_text(
        f"# Requirements\n\n"
        f"## Problem Alignment\nThis goal addresses the problem described in problem.md.\n\n"
        f"## Goal\n{_or_placeholder(brief['goal'])}\n\n"
        f"---\n\n"
        f"## Scope Definition (Execution Context)\n"
        f"- Type: {scope.get('type', 'unknown')}\n"
        f"- Complexity: {scope.get('complexity_hint', 'unknown')}\n"
    )
    return p

def _write_constraints(brief: dict, out: Path) -> Path:
    p = out / "constraints.md"
    hard = brief.get("constraints", {}).get("hard", [])
    soft = brief.get("constraints", {}).get("soft", [])
    hard_text = "\n".join(f"- [HARD] {c}" for c in hard) if hard else "(none specified)"
    soft_text = "\n".join(f"- [SOFT] {c}" for c in soft) if soft else "(none specified)"
    p.write_text(
        f"# Constraints\n\n"
        f"## Hard Constraints (MUST satisfy)\n{hard_text}\n\n"
        f"## Soft Constraints (SHOULD satisfy, tradeable)\n{soft_text}\n"
    )
    return p

def _write_success_criteria(brief: dict, out: Path) -> Path:
    p = out / "success_criteria.md"
    signals = brief.get("success_signals", [])
    if signals:
        items = "\n".join(
            f"- [ ] {s}\n  - Metric: (to be defined)\n  - Target: (to be defined)"
            for s in signals
        )
    else:
        items = "(no signals defined — verification will use goal as proxy)"
    p.write_text(f"# Success Criteria\n\n{items}\n")
    return p

def _write_assumptions(brief: dict, out: Path) -> Path:
    p = out / "assumptions.md"
    assumptions = brief.get("assumptions", [])
    if assumptions:
        items = "\n".join(f"- {a}" for a in assumptions)
    else:
        items = "(no assumptions recorded)"
    p.write_text(
        f"# Assumptions\n\n{items}\n\n"
        f"> These assumptions have not been validated.\n"
        f"> Debate crew may challenge these. Task execution should flag if an assumption proves false.\n"
    )
    return p
