"""
Example 04: Debate Pipeline — Human-as-Approver

Pipeline đầy đủ: Ý tưởng thô -> AI Debate (đối xứng, vòng lặp) -> Con người duyệt
-> Spec -> AI tạo tasks -> Con người duyệt -> Execution.

Con người chỉ cần:
  1. Nhập ý tưởng thô
  2. Gate 1: quyết định FORCED items, confirm CONSENSUS items (~5 phút)
  3. Gate 2: approve task graph (~1 phút)

Lưu ý: Cần cài đặt CrewAI và có API key để chạy thực tế.
        Dùng simulate=True để chạy demo không cần API key.
"""
import sys
import hashlib
from pathlib import Path
from datetime import datetime

from agent_pairing import select_pair_for_question, classify_question_domain
from debate_crew import run_symmetric_debate
from approval_gate import approval_gate_debate, approval_gate_task_graph
from task_graph_generator import generate_task_graph, generate_beads_commands, parse_task_graph


# ============================================================
# Phase 1a: Sinh câu hỏi brainstorming
# ============================================================

DEFAULT_QUESTIONS = [
    "Những feature chính nào cần có trong MVP?",
    "Ai là người dùng chính (primary users) của hệ thống?",
    "Tech stack nào phù hợp cho backend?",
    "Tech stack nào phù hợp cho frontend?",
    "Database nào nên dùng và schema thiết kế như thế nào?",
    "Authentication và authorization nên xử lý như thế nào?",
    "Hệ thống cần những API endpoints chính nào?",
    "Chiến lược testing như thế nào? (unit, integration, e2e)",
]


def generate_brainstorming_questions(raw_idea: str) -> list[str]:
    """Sinh câu hỏi brainstorming từ ý tưởng thô.

    Production: Superpowers brainstorming skill sinh câu hỏi thông minh theo context.
    Demo: dùng danh sách câu hỏi mặc định.
    """
    print(f"\n📋 Đang sinh câu hỏi brainstorming cho: \"{raw_idea}\"")
    print(f"   (Production: Superpowers brainstorming skill)")
    print(f"   (Demo: {len(DEFAULT_QUESTIONS)} câu hỏi mặc định)\n")
    return DEFAULT_QUESTIONS


# ============================================================
# Phase 1b: Chạy debate cho tất cả câu hỏi
# ============================================================

def run_all_debates(
    questions: list[str],
    raw_idea: str,
    simulate: bool = True,
) -> list[dict]:
    """Chạy debate đối xứng cho toàn bộ câu hỏi.

    Mỗi câu hỏi: 2 agent tranh luận vòng lặp đến đồng thuận (tối đa 5 vòng).
    """
    print(f"🤖 Bắt đầu tranh luận {len(questions)} câu hỏi...")
    print(f"   Mỗi câu: 2 agent đối xứng, tối đa 5 vòng")
    print(f"   Kết quả: CONSENSUS (đồng thuận) hoặc FORCED (hết vòng)\n")

    results = []
    for i, question in enumerate(questions, 1):
        pairing = select_pair_for_question(question)
        print(f"  [{i}/{len(questions)}] {question}")
        print(f"    Agents: {pairing['agent_a']['role']} ↔ {pairing['agent_b']['role']}")

        if simulate:
            result = _simulate_debate(question, pairing)
        else:
            result = run_symmetric_debate(question, context=raw_idea)

        status = result.get("status", "?")
        rounds = result.get("rounds", "?")
        icon = "✅" if status == "CONSENSUS" else "⚠️ "
        print(f"    {icon} {status} ({rounds} vòng): {result.get('final_answer', '')[:60]}")
        print()
        results.append(result)

    return results


def _simulate_debate(question: str, pairing: dict) -> dict:
    """Tạo mock debate result cho demo (không cần API key)."""
    h = int(hashlib.md5(question.encode()).hexdigest(), 16)
    # 80% CONSENSUS, 20% FORCED
    is_forced = (h % 5 == 0)
    rounds = (h % 4) + (2 if not is_forced else 5)

    return {
        "question": question,
        "domain": classify_question_domain(question),
        "agent_a_role": pairing["agent_a"]["role"],
        "agent_b_role": pairing["agent_b"]["role"],
        "agent_a_final_position": f"[{pairing['agent_a']['role']}] Phương án A cho: {question[:40]}",
        "agent_b_final_position": f"[{pairing['agent_b']['role']}] Phương án B cho: {question[:40]}",
        "common_ground": f"Cả 2 đồng ý về nguyên tắc chính" if not is_forced else "Đồng ý về mục tiêu, bất đồng về cách thực hiện",
        "remaining_disagreements": "" if not is_forced else "Bất đồng về approach cụ thể sau 5 vòng",
        "final_answer": f"Phương án tối ưu cho: {question[:50]} (simulated)",
        "rounds": rounds,
        "status": "FORCED" if is_forced else "CONSENSUS",
    }


