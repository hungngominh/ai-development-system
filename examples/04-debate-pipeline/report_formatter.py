"""
Report Formatter: Định dạng kết quả debate thành markdown report cho con người duyệt.

Report được sắp xếp theo status:
- FORCED items hiện trước (bắt buộc con người quyết định)
- CONSENSUS items sau (chỉ cần confirm nhanh)
"""
from datetime import datetime


def format_debate_report(project_name: str, debate_results: list[dict]) -> str:
    """Tạo markdown report từ kết quả debate.

    Args:
        project_name: Tên dự án
        debate_results: List các kết quả debate, mỗi item có dạng:
            {
                "question": str,
                "domain": str,
                "agent_a_role": str,
                "agent_b_role": str,
                "agent_a_final_position": str,
                "agent_b_final_position": str,
                "common_ground": str,
                "remaining_disagreements": str,
                "final_answer": str,
                "rounds": int,
                "status": "CONSENSUS" | "FORCED",
            }
    """
    forced = [r for r in debate_results if r.get("status") == "FORCED"]
    consensus = [r for r in debate_results if r.get("status") == "CONSENSUS"]

    total = len(debate_results)

    lines = []
    lines.append("# Debate Report")
    lines.append("")
    lines.append(f"- **Dự án:** {project_name}")
    lines.append(f"- **Thời gian:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- **Tổng câu hỏi:** {total}")
    lines.append(f"- **Đồng thuận (CONSENSUS):** {len(consensus)}")
    lines.append(f"- **Cần quyết định (FORCED):** {len(forced)}")
    lines.append("")
    lines.append("---")

    # === FORCED — bắt buộc con người quyết định ===
    if forced:
        lines.append("")
        lines.append("## Cần bạn quyết định (FORCED)")
        lines.append("")
        lines.append("> Các câu hỏi này 2 agent đã tranh luận hết 5 vòng nhưng vẫn bất đồng.")
        lines.append("> Moderator đã chốt phương án hợp lý nhất, nhưng **bạn bắt buộc phải review và quyết định**.")
        lines.append("")
        for i, r in enumerate(forced, 1):
            lines.extend(_format_forced_item(r, i))

    # === CONSENSUS — confirm nhanh ===
    if consensus:
        lines.append("")
        lines.append("## Đã đồng thuận — confirm nhanh (CONSENSUS)")
        lines.append("")
        lines.append("> Các câu hỏi này 2 agent đã tự đồng ý 100%. Bạn chỉ cần xác nhận hoặc override.")
        lines.append("")
        for i, r in enumerate(consensus, 1):
            lines.extend(_format_consensus_item(r, i))

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Hướng dẫn")
    lines.append("")
    lines.append("- **FORCED:** Bạn **BẮT BUỘC** phải chọn hoặc đưa ra ý kiến riêng")
    lines.append("- **CONSENSUS:** 2 agent đã đồng ý, bạn chỉ cần confirm hoặc override")

    return "\n".join(lines)


def _format_forced_item(r: dict, index: int) -> list[str]:
    """Format một item FORCED — hiển thị đầy đủ 2 quan điểm cuối và điểm bất đồng."""
    lines = []
    rounds = r.get("rounds", "?")
    lines.append(f"### F{index}: \"{r['question']}\" [FORCED — {rounds} vòng]")
    lines.append("")
    lines.append(f"| | {r['agent_a_role']} | {r['agent_b_role']} |")
    lines.append("|---|---|---|")
    lines.append(f"| **Quan điểm cuối** | {r.get('agent_a_final_position', '')} | {r.get('agent_b_final_position', '')} |")
    lines.append("")

    if r.get("common_ground"):
        lines.append(f"**Điểm đã đồng thuận:** {r['common_ground']}")
        lines.append("")

    if r.get("remaining_disagreements"):
        lines.append(f"**Điểm bất đồng còn lại:** {r['remaining_disagreements']}")
        lines.append("")

    lines.append(f"**Moderator chốt:** {r.get('final_answer', '')}")
    lines.append("")
    lines.append("**HÀNH ĐỘNG CỦA BẠN:**")
    lines.append(f"- [ ] Đồng ý moderator: {r.get('final_answer', '')}")
    lines.append(f"- [ ] Chọn {r['agent_a_role']}: {r.get('agent_a_final_position', '')}")
    lines.append(f"- [ ] Chọn {r['agent_b_role']}: {r.get('agent_b_final_position', '')}")
    lines.append("- [ ] Ý kiến khác: ___")
    lines.append("")
    return lines


def _format_consensus_item(r: dict, index: int) -> list[str]:
    """Format một item CONSENSUS — hiển thị kết quả ngắn gọn."""
    lines = []
    rounds = r.get("rounds", "?")
    lines.append(f"### C{index}: \"{r['question']}\" [CONSENSUS — {rounds} vòng]")
    lines.append("")
    lines.append(f"**Đáp án:** {r.get('final_answer', '')}")
    lines.append("")
    if r.get("common_ground"):
        lines.append(f"*Lý do đồng thuận: {r['common_ground']}*")
        lines.append("")
    lines.append("- [ ] OK")
    lines.append("- [ ] Override: ___")
    lines.append("")
    return lines


