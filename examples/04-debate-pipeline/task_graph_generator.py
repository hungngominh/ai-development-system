"""
Task Graph Generator: Tự động tạo task graph từ OpenSpec specs.

CrewAI crew đọc spec và sinh ra danh sách tasks + dependencies,
thay vì con người phải tự tay chạy `bd create` và `bd dep add`.
"""
import json
import re
from crewai import Agent, Task, Crew, Process
from agent_pairing import load_agent_prompt


def create_task_generator_crew(spec_text: str, approved_answers: list[dict]) -> Crew:
    """Tạo CrewAI crew để sinh task graph từ spec.

    Args:
        spec_text: Nội dung spec (từ OpenSpec hoặc từ debate results)
        approved_answers: Các đáp án đã được con người duyệt từ Gate 1

    Returns:
        CrewAI Crew sẵn sàng kickoff
    """
    answers_context = "\n".join(
        f"- {a['question']}: {a.get('final_answer', a.get('human_answer', ''))}"
        for a in approved_answers
    )

    # Agent 1: Project Planner — phân tích spec, xác định tasks
    planner = Agent(
        role="Project Planner",
        goal="Phân tích spec và tạo danh sách task cụ thể, có thể thực thi được",
        backstory=load_agent_prompt("product/product-manager.md"),
        verbose=True,
    )

    # Agent 2: Technical Architect — xác định dependencies
    architect = Agent(
        role="Technical Architect",
        goal="Xác định thứ tự và dependency giữa các task, đảm bảo logic",
        backstory=load_agent_prompt("engineering/engineering-backend-architect.md"),
        verbose=True,
    )

    # Task 1: Planner tạo danh sách tasks
    task_identify = Task(
        description=(
            f"Dựa trên spec và các quyết định đã duyệt, hãy tạo danh sách tasks.\n\n"
            f"SPEC:\n{spec_text}\n\n"
            f"QUYẾT ĐỊNH ĐÃ DUYỆT:\n{answers_context}\n\n"
            f"Yêu cầu:\n"
            f"- Mỗi task phải cụ thể, có thể giao cho 1 developer\n"
            f"- Mỗi task có tiêu đề ngắn gọn và mô tả chi tiết\n"
            f"- Gán priority: high (core), medium (important), low (nice-to-have)\n"
            f"- Chia nhỏ đủ để theo dõi, không quá lớn (1-3 ngày/task)\n\n"
            f"Trả về danh sách dạng:\n"
            f"TASK-1: [title] (priority: high/medium/low)\n"
            f"  Mô tả: ...\n"
            f"TASK-2: ...\n"
        ),
        agent=planner,
        expected_output="Danh sách tasks với title, priority, và mô tả cho từng task",
    )

    # Task 2: Architect xác định dependencies và output JSON
    task_dependencies = Task(
        description=(
            f"Dựa trên danh sách tasks từ Project Planner, hãy:\n\n"
            f"1. Xác định dependency: task nào phải xong trước task nào\n"
            f"2. Xác định task nào có thể chạy song song\n"
            f"3. Đảm bảo không có circular dependency\n\n"
            f"Trả về KẾT QUẢ dạng JSON array:\n"
            f'[\n'
            f'  {{"id": "TASK-1", "title": "...", "priority": "high", '
            f'"deps": [], "status": "ready"}},\n'
            f'  {{"id": "TASK-2", "title": "...", "priority": "high", '
            f'"deps": ["TASK-1"], "status": "blocked"}},\n'
            f'  ...\n'
            f']\n\n'
            f"Quy tắc status:\n"
            f"- ready: không có dependency hoặc tất cả dependency đã hoàn thành\n"
            f"- blocked: còn dependency chưa hoàn thành\n\n"
            f"CHỈ TRẢ VỀ JSON ARRAY, KHÔNG THÊM TEXT KHÁC."
        ),
        agent=architect,
        expected_output="JSON array chứa tất cả tasks với dependencies",
        context=[task_identify],
    )

    return Crew(
        agents=[planner, architect],
        tasks=[task_identify, task_dependencies],
        process=Process.sequential,
        verbose=True,
    )


def parse_task_graph(crew_output) -> list[dict]:
    """Parse output của Task Generator Crew thành list tasks.

    Args:
        crew_output: Output từ crew.kickoff()

    Returns:
        List tasks, mỗi task có: id, title, priority, deps, status
    """
    raw = str(crew_output)

    # Tìm JSON array trong output
    json_match = re.search(r'\[[\s\S]*\]', raw)
    if json_match:
        try:
            tasks = json.loads(json_match.group())
            normalized = []
            for t in tasks:
                normalized.append({
                    "id": t.get("id", f"TASK-{len(normalized)+1}"),
                    "title": t.get("title", "Untitled"),
                    "priority": t.get("priority", "medium"),
                    "deps": t.get("deps", []),
                    "status": t.get("status", "ready" if not t.get("deps") else "blocked"),
                })
            return normalized
        except json.JSONDecodeError:
            pass

    return _parse_text_format(raw)