# ============================================================
# Phase 1d: Formalize spec từ đáp án đã duyệt
# ============================================================

def formalize_spec(raw_idea: str, approved_answers: list[dict]) -> str:
    """Tạo OpenSpec spec từ ý tưởng + đáp án đã duyệt.

    Production: OpenSpec /opsx:propose với context là đáp án đã duyệt.
    Demo: tạo text spec tổng hợp.
    """
    print("\n📝 Đang formalize spec từ đáp án đã duyệt...")
    print("   (Production: OpenSpec /opsx:propose)")

    lines = [
        f"# {raw_idea}",
        f"",
        f"## Thông tin dự án",
        f"- Ngày tạo: {datetime.now().strftime('%Y-%m-%d')}",
        f"- Nguồn: AI Debate Pipeline v3",
        f"",
        f"## Các quyết định đã duyệt",
        f"",
    ]

    for a in approved_answers:
        decision_tag = "APPROVED" if a.get("human_decision") == "approved" else "OVERRIDDEN"
        status = a.get("status", "?")
        lines.append(f"### {a['question']}")
        lines.append(f"- **Đáp án:** {a.get('final_answer', a.get('human_answer', ''))}")
        lines.append(f"- **Debate:** {status} ({a.get('rounds', '?')} vòng)")
        lines.append(f"- **Trạng thái:** {decision_tag}")
        lines.append("")

    spec = "\n".join(lines)
    print(f"   Spec đã tạo ({len(approved_answers)} quyết định)")
    return spec


# ============================================================
# Phase 2a: Sinh task graph
# ============================================================

def auto_generate_tasks(
    spec_text: str,
    approved_answers: list[dict],
    simulate: bool = True,
) -> list[dict]:
    """Sinh task graph từ spec.

    Production: CrewAI Task Graph Generator crew đọc spec và sinh tasks.
    Demo: mock task graph.
    """
    print("\n📊 Đang sinh task graph từ spec...")
    print("   (Production: CrewAI Task Graph Generator)")

    if simulate:
        tasks = [
            {"id": "TASK-1", "title": "Thiết kế database schema", "priority": "high", "deps": [], "status": "ready"},
            {"id": "TASK-2", "title": "Setup authentication", "priority": "high", "deps": ["TASK-1"], "status": "blocked"},
            {"id": "TASK-3", "title": "API endpoints (CRUD)", "priority": "high", "deps": ["TASK-1", "TASK-2"], "status": "blocked"},
            {"id": "TASK-4", "title": "Frontend setup + routing", "priority": "medium", "deps": ["TASK-3"], "status": "blocked"},
            {"id": "TASK-5", "title": "UI components", "priority": "medium", "deps": ["TASK-4"], "status": "blocked"},
            {"id": "TASK-6", "title": "Testing + QA", "priority": "high", "deps": ["TASK-3", "TASK-5"], "status": "blocked"},
        ]
        print(f"   Đã sinh {len(tasks)} tasks (simulated)")
        return tasks
    else:
        return generate_task_graph(spec_text, approved_answers)


# ============================================================
# Phase 3-5: Execution (giữ nguyên từ v1)
# ============================================================

