# Example 04: Debate Pipeline (Human-as-Approver)

Pipeline mới: con người chỉ nhập ý tưởng thô, AI tự tranh luận và tạo task, con người chỉ duyệt.

## Mô hình

```
Ý tưởng thô -> AI Debate (2 agents x vòng lặp) -> [Con người duyệt] -> Spec -> AI tạo tasks -> [Con người duyệt] -> Execution
```

## Cấu trúc files

| File | Vai trò |
|---|---|
| `agent_pairing.py` | Phân loại domain câu hỏi, chọn cặp agent đối lập |
| `debate_crew.py` | Tạo CrewAI crew tranh luận vòng lặp cho 1 câu hỏi |
| `report_formatter.py` | Format kết quả debate thành markdown report |
| `approval_gate.py` | 2 approval gates: duyệt đáp án + duyệt task graph |
| `task_graph_generator.py` | Từ spec sinh tasks + dependencies |
| `debate_pipeline.py` | Main orchestrator nối tất cả lại |

## Chạy thử (Simulation mode)

Không cần API key, sử dụng mock data:

```bash
cd examples/04-debate-pipeline/
python debate_pipeline.py "Forum chia sẻ kiến thức nội bộ"
```

Pipeline sẽ:
1. Sinh 8 câu hỏi brainstorming
2. Simulate debate cho mỗi câu hỏi (mock data)
3. Hiển thị Debate Report — bạn duyệt từng câu
4. Sinh task graph (mock)
5. Hiển thị Task Graph — bạn duyệt
6. Simulate execution

## Chạy thật (Production mode)

Cần:
- Python >= 3.10
- `pip install crewai`
- API key (OpenAI hoặc Anthropic)
- Repo `agency-agents` clone cùng thư mục workspace

Trong code, đổi `simulate=False`:

```python
run_pipeline("Forum chia sẻ kiến thức", simulate=False)
```

## Cơ chế Debate

Mỗi câu hỏi brainstorming được xử lý qua vòng lặp:

```
Vòng 1:
  Agent A (vd: Product Manager)
    -> Đưa quan điểm + lý do + ưu/nhược điểm

  Agent B (vd: Backend Architect)
    -> Phản biện + quan điểm riêng + so sánh

  Moderator: Kiểm tra đồng thuận?
    → CHƯA → chỉ ra điểm bất đồng cụ thể

Vòng 2-5:
  Agent A: Tiếp thu B + phản biện B + điều chỉnh
  Agent B: Tiếp thu A + phản biện A + điều chỉnh
  Moderator: Kiểm tra đồng thuận?
    → ĐỒNG THUẬN 100% hoặc FORCED (5 vòng)
```

### Status Score

- **CONSENSUS**: Cả 2 agent đồng ý 100% → con người 1-click confirm
- **FORCED**: Hết 5 vòng vẫn bất đồng → con người **bắt buộc** quyết định

### Ghép cặp Agent

| Domain | Agent A | Agent B |
|---|---|---|
| Feature/scope | Product Manager | Backend Architect |
| UX/UI | UX Designer | Product Manager |
| Architecture | Backend Architect | DevOps Specialist |
| Data/DB | Database Specialist | Backend Architect |
| Quality | QA Engineer | Backend Architect |
| Security | Security Specialist | Product Manager |

## Liên kết

- [Workflow v2](../../docs/workflow-v2.md) — luồng làm việc mới
- [Data Flow v2](../../docs/diagrams/data-flow-v2.md) — sequence diagram mới
- [Example 03](../03-full-pipeline/) — pipeline v1 (human-in-the-loop)
