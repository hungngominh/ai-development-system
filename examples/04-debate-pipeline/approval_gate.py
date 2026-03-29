"""
Approval Gate: Cơ chế dừng pipeline cho con người duyệt.

2 approval gates:
  Gate 1: Sau debate, trước OpenSpec — duyệt đáp án các câu hỏi (CONSENSUS/FORCED)
  Gate 2: Sau sinh task graph, trước execution — duyệt tasks và dependencies

Mỗi gate:
  1. Ghi report ra file markdown
  2. Hiển thị report lên terminal
  3. Chờ con người nhập quyết định
  4. Xử lý quyết định (approve / override / reject)
"""
import json
from pathlib import Path
from report_formatter import format_debate_report, format_task_graph_report

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _ensure_reports_dir():
    REPORTS_DIR.mkdir(exist_ok=True)


def _save_report(filename: str, content: str) -> Path:
    _ensure_reports_dir()
    filepath = REPORTS_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _display_report(content: str):
    print()
    print("=" * 70)
    print(content)
    print("=" * 70)
    print()


# ============================================================
# Gate 1: Duyệt kết quả debate
# ============================================================

def approval_gate_debate(
    project_name: str,
    debate_results: list[dict],
) -> list[dict]:
    """Gate 1: Hiển thị debate report và chờ con người duyệt.

    Args:
        project_name: Tên dự án
        debate_results: List kết quả debate từ debate_crew (mỗi item có status CONSENSUS/FORCED)

    Returns:
        List kết quả đã được con người duyệt. Mỗi item thêm:
            - "human_decision": "approved" | "overridden"
            - "human_answer": str (nếu overridden)
    """
    report = format_debate_report(project_name, debate_results)
    filepath = _save_report("debate_report.md", report)
    _display_report(report)
    print(f"📄 Report đã lưu tại: {filepath}")
    print()

    approved_results = []

    # Xử lý FORCED items trước (bắt buộc)
    forced = [r for r in debate_results if r.get("status") == "FORCED"]
    consensus = [r for r in debate_results if r.get("status") == "CONSENSUS"]

    if forced:
        print(f"⚠️  Có {len(forced)} câu hỏi FORCED — bạn bắt buộc phải quyết định:\n")
        for i, result in enumerate(forced, 1):
            result = _handle_forced_item(result, i)
            approved_results.append(result)

    if consensus:
        print(f"\n✅ Có {len(consensus)} câu hỏi CONSENSUS — confirm hoặc override:\n")
        for i, result in enumerate(consensus, 1):
            result = _handle_consensus_item(result, i)
            approved_results.append(result)

    # Lưu kết quả đã duyệt
    approved_json = json.dumps(approved_results, indent=2, ensure_ascii=False)
    _save_report("debate_approved.json", approved_json)
    print(f"\n✅ Kết quả đã duyệt lưu tại: {REPORTS_DIR / 'debate_approved.json'}")

    return approved_results


def _handle_forced_item(result: dict, index: int) -> dict:
    """Xử lý một item FORCED — bắt buộc con người chọn."""
    print(f"--- FORCED F{index}: {result['question']} ---")
    print(f"  Agent A ({result['agent_a_role']}): {result.get('agent_a_final_position', '')}")
    print(f"  Agent B ({result['agent_b_role']}): {result.get('agent_b_final_position', '')}")
    print(f"  Moderator chốt: {result.get('final_answer', '')}")
    print()
    print(f"  [1] Đồng ý moderator")
    print(f"  [2] Chọn {result['agent_a_role']}")
    print(f"  [3] Chọn {result['agent_b_role']}")
    print(f"  [4] Nhập ý kiến khác")

    while True:
        choice = input("  Chọn (1/2/3/4): ").strip()
        if choice == "1":
            result["human_decision"] = "approved"
            result["human_answer"] = result.get("final_answer", "")
            break
        elif choice == "2":
            result["human_decision"] = "overridden"
            result["human_answer"] = result.get("agent_a_final_position", "")
            break
        elif choice == "3":
            result["human_decision"] = "overridden"
            result["human_answer"] = result.get("agent_b_final_position", "")
            break
        elif choice == "4":
            answer = input("  Nhập đáp án của bạn: ").strip()
            result["human_decision"] = "overridden"
            result["human_answer"] = answer
            break
        else:
            print("  Vui lòng chọn 1, 2, 3 hoặc 4.")

    result["final_answer"] = result["human_answer"]
    print(f"  → Đáp án cuối: {result['final_answer']}\n")
    return result


def _handle_consensus_item(result: dict, index: int) -> dict:
    """Xử lý một item CONSENSUS — 1-click confirm hoặc override."""
    print(f"--- CONSENSUS C{index}: {result['question']} ---")
    print(f"  Đáp án: {result.get('final_answer', '')}")
    print(f"  ({result.get('rounds', '?')} vòng, cả 2 đồng ý)")

    choice = input("  [Enter] = OK | [o] = Override: ").strip().lower()
    if choice == "o":
        override = input("  Nhập đáp án của bạn: ").strip()
        result["human_decision"] = "overridden"
        result["human_answer"] = override
        result["final_answer"] = override
        print(f"  → Override: {override}\n")
    else:
        result["human_decision"] = "approved"
        result["human_answer"] = result.get("final_answer", "")
        print(f"  → OK\n")
    return result