def format_task_graph_report(project_name: str, tasks: list[dict]) -> str:
    """Tạo markdown report từ task graph cho con người duyệt.

    Args:
        project_name: Tên dự án
        tasks: List các task, mỗi item có dạng:
            {
                "id": str,
                "title": str,
                "priority": "high" | "medium" | "low",
                "deps": list[str],
                "status": "ready" | "blocked",
            }
    """
    ready = [t for t in tasks if t["status"] == "ready"]
    blocked = [t for t in tasks if t["status"] == "blocked"]

    lines = []
    lines.append("# Task Graph Report")
    lines.append("")
    lines.append(f"- **Dự án:** {project_name}")
    lines.append(f"- **Thời gian:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- **Tổng tasks:** {len(tasks)} ({len(ready)} ready, {len(blocked)} blocked)")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Danh sách Tasks")
    lines.append("")
    lines.append("| ID | Task | Priority | Dependencies | Status |")
    lines.append("|---|---|---|---|---|")

    for t in tasks:
        deps_str = ", ".join(t["deps"]) if t["deps"] else "—"
        status_str = "✅ Ready" if t["status"] == "ready" else "⏳ Blocked"
        lines.append(f"| {t['id']} | {t['title']} | {t['priority']} | {deps_str} | {status_str} |")

    lines.append("")

    lines.append("## Dependency Graph")
    lines.append("")
    lines.append("```")
    for t in tasks:
        if not t["deps"]:
            lines.append(f"  {t['id']}: {t['title']} (start)")
        else:
            for dep in t["deps"]:
                lines.append(f"  {dep} --> {t['id']}")
    lines.append("```")
    lines.append("")

    lines.append("## HÀNH ĐỘNG CỦA BẠN")
    lines.append("")
    lines.append("- [ ] Approve task graph như trên")
    lines.append("- [ ] Thêm task: ___")
    lines.append("- [ ] Xóa task: ___")
    lines.append("- [ ] Sửa dependency: ___")
    lines.append("- [ ] Reject (yêu cầu tạo lại)")

    return "\n".join(lines)


# ============================================================
# Demo / Test
# ============================================================

if __name__ == "__main__":
    mock_results = [
        {
            "question": "Authentication method?",
            "domain": "security",
            "agent_a_role": "Security Specialist",
            "agent_b_role": "Product Manager",
            "agent_a_final_position": "JWT + short expiry + Redis blacklist",
            "agent_b_final_position": "Session + Redis cho đơn giản",
            "common_ground": "Cả 2 đồng ý cần revoke được token ngay lập tức",
            "remaining_disagreements": "JWT vs Session: stateless vs simplicity",
            "final_answer": "JWT (15 min) + Redis blacklist cho revoke",
            "rounds": 5,
            "status": "FORCED",
        },
        {
            "question": "Frontend framework?",
            "domain": "architecture",
            "agent_a_role": "Backend Architect",
            "agent_b_role": "DevOps Specialist",
            "agent_a_final_position": "React + TypeScript",
            "agent_b_final_position": "React + TypeScript",
            "common_ground": "Cả 2 đồng ý React + TypeScript: ecosystem lớn, team quen, CI/CD tooling tốt",
            "remaining_disagreements": "",
            "final_answer": "React + TypeScript",
            "rounds": 2,
            "status": "CONSENSUS",
        },
        {
            "question": "Database?",
            "domain": "data",
            "agent_a_role": "Database Specialist",
            "agent_b_role": "Backend Architect",
            "agent_a_final_position": "PostgreSQL",
            "agent_b_final_position": "PostgreSQL",
            "common_ground": "PostgreSQL: ACID, full-text search, JSON support, team đã quen",
            "remaining_disagreements": "",
            "final_answer": "PostgreSQL",
            "rounds": 1,
            "status": "CONSENSUS",
        },
    ]

    print(format_debate_report("Forum chia sẻ kiến thức", mock_results))
    print("\n" + "=" * 60 + "\n")

    mock_tasks = [
        {"id": "TASK-1", "title": "Thiết kế DB schema", "priority": "high", "deps": [], "status": "ready"},
        {"id": "TASK-2", "title": "Setup authentication", "priority": "high", "deps": ["TASK-1"], "status": "blocked"},
        {"id": "TASK-3", "title": "API endpoints", "priority": "high", "deps": ["TASK-1", "TASK-2"], "status": "blocked"},
        {"id": "TASK-4", "title": "React frontend", "priority": "medium", "deps": ["TASK-3"], "status": "blocked"},
        {"id": "TASK-5", "title": "Testing + QA", "priority": "high", "deps": ["TASK-3", "TASK-4"], "status": "blocked"},
    ]

    print(format_task_graph_report("Forum chia sẻ kiến thức", mock_tasks))
