"""
Example 02: OpenSpec -> CrewAI Pipeline
Doc spec file va tao CrewAI tasks tu cac requirements.

Script nay tu chua (self-contained) — khong can cai OpenSpec.
Su dung sample spec de minh hoa concept.
"""
import re
from pathlib import Path
from crewai import Agent, Task, Crew, Process

# ============================================================
# Sample spec (thay vi doc tu OpenSpec repo)
# ============================================================

SAMPLE_SPEC = """\
# Forum Chia Se Kien Thuc Noi Bo

## Muc tieu
Xay dung forum de nhan vien chia se kien thuc, kinh nghiem va tai lieu ky thuat.

## Requirements

### REQ-001: He thong dang bai
He thong MUST cho phep nguoi dung tao bai viet voi tieu de, noi dung (Markdown),
va gan tags phan loai. Bai viet MUST co trang thai draft/published.

### REQ-002: Binh luan va vote
He thong MUST ho tro binh luan (nested comments) va vote (upvote/downvote)
cho ca bai viet va binh luan. Vote MUST anh huong den contributor score.

### REQ-003: Bang xep hang contributors
He thong MUST hien thi leaderboard voi top contributors dua tren diem so.
Diem tinh tu: bai viet (+10), binh luan (+2), upvote nhan duoc (+1).

### REQ-004: Tim kiem noi dung
He thong MUST ho tro full-text search tren tieu de, noi dung bai viet,
va binh luan. Ket qua MUST duoc sap xep theo relevance.
"""

# ============================================================
# Duong dan den repo agency-agents
# ============================================================

AGENTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "agency-agents"


def load_agent_prompt(relative_path: str) -> str:
    """Doc file prompt agent tu repo agency-agents."""
    filepath = AGENTS_DIR / relative_path
    if not filepath.exists():
        print(f"⚠️ Khong tim thay: {filepath}")
        return "Ban la chuyen gia trong linh vuc duoc giao."
    return filepath.read_text(encoding="utf-8")


# ============================================================
# Parse requirements tu spec
# ============================================================

def parse_requirements(spec_text: str) -> list[dict]:
    """Trich xuat requirements tu spec markdown."""
    requirements = []
    # Tim cac section bat dau bang ### REQ-
    pattern = r"### (REQ-\d+): (.+?)\n(.+?)(?=\n### |\Z)"
    matches = re.findall(pattern, spec_text, re.DOTALL)
    for req_id, title, body in matches:
        requirements.append({
            "id": req_id,
            "title": title.strip(),
            "body": body.strip(),
        })
    return requirements


# ============================================================
# Tao CrewAI agents va tasks tu requirements
# ============================================================

def build_crew(requirements: list[dict]) -> Crew:
    """Tao Crew tu danh sach requirements."""

    # Tao agents voi backstory tu agency-agents
    pm_agent = Agent(
        role="Product Manager",
        goal="Phan tich va chi tiet hoa requirements thanh user stories",
        backstory=load_agent_prompt("product/product-manager.md"),
        verbose=True,
    )

    dev_agent = Agent(
        role="Backend Developer",
        goal="Thiet ke giai phap ky thuat cho tung requirement",
        backstory=load_agent_prompt("engineering/engineering-backend-architect.md"),
        verbose=True,
    )

    # Tao tasks tu tung requirement
    tasks = []
    for req in requirements:
        # Task 1: PM phan tich requirement
        analysis_task = Task(
            description=(
                f"Phan tich requirement {req['id']}: {req['title']}\n"
                f"Noi dung: {req['body']}\n\n"
                "Viet user stories (As a... I want... So that...) va acceptance criteria."
            ),
            agent=pm_agent,
            expected_output="User stories va acceptance criteria chi tiet",
        )

        # Task 2: Dev thiet ke giai phap
        design_task = Task(
            description=(
                f"Thiet ke giai phap ky thuat cho requirement {req['id']}: {req['title']}\n"
                "Bao gom: database tables, API endpoints, va logic chinh."
            ),
            agent=dev_agent,
            expected_output="Technical design voi schema, endpoints, va implementation notes",
            context=[analysis_task],  # Nhan ket qua phan tich tu PM
        )

        tasks.extend([analysis_task, design_task])

    crew = Crew(
        agents=[pm_agent, dev_agent],
        tasks=tasks,
        process=Process.sequential,  # Chay tuan tu de dam bao context
        verbose=True,
    )
    return crew


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("📋 OPENSPEC -> CREWAI PIPELINE")
    print("=" * 60)

    # Buoc 1: Parse spec
    print("\n📖 Dang parse spec...")
    requirements = parse_requirements(SAMPLE_SPEC)
    print(f"   Tim thay {len(requirements)} requirements:")
    for req in requirements:
        print(f"   - {req['id']}: {req['title']}")

    # Buoc 2: Tao crew
    print("\n🔨 Dang tao CrewAI crew...")
    crew = build_crew(requirements)
    print(f"   Tao {len(crew.tasks)} tasks cho {len(crew.agents)} agents")

    # Buoc 3: Chay crew
    print("\n🚀 Bat dau chay crew...")
    result = crew.kickoff()

    print("\n" + "=" * 60)
    print("📋 KET QUA CUOI CUNG:")
    print("=" * 60)
    print(result)