# ============================================================
# Gate 2: Duyệt task graph
# ============================================================

def approval_gate_task_graph(
    project_name: str,
    tasks: list[dict],
) -> list[dict] | None:
    """Gate 2: Hiển thị task graph và chờ con người duyệt.

    Args:
        project_name: Tên dự án
        tasks: List tasks từ task_graph_generator

    Returns:
        List tasks đã được con người duyệt, hoặc None nếu bị reject.
    """
    report = format_task_graph_report(project_name, tasks)
    filepath = _save_report("task_graph_report.md", report)
    _display_report(report)
    print(f"📄 Report đã lưu tại: {filepath}")
    print()

    while True:
        print("HÀNH ĐỘNG:")
        print("  [a] Approve task graph")
        print("  [e] Edit (sửa task/dependency)")
        print("  [r] Reject (yêu cầu tạo lại)")
        choice = input("Chọn (a/e/r): ").strip().lower()

        if choice == "a":
            print("→ Task graph đã được approve!")
            break
        elif choice == "e":
            tasks = _edit_task_graph(tasks)
            _display_report(format_task_graph_report(project_name, tasks))
        elif choice == "r":
            print("→ Task graph bị reject. Cần tạo lại.")
            return None
        else:
            print("Lựa chọn không hợp lệ. Vui lòng chọn a, e, hoặc r.")

    approved_json = json.dumps(tasks, indent=2, ensure_ascii=False)
    _save_report("task_graph_approved.json", approved_json)
    print(f"✅ Task graph đã duyệt lưu tại: {REPORTS_DIR / 'task_graph_approved.json'}")
    return tasks


def _edit_task_graph(tasks: list[dict]) -> list[dict]:
    """Cho phép con người sửa task graph."""
    print()
    print("Các thao tác:")
    print("  [add]    Thêm task mới")
    print("  [remove] Xóa task")
    print("  [dep]    Sửa dependency")
    print("  [done]   Xong, quay lại review")

    while True:
        action = input("\nThao tác (add/remove/dep/done): ").strip().lower()

        if action == "done":
            break

        elif action == "add":
            title = input("  Tiêu đề task: ").strip()
            priority = input("  Priority (high/medium/low) [medium]: ").strip() or "medium"
            deps_str = input("  Dependencies (ID cách nhau bởi dấu phẩy, hoặc Enter nếu không có): ").strip()
            deps = [d.strip() for d in deps_str.split(",") if d.strip()] if deps_str else []

            existing_ids = [t["id"] for t in tasks]
            max_num = max((int(tid.split("-")[1]) for tid in existing_ids if "-" in tid), default=0)
            new_id = f"TASK-{max_num + 1}"
            new_task = {
                "id": new_id,
                "title": title,
                "priority": priority,
                "deps": deps,
                "status": "blocked" if deps else "ready",
            }
            tasks.append(new_task)
            print(f"  → Đã thêm: {new_id} — {title}")

        elif action == "remove":
            task_id = input("  ID task cần xóa: ").strip().upper()
            tasks = [t for t in tasks if t["id"] != task_id]
            for t in tasks:
                t["deps"] = [d for d in t["deps"] if d != task_id]
            print(f"  → Đã xóa: {task_id}")

        elif action == "dep":
            task_id = input("  ID task cần sửa dependency: ").strip().upper()
            task = next((t for t in tasks if t["id"] == task_id), None)
            if not task:
                print(f"  Không tìm thấy task {task_id}")
                continue
            print(f"  Dependency hiện tại: {task['deps']}")
            new_deps_str = input("  Dependency mới (ID cách nhau bởi dấu phẩy, hoặc Enter = xóa hết): ").strip()
            task["deps"] = [d.strip() for d in new_deps_str.split(",") if d.strip()] if new_deps_str else []
            task["status"] = "blocked" if task["deps"] else "ready"
            print(f"  → Đã cập nhật dependency cho {task_id}: {task['deps']}")

    # Cập nhật status sau khi sửa
    existing_ids = {t["id"] for t in tasks}
    for t in tasks:
        t["deps"] = [d for d in t["deps"] if d in existing_ids]
        t["status"] = "blocked" if t["deps"] else "ready"

    return tasks


# ============================================================
# Demo / Test
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("APPROVAL GATE — DEMO")
    print("=" * 60)
    print()
    print("Demo này chạy interactive — cần nhập từ terminal.")
    print()

    mock_debate = [
        {
            "question": "Authentication method?",
            "domain": "security",
            "agent_a_role": "Security Specialist",
            "agent_b_role": "Product Manager",
            "agent_a_final_position": "JWT + Redis blacklist",
            "agent_b_final_position": "Session + Redis",
            "common_ground": "Cần revoke được ngay",
            "remaining_disagreements": "JWT vs Session",
            "final_answer": "JWT (15 min) + Redis blacklist",
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
            "common_ground": "Đồng ý hoàn toàn về React + TypeScript",
            "remaining_disagreements": "",
            "final_answer": "React + TypeScript",
            "rounds": 2,
            "status": "CONSENSUS",
        },
    ]

    result = approval_gate_debate("Forum Demo", mock_debate)
    print(f"\nKết quả đã duyệt: {len(result)} câu hỏi")
