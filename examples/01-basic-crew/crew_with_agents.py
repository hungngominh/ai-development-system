"""
Example 01: Ket hop CrewAI voi agency-agents
Tao 2 agents su dung prompt chuyen biet tu agency-agents lam backstory.
"""
from pathlib import Path
from crewai import Agent, Task, Crew, Process

# Duong dan den repo agency-agents (cung workspace)
AGENTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "agency-agents"


def load_agent_prompt(relative_path: str) -> str:
    """Doc file prompt agent tu repo agency-agents."""
    filepath = AGENTS_DIR / relative_path
    if not filepath.exists():
        print(f"⚠️ Khong tim thay: {filepath}")
        print(f"   Hay clone agency-agents vao cung thu muc workspace.")
        return f"Ban la chuyen gia trong linh vuc duoc giao."
    return filepath.read_text(encoding="utf-8")


# --- Tao Agents ---

# Agent 1: Product Manager — viet PRD
product_manager = Agent(
    role="Product Manager",
    goal="Viet PRD cho forum chia se kien thuc noi bo cong ty",
    backstory=load_agent_prompt("product/product-manager.md"),
    verbose=True,
)

# Agent 2: Backend Architect — thiet ke database
backend_architect = Agent(
    role="Backend Architect",
    goal="Thiet ke database schema va API endpoints cho forum",
    backstory=load_agent_prompt("engineering/engineering-backend-architect.md"),
    verbose=True,
)

# --- Tao Tasks ---

task_prd = Task(
    description=(
        "Viet Product Requirements Document (PRD) cho forum chia se kien thuc noi bo.\n"
        "Yeu cau chinh:\n"
        "- Dang bai, binh luan, vote\n"
        "- Bang xep hang vinh danh top contributors\n"
        "- Phan loai bai viet theo categories\n"
        "- Tim kiem noi dung\n"
    ),
    agent=product_manager,
    expected_output="PRD document voi user stories, features, va acceptance criteria",
)

task_db = Task(
    description=(
        "Dua tren PRD, thiet ke database schema (PostgreSQL) va REST API endpoints.\n"
        "Bao gom: users, posts, comments, votes, categories, contributor scores.\n"
    ),
    agent=backend_architect,
    expected_output="SQL schema + API endpoint list voi HTTP methods va response format",
    context=[task_prd],  # Nhan output tu task PRD
)

# --- Tao Crew va chay ---

crew = Crew(
    agents=[product_manager, backend_architect],
    tasks=[task_prd, task_db],
    process=Process.sequential,  # Chay tuan tu: PRD truoc, DB sau
    verbose=True,
)

if __name__ == "__main__":
    print("🚀 Bat dau chay crew...")
    print(f"📁 Agency agents dir: {AGENTS_DIR}")
    print()
    result = crew.kickoff()
    print("\n" + "=" * 60)
    print("📋 KET QUA CUOI CUNG:")
    print("=" * 60)
    print(result)
