# Hướng dẫn tích hợp 5 thành phần

Tài liệu này hướng dẫn từng bước cách tích hợp tất cả 5 thành phần trong hệ thống: **OpenSpec**, **Beads**, **CrewAI**, **agency-agents**, và **Superpowers**. Mỗi bước bao gồm các command cụ thể và output mong đợi.

## Prerequisites

Trước khi bắt đầu, đảm bảo bạn đã chuẩn bị:

- Tất cả 5 repo đã được clone trong cùng một workspace
- Python >= 3.10, Node.js 18+, Go 1.25+, Git đã cài đặt
- CrewAI đã cài đặt: `pip install crewai`
- OpenSpec đã cài đặt: `npm install -g @fission-ai/openspec`
- Beads đã cài đặt (build from source theo hướng dẫn trong Beads README)

## Bước 1: Viết Spec (OpenSpec)

**Mục đích:** Định nghĩa requirements trước khi viết code. Spec là nguồn gốc của mọi thứ — không có spec, không có task, không có code.

### Các command chính

Khởi tạo OpenSpec lần đầu:

```bash
openspec init
```

Đề xuất một feature mới thông qua AI assistant:

```
/opsx:propose "Tên feature"
```

Hoặc validate một change đã có:

```bash
openspec validate <change-name>
```

### Output mong đợi

Sau khi hoàn tất, thư mục `openspec/changes/<name>/` sẽ chứa:

- `proposal.md` — mô tả tổng quan feature
- `specs/` — các specification chi tiết
- `design.md` — thiết kế kỹ thuật
- `tasks.md` — danh sách task cần thực hiện

Tham khảo thêm: [references/openspec.md](../references/openspec.md)

## Bước 2: Tạo Task Graph (Beads)

**Mục đích:** Tạo các task có thể theo dõi với dependency rõ ràng từ spec ở Bước 1.

### Các command chính

Tạo task mới:

```bash
bd create "Task title" --type task --priority high
```

Thiết lập dependency giữa các task:

```bash
bd dep add <child-id> blocks <parent-id>
```

Xem tất cả task:

```bash
bd list
```

Xem các task sẵn sàng để thực hiện (không còn dependency nào chặn):

```bash
bd ready
```

### Output mong đợi

Các task được lưu trong Beads với dependency graph hoàn chỉnh. Dùng `bd list` để kiểm tra trạng thái tổng thể.

Tham khảo thêm: [references/beads.md](../references/beads.md)

## Bước 3: Chạy Pipeline (CrewAI + agency-agents)

**Mục đích:** Các agent tự động thực hiện task từ task graph. Mỗi agent có role, goal, và backstory được định nghĩa từ agency-agents.

### Code snippet

```python
from crewai import Agent, Task, Crew, Process

agent = Agent(
    role="Backend Architect",
    goal="Design database schema",
    backstory=open("../agency-agents/engineering/engineering-backend-architect.md").read(),
    llm="claude-sonnet-4-6"
)

task = Task(
    description="Thiết kế database schema cho user management",
    expected_output="SQL migration file và ERD diagram",
    agent=agent
)

crew = Crew(
    agents=[agent],
    tasks=[task],
    process=Process.sequential
)

result = crew.kickoff()
```

Bạn có thể tạo nhiều agent với các role khác nhau (Frontend Developer, QA Engineer, DevOps Specialist, ...) và kết hợp chúng trong cùng một Crew.

Tham khảo thêm: [references/crewai.md](../references/crewai.md) và [references/agency-agents.md](../references/agency-agents.md)

## Bước 4: Đảm bảo chất lượng (Superpowers)

**Mục đích:** Áp dụng quality gate ở mọi giai đoạn của quy trình, đảm bảo output đạt tiêu chuẩn trước khi chuyển sang bước tiếp theo.

### Các skill quan trọng

| Skill | Vai trò |
|---|---|
| **brainstorming** | Khám phá requirements trước khi implementation — không bỏ qua bước này |
| **test-driven-development** | Viết test trước, luôn luôn — code chỉ được viết sau khi test đã tồn tại |
| **requesting-code-review** | Dispatch reviewer agent sau mỗi task hoàn thành |
| **verification-before-completion** | Phải có bằng chứng cụ thể trước khi tuyên bố hoàn thành |
| **systematic-debugging** | Quy trình 4 phase để tìm root cause khi có lỗi |

Các skill này không phải optional — chúng là quality gate bắt buộc trong quy trình.

Tham khảo thêm: [references/superpowers.md](../references/superpowers.md)

## Bước 5: Lưu kết quả (Beads)

**Mục đích:** Đóng task, tạo report, và duy trì audit trail đầy đủ cho toàn bộ quá trình.

### Các command chính

Đóng task khi hoàn thành:

```bash
bd update <id> --status closed
```

Xem thống kê tổng quan (open, closed, blocked, lead time):

```bash
bd admin stats
```

Xem toàn bộ lịch sử thay đổi của một task:

```bash
bd show <id>
```

Xem trạng thái của task tại một thời điểm cụ thể trong quá khứ:

```bash
bd show <id> --as-of <ref>
```

### Output mong đợi

- Audit trail hoàn chỉnh cho mỗi task
- Thống kê về throughput và lead time
- Lịch sử thay đổi có thể truy vết tại bất kỳ thời điểm nào

## Tổng kết

Quy trình 5 bước trên tạo thành một vòng lặp khép kín:

1. **Spec** (OpenSpec) — định nghĩa cần làm gì
2. **Task Graph** (Beads) — chia nhỏ và theo dõi tiến độ
3. **Pipeline** (CrewAI + agency-agents) — thực thi tự động
4. **Quality Gates** (Superpowers) — đảm bảo chất lượng
5. **Audit Trail** (Beads) — lưu kết quả và học từ dữ liệu

Xem [docs/workflow.md](workflow.md) để tham khảo ví dụ end-to-end hoàn chỉnh.
