"""
Debate Crew: Co che tranh luan DOI XUNG cho moi cau hoi brainstorming.

Moi cau hoi duoc 2 agent tranh luan qua VONG LAP cho den khi dong thuan 100%:
  - Moi vong: Agent A va Agent B BINH DANG — ca 2 deu duoc dua quan diem va phan bien
  - Moderator kiem tra dong thuan sau moi vong
  - Dung khi dong thuan 100% hoac het 5 vong (danh dau FORCED)

Khong ai "noi cuoi" — Moderator la nguoi kiem tra va ket luan.
"""
import json
import re
from crewai import Agent, Task, Crew, Process
from agent_pairing import load_agent_prompt, select_pair_for_question

MAX_ROUNDS = 5


def _create_agents(pairing: dict, question: str) -> tuple:
    """Tao 3 agents tu pairing config."""
    agent_a = Agent(
        role=pairing["agent_a"]["role"],
        goal=f"Dua ra quan diem tot nhat va phan bien doi phuong cho: {question}",
        backstory=load_agent_prompt(pairing["agent_a"]["prompt_path"]),
        verbose=True,
    )
    agent_b = Agent(
        role=pairing["agent_b"]["role"],
        goal=f"Dua ra quan diem tot nhat va phan bien doi phuong cho: {question}",
        backstory=load_agent_prompt(pairing["agent_b"]["prompt_path"]),
        verbose=True,
    )
    moderator = Agent(
        role=pairing["moderator"]["role"],
        goal=pairing["moderator"]["goal"],
        backstory=load_agent_prompt(pairing["moderator"]["prompt_path"]),
        verbose=True,
    )
    return agent_a, agent_b, moderator


def _create_round1_tasks(agent_a, agent_b, moderator, question, context_block, pairing):
    """Tao tasks cho vong 1 (chua co lich su tranh luan)."""

    task_a = Task(
        description=(
            f"Cau hoi can tra loi: {question}\n"
            f"{context_block}\n"
            f"Hay dua ra quan diem cua ban:\n"
            f"1. QUAN DIEM: Dap an ban de xuat\n"
            f"2. LY DO: Tai sao day la lua chon tot\n"
            f"3. UU DIEM: Liet ke cac uu diem chinh\n"
            f"4. NHUOC DIEM: Tu nhan biet cac han che\n"
            f"5. RUI RO: Nhung rui ro can luu y\n"
        ),
        agent=agent_a,
        expected_output="Quan diem day du voi ly do, uu diem, nhuoc diem, rui ro.",
    )

    task_b = Task(
        description=(
            f"Cau hoi can tra loi: {question}\n"
            f"{context_block}\n"
            f"Ban vua doc quan diem cua {pairing['agent_a']['role']}.\n"
            f"Hay:\n"
            f"1. PHAN BIEN: Chi ra diem yeu trong quan diem cua {pairing['agent_a']['role']}\n"
            f"2. QUAN DIEM RIENG: Dap an ban de xuat\n"
            f"3. LY DO: Tai sao quan diem cua ban tot hon hoac bo sung tot\n"
            f"4. UU DIEM: Cua phuong an ban de xuat\n"
            f"5. NHUOC DIEM: Tu nhan biet han che cua phuong an minh\n"
        ),
        agent=agent_b,
        expected_output="Phan bien + quan diem rieng co lap luan.",
        context=[task_a],
    )

    task_mod = _create_moderator_check_task(moderator, question, context_block, pairing, [task_a, task_b])

    return [task_a, task_b, task_mod]


def _create_roundN_tasks(agent_a, agent_b, moderator, question, context_block, pairing,
                         round_num, prev_tasks, disagreements):
    """Tao tasks cho vong 2+ (doi xung — ca 2 deu phan bien)."""

    task_a = Task(
        description=(
            f"Cau hoi: {question}\n"
            f"{context_block}\n"
            f"Day la vong {round_num} cua tranh luan voi {pairing['agent_b']['role']}.\n"
            f"Moderator da chi ra diem bat dong con lai:\n{disagreements}\n\n"
            f"Hay:\n"
            f"1. TIEP THU: Nhung diem hop ly cua {pairing['agent_b']['role']} ma ban dong y\n"
            f"2. PHAN BIEN: Nhung diem cua {pairing['agent_b']['role']} ma ban chua dong y, giai thich tai sao\n"
            f"3. DIEU CHINH: Quan diem moi cua ban (co the da thay doi)\n"
        ),
        agent=agent_a,
        expected_output="Tiep thu + phan bien + quan diem da dieu chinh.",
        context=prev_tasks,
    )

    task_b = Task(
        description=(
            f"Cau hoi: {question}\n"
            f"{context_block}\n"
            f"Day la vong {round_num} cua tranh luan voi {pairing['agent_a']['role']}.\n"
            f"Moderator da chi ra diem bat dong con lai:\n{disagreements}\n\n"
            f"Hay:\n"
            f"1. TIEP THU: Nhung diem hop ly cua {pairing['agent_a']['role']} ma ban dong y\n"
            f"2. PHAN BIEN: Nhung diem cua {pairing['agent_a']['role']} ma ban chua dong y, giai thich tai sao\n"
            f"3. DIEU CHINH: Quan diem moi cua ban (co the da thay doi)\n"
        ),
        agent=agent_b,
        expected_output="Tiep thu + phan bien + quan diem da dieu chinh.",
        context=prev_tasks + [task_a],
    )

    task_mod = _create_moderator_check_task(
        moderator, question, context_block, pairing, prev_tasks + [task_a, task_b]
    )

    return [task_a, task_b, task_mod]