def execute_tasks(tasks: list[dict], simulate: bool = True):
    """Thực thi tasks qua CrewAI + agency-agents.

    Phase 3-5 giữ nguyên từ pipeline v1:
    - CrewAI + agency-agents thực thi theo dependency graph
    - Superpowers: TDD, code review, verification
    - Beads: cập nhật status, lưu audit trail
    """
    print("\n" + "=" * 60)
    print("PHASE 3-5: EXECUTION (giữ nguyên từ v1)")
    print("=" * 60)

    if simulate:
        for t in tasks:
            print(f"  ▶ {t['id']}: {t['title']}...")
            t["status"] = "closed"
            print(f"  ✅ {t['id']} — done")

        print()
        print("  🛡️  Superpowers verification: All tests passed (simulated)")
        print("  📊 Beads audit trail: Updated (simulated)")

        closed = sum(1 for t in tasks if t["status"] == "closed")
        print(f"\n  Thống kê: {closed}/{len(tasks)} tasks hoàn thành")
    else:
        print("  → Khởi động CrewAI crew với task graph đã duyệt...")
        print("  → Superpowers: TDD + code review + verification...")
        print("  → Beads: cập nhật audit trail...")


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_pipeline(raw_idea: str, simulate: bool = True):
    """Chạy toàn bộ Debate Pipeline từ ý tưởng thô đến sản phẩm.

    Args:
        raw_idea: Ý tưởng thô của người dùng
        simulate: True = demo với mock data, False = chạy CrewAI thật
    """
    print("=" * 60)
    print("AI DEVELOPMENT SYSTEM — DEBATE PIPELINE v3")
    print("Human-as-Approver | Symmetric Debate | Consensus Loop")
    print("=" * 60)
    print(f"\nMode: {'SIMULATION (không cần API key)' if simulate else 'PRODUCTION (CrewAI thật)'}")
    print(f"Input: {raw_idea}")

    # --- Phase 1a: Sinh câu hỏi ---
    questions = generate_brainstorming_questions(raw_idea)

    # --- Phase 1b: AI Debate (đối xứng, vòng lặp) ---
    debate_results = run_all_debates(questions, raw_idea, simulate=simulate)

    # Tóm tắt trước Gate 1
    forced_count = sum(1 for r in debate_results if r.get("status") == "FORCED")
    consensus_count = sum(1 for r in debate_results if r.get("status") == "CONSENSUS")
    print(f"\n📊 Kết quả debate: {consensus_count} CONSENSUS, {forced_count} FORCED")

    # --- Gate 1: Con người duyệt đáp án ---
    print("\n" + "=" * 60)
    print("GATE 1: DUYỆT KẾT QUẢ DEBATE")
    print("=" * 60)
    approved_answers = approval_gate_debate(raw_idea, debate_results)

    # --- Phase 1d: Formalize spec ---
    spec_text = formalize_spec(raw_idea, approved_answers)

    # --- Phase 2a: Sinh task graph ---
    tasks = auto_generate_tasks(spec_text, approved_answers, simulate=simulate)

    # --- Gate 2: Con người duyệt task graph ---
    print("\n" + "=" * 60)
    print("GATE 2: DUYỆT TASK GRAPH")
    print("=" * 60)

    approved_tasks = approval_gate_task_graph(raw_idea, tasks)
    if approved_tasks is None:
        print("\n⚠️  Task graph bị reject. Tạo lại...")
        tasks = auto_generate_tasks(spec_text, approved_answers, simulate=simulate)
        approved_tasks = approval_gate_task_graph(raw_idea, tasks)
        if approved_tasks is None:
            print("❌ Task graph vẫn bị reject. Dừng pipeline.")
            return

    # --- Phase 2c: Sinh Beads commands ---
    print("\n📋 Beads commands (chạy trong terminal):")
    for cmd in generate_beads_commands(approved_tasks):
        print(f"  $ {cmd}")

    # --- Phase 3-5: Execution ---
    execute_tasks(approved_tasks, simulate=simulate)

    # --- Kết thúc ---
    overridden = sum(1 for a in approved_answers if a.get("human_decision") == "overridden")
    print("\n" + "=" * 60)
    print("✅ PIPELINE HOÀN THÀNH")
    print("=" * 60)
    print(f"  Dự án:              {raw_idea}")
    print(f"  Câu hỏi debate:     {len(questions)}")
    print(f"    - CONSENSUS:      {consensus_count}")
    print(f"    - FORCED:         {forced_count}")
    print(f"  Override bởi bạn:   {overridden}")
    print(f"  Tasks đã tạo:       {len(approved_tasks)}")
    print(f"  Mode:               {'SIMULATION' if simulate else 'PRODUCTION'}")
    print()
    print(f"  📁 Reports: examples/04-debate-pipeline/reports/")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        idea = " ".join(sys.argv[1:])
    else:
        idea = input("Nhập ý tưởng của bạn: ").strip()
        if not idea:
            idea = "Forum chia sẻ kiến thức nội bộ công ty"
            print(f"  (Dùng ý tưởng mặc định: {idea})")

    # Chạy simulation mode — không cần API key
    run_pipeline(idea, simulate=True)