def _parse_text_format(text: str) -> list[dict]:
    """Fallback parser khi JSON không parse được."""
    tasks = []
    pattern = r'TASK-(\d+):\s*(.+?)(?:\(priority:\s*(high|medium|low)\))?'
    matches = re.findall(pattern, text, re.IGNORECASE)

    for num, title, priority in matches:
        tasks.append({
            "id": f"TASK-{num}",
            "title": title.strip(),
            "priority": priority.lower() if priority else "medium",
            "deps": [],
            "status": "ready",
        })

    # Cố gắng tìm dependencies từ text
    dep_pattern = r'(TASK-\d+)\s*(?:->|-->|depends on|blocked by|phụ thuộc)\s*(TASK-\d+)'
    dep_matches = re.findall(dep_pattern, text, re.IGNORECASE)
    task_ids = {t["id"] for t in tasks}

    for child, parent in dep_matches:
        if child in task_ids and parent in task_ids:
            task = next(t for t in tasks if t["id"] == child)
            if parent not in task["deps"]:
                task["deps"].append(parent)
                task["status"] = "blocked"

    return tasks


def generate_task_graph(spec_text: str, approved_answers: list[dict]) -> list[dict]:
    """Tiện ích: chạy crew và trả về task graph đã parse.

    Args:
        spec_text: Nội dung spec
        approved_answers: Đáp án đã duyệt từ Gate 1

    Returns:
        List tasks sẵn sàng truyền vào approval_gate_task_graph
    """
    crew = create_task_generator_crew(spec_text, approved_answers)
    output = crew.kickoff()
    return parse_task_graph(output)


def generate_beads_commands(tasks: list[dict]) -> list[str]:
    """Sinh ra các lệnh Beads CLI từ task graph đã duyệt.

    Returns:
        List các lệnh `bd create` và `bd dep add` để chạy trong terminal
    """
    commands = []

    for t in tasks:
        cmd = f'bd create "{t["title"]}" --type task --priority {t["priority"]}'
        commands.append(cmd)

    for t in tasks:
        for dep in t["deps"]:
            cmd = f'bd dep add {dep} blocks {t["id"]}'
            commands.append(cmd)

    return commands


# ============================================================
# Demo / Test
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("TASK GRAPH GENERATOR — DEMO")
    print("=" * 60)
    print()

    sample_spec = """
    Forum chia sẻ kiến thức nội bộ công ty.
    - Hệ thống MUST cho phép đăng bài với tiêu đề và nội dung
    - Hệ thống MUST hỗ trợ bình luận và vote
    - Hệ thống MUST hiển thị bảng xếp hạng top contributors
    """

    sample_answers = [
        {"question": "Frontend framework?", "final_answer": "React + TypeScript"},
        {"question": "Database?", "final_answer": "PostgreSQL"},
        {"question": "Authentication?", "final_answer": "OAuth2 + JWT"},
    ]

    # Demo: tạo crew (không kickoff)
    crew = create_task_generator_crew(sample_spec, sample_answers)
    print(f"Agents: {[a.role for a in crew.agents]}")
    print(f"Tasks: {len(crew.tasks)}")
    print()

    # Demo: simulate output và parse
    mock_output = """
    [
        {"id": "TASK-1", "title": "Thiết kế PostgreSQL schema", "priority": "high", "deps": [], "status": "ready"},
        {"id": "TASK-2", "title": "Setup OAuth2 + JWT authentication", "priority": "high", "deps": ["TASK-1"], "status": "blocked"},
        {"id": "TASK-3", "title": "API endpoints (CRUD posts, comments, votes)", "priority": "high", "deps": ["TASK-1", "TASK-2"], "status": "blocked"},
        {"id": "TASK-4", "title": "React frontend + TypeScript setup", "priority": "medium", "deps": ["TASK-3"], "status": "blocked"},
        {"id": "TASK-5", "title": "Leaderboard component", "priority": "medium", "deps": ["TASK-3", "TASK-4"], "status": "blocked"}
    ]
    """

    tasks = parse_task_graph(mock_output)
    print("Parsed tasks:")
    for t in tasks:
        deps_str = " -> ".join(t["deps"]) if t["deps"] else "(none)"
        print(f"  {t['id']}: {t['title']} [{t['priority']}] deps: {deps_str}")

    print()
    print("Beads commands:")
    for cmd in generate_beads_commands(tasks):
        print(f"  $ {cmd}")