def _create_moderator_check_task(moderator, question, context_block, pairing, context_tasks):
    """Tao task Moderator kiem tra dong thuan."""
    return Task(
        description=(
            f"Cau hoi: {question}\n"
            f"{context_block}\n"
            f"Ban la moderator trung lap. Doc toan bo tranh luan giua "
            f"{pairing['agent_a']['role']} va {pairing['agent_b']['role']}.\n\n"
            f"Hay tra ve JSON:\n"
            f'{{\n'
            f'  "consensus": true hoac false,\n'
            f'  "disagreements": "Liet ke CU THE cac diem bat dong con lai (neu co)",\n'
            f'  "agent_a_position": "Quan diem hien tai cua {pairing["agent_a"]["role"]}",\n'
            f'  "agent_b_position": "Quan diem hien tai cua {pairing["agent_b"]["role"]}",\n'
            f'  "common_ground": "Nhung diem ca 2 da dong y",\n'
            f'  "answer": "Dap an chung neu dong thuan, hoac tom tat tinh hinh neu chua"\n'
            f'}}\n\n'
            f"Quy tac:\n"
            f"- consensus=true CHI KHI ca 2 agent dong y 100% ve moi khia canh\n"
            f"- Neu con BAT KY diem bat dong nao, consensus=false\n"
            f"- Liet ke disagreements CU THE de 2 agent biet can tranh luan gi tiep\n\n"
            f"CHI TRA VE JSON, KHONG THEM TEXT KHAC."
        ),
        agent=moderator,
        expected_output="JSON voi consensus status va chi tiet.",
        context=context_tasks,
    )


def _create_final_synthesis_task(moderator, question, context_block, pairing, all_tasks,
                                 is_forced, round_count):
    """Tao task Moderator tong hop ket qua cuoi cung."""
    forced_note = (
        "KHONG dat duoc dong thuan sau toi da vong tranh luan. "
        "Hay chot phuong an co nhieu diem chung nhat."
    ) if is_forced else (
        "Ca 2 agent da dong thuan 100%."
    )

    return Task(
        description=(
            f"Cau hoi: {question}\n"
            f"{context_block}\n"
            f"Tranh luan da ket thuc sau {round_count} vong. {forced_note}\n\n"
            f"Hay tong hop ket qua cuoi cung dang JSON:\n"
            f'{{\n'
            f'  "question": "{question}",\n'
            f'  "agent_a_role": "{pairing["agent_a"]["role"]}",\n'
            f'  "agent_b_role": "{pairing["agent_b"]["role"]}",\n'
            f'  "agent_a_final_position": "Quan diem cuoi cung cua Agent A",\n'
            f'  "agent_b_final_position": "Quan diem cuoi cung cua Agent B",\n'
            f'  "common_ground": "Tat ca diem 2 ben dong y",\n'
            f'  "remaining_disagreements": "Diem bat dong con lai (neu co)",\n'
            f'  "final_answer": "Dap an cuoi cung",\n'
            f'  "rounds": {round_count},\n'
            f'  "status": "{"FORCED" if is_forced else "CONSENSUS"}"\n'
            f'}}\n\n'
            f"CHI TRA VE JSON, KHONG THEM TEXT KHAC."
        ),
        agent=moderator,
        expected_output="JSON tong hop ket qua cuoi cung.",
        context=all_tasks[-4:],  # Chi lay context gan nhat de tranh tran context
    )


def run_symmetric_debate(question: str, context: str = "", max_rounds: int = MAX_ROUNDS) -> dict:
    """Chay debate doi xung cho 1 cau hoi.

    Vong lap:
      1. Agent A: quan diem (+ phan bien B tu vong 2)
      2. Agent B: phan bien A + quan diem rieng (+ phan bien A tu vong 2)
      3. Moderator: kiem tra dong thuan
      4. Neu chua dong thuan va chua het vong → lap lai
      5. Moderator tong hop ket qua cuoi

    Args:
        question: Cau hoi brainstorming
        context: Boi canh du an
        max_rounds: So vong toi da (mac dinh 5)

    Returns:
        Dict ket qua debate voi status CONSENSUS hoac FORCED
    """
    pairing = select_pair_for_question(question)
    agent_a, agent_b, moderator = _create_agents(pairing, question)
    context_block = f"\nBoi canh du an: {context}\n" if context else ""

    all_tasks = []
    disagreements = ""
    consensus = False
    round_count = 0

    for round_num in range(1, max_rounds + 1):
        round_count = round_num

        if round_num == 1:
            round_tasks = _create_round1_tasks(
                agent_a, agent_b, moderator, question, context_block, pairing
            )
        else:
            round_tasks = _create_roundN_tasks(
                agent_a, agent_b, moderator, question, context_block, pairing,
                round_num, all_tasks[-3:], disagreements
            )

        # Chay vong nay
        crew = Crew(
            agents=[agent_a, agent_b, moderator],
            tasks=round_tasks,
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()

        all_tasks.extend(round_tasks)

        # Parse moderator check
        mod_result = _parse_moderator_check(result)
        consensus = mod_result.get("consensus", False)
        disagreements = mod_result.get("disagreements", "")

        if consensus:
            break

    # Tong hop cuoi cung
    is_forced = not consensus
    synthesis_task = _create_final_synthesis_task(
        moderator, question, context_block, pairing, all_tasks,
        is_forced, round_count
    )

    synthesis_crew = Crew(
        agents=[moderator],
        tasks=[synthesis_task],
        process=Process.sequential,
        verbose=True,
    )
    final_output = synthesis_crew.kickoff()
    final_result = _parse_final_result(final_output, pairing, question)
    final_result["domain"] = pairing["domain"]
    final_result["rounds"] = round_count

    return final_result


def _parse_moderator_check(crew_output) -> dict:
    """Parse output Moderator check dong thuan."""
    raw = str(crew_output)
    json_match = re.search(r'\{[\s\S]*?\}', raw)
    if json_match:
        try:
            result = json.loads(json_match.group())
            # Normalize consensus field
            if isinstance(result.get("consensus"), str):
                result["consensus"] = result["consensus"].lower() == "true"
            return result
        except json.JSONDecodeError:
            pass
    return {"consensus": False, "disagreements": raw[:500]}


def _parse_final_result(crew_output, pairing, question) -> dict:
    """Parse output Moderator tong hop cuoi cung."""
    raw = str(crew_output)
    json_match = re.search(r'\{[\s\S]*?\}', raw)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Fallback
    return {
        "question": question,
        "agent_a_role": pairing["agent_a"]["role"],
        "agent_b_role": pairing["agent_b"]["role"],
        "agent_a_final_position": raw[:200],
        "agent_b_final_position": "",
        "common_ground": "",
        "remaining_disagreements": raw[:300],
        "final_answer": raw[:200],
        "rounds": 0,
        "status": "FORCED",
    }


# ============================================================
# Demo / Test
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("DEBATE CREW — SYMMETRIC DEBATE DEMO")
    print("=" * 60)
    print()
    print("Mo hinh debate doi xung:")
    print("  - Moi vong: Agent A va Agent B BINH DANG")
    print("  - Ca 2 deu duoc dua quan diem + phan bien doi phuong")
    print("  - Moderator kiem tra dong thuan sau moi vong")
    print("  - Dung khi dong thuan 100% hoac het 5 vong")
    print()
    print("Cau truc 1 vong:")
    print("  Agent A: tiep thu B + phan bien B + dieu chinh quan diem")
    print("  Agent B: tiep thu A + phan bien A + dieu chinh quan diem")
    print("  Moderator: dong thuan chua? → diem bat dong con lai")
    print()
    print("Luu y: Can cai dat CrewAI va co API key de chay thuc te.")
    print("       pip install crewai")
    print()

    question = "Nen dung tech stack gi cho backend cua forum?"
    context = "Forum chia se kien thuc noi bo cong ty, khoang 500 nguoi dung"
    pairing = select_pair_for_question(question)

    print(f"Question: {question}")
    print(f"Context:  {context}")
    print(f"Agent A:  {pairing['agent_a']['role']}")
    print(f"Agent B:  {pairing['agent_b']['role']}")
    print(f"Max rounds: {MAX_ROUNDS}")
    print()
    print("De chay thuc te:")
    print("  from debate_crew import run_symmetric_debate")
    print(f'  result = run_symmetric_debate("{question}", "{context}")')
    print("  # result['status'] = 'CONSENSUS' hoac 'FORCED'")
    print("  # result['rounds'] = so vong da tranh luan")
