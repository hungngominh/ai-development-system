# AI Development System — Thiết kế Research Hub

> Ngày tạo: 2026-03-29
> Phương án: Monorepo tham chiếu
> Ngôn ngữ: Tiếng Việt (code và thuật ngữ kỹ thuật giữ tiếng Anh)
> Đối tượng: Chia sẻ cho đồng nghiệp
> Vị trí: `E:\Work\ai-development-system`

---

## Mục đích

Tạo một research hub tập trung ghi lại hệ thống AI development hoàn chỉnh gồm 5 thành phần, phục vụ:

- Nghiên cứu và thí nghiệm cách tích hợp các công cụ AI
- Chia sẻ kiến thức cho đồng nghiệp
- Lưu trữ findings và ghi chú nghiên cứu theo thời gian

Dự án này **không chứa code của 5 repo gốc**, chỉ tham chiếu đến chúng.

---

## 5 thành phần hệ thống

| Vai trò | Repo | Mô tả |
|---|---|---|
| Chuyên môn | `agency-agents` | Bộ sưu tập 100+ AI agent prompts chuyên biệt theo lĩnh vực |
| Điều phối | `CrewAI` | Framework multi-agent orchestration (sequential/hierarchical) |
| Quy trình | `OpenSpec` | Spec-driven development: proposal → specs → design → tasks → verify → archive |
| Kỷ luật | `Superpowers` | 15 skills bắt buộc: TDD, code review, verification, debugging |
| Lưu vết | `Beads` | Distributed graph issue tracker với audit trail, dependency graph, thống kê |

Mối quan hệ:

```
Input (yêu cầu)
    │
    ▼
OpenSpec ──→ Spec + Design + Tasks        (QUY TRÌNH)
    │
    ▼
Beads ──→ Task graph + Audit trail        (LƯU VẾT)
    │
    ▼
CrewAI ←──→ agency-agents                 (ĐIỀU PHỐI + CHUYÊN MÔN)
    │
    ▼
Superpowers ──→ TDD + Review + Verify     (ĐÁNH GIÁ)
```

---

## Cấu trúc thư mục

```
ai-development-system/
│
├── README.md                          # Tổng quan, bản đồ 5 thành phần, quick start
├── CHANGELOG.md                       # Ghi lại thay đổi
│
├── docs/                              # Tài liệu kiến trúc
│   ├── architecture.md                # Kiến trúc tổng thể, vai trò, giới hạn
│   ├── integration-guide.md           # Hướng dẫn tích hợp 5 bước
│   ├── memory-analysis.md             # Phân tích 4 tầng trí nhớ AI
│   ├── workflow.md                    # Luồng end-to-end với ví dụ thực tế
│   └── diagrams/                      # Sơ đồ Mermaid
│       ├── system-overview.md         # Sơ đồ tổng thể
│       ├── data-flow.md              # Luồng dữ liệu giữa 5 thành phần
│       └── memory-layers.md          # Sơ đồ 4 tầng memory
│
├── references/                        # Thẻ tham chiếu (~1-2 trang/repo)
│   ├── crewai.md                      # Vai trò, tính năng, commands, kết nối
│   ├── agency-agents.md               # Danh mục agents, cách dùng
│   ├── beads.md                       # Tracking, audit, commands thường dùng
│   ├── openspec.md                    # Spec workflow, validation rules
│   └── superpowers.md                 # Skills, quality gates
│
├── research/                          # Ghi chú nghiên cứu
│   ├── README.md                      # Convention viết notes (YYYY-MM-DD-<chủ-đề>.md)
│   ├── 2026-03-29-initial-analysis.md # Phân tích ban đầu từ hội thoại đầu tiên
│   └── experiments/                   # Thư mục thí nghiệm
│       └── README.md                  # Template: mục tiêu, cách thực hiện, kết quả, kết luận
│
└── examples/                          # Code mẫu minh họa
    ├── README.md                      # Hướng dẫn chạy, yêu cầu cài đặt
    ├── 01-basic-crew/                 # CrewAI + agency-agents cơ bản
    │   └── crew_with_agents.py
    ├── 02-spec-driven-crew/           # OpenSpec → CrewAI pipeline
    │   └── spec_to_crew.py
    └── 03-full-pipeline/              # Pipeline đầy đủ 5 thành phần
        └── full_pipeline.py
```

---

## Nội dung chi tiết từng phần

### README.md

1. **Giới thiệu** — Hệ thống AI Development là gì, giải quyết vấn đề gì
2. **Bản đồ 5 thành phần** — Bảng + analogy (não, cơ thể, quy trình, kỷ luật, ký ức)
3. **Sơ đồ kiến trúc** — Mermaid diagram inline
4. **Quick Start** — 3 bước: clone repos → đọc integration guide → chạy example
5. **Mục lục** — Link đến docs/, references/, research/, examples/
6. **Yêu cầu** — Python 3.11+, Node.js 18+, Go (cho Beads)

### docs/architecture.md

- Sơ đồ tổng thể 5 thành phần và mối quan hệ
- Vai trò cụ thể từng thành phần (tóm tắt, chi tiết ở references/)
- Bảng mapping: "Vấn đề → Thành phần giải quyết → Cách giải quyết"
- Giới hạn đã biết:
  - Intra-session memory (context window vật lý)
  - Handoff quality giữa agents
  - Memory accuracy validation

### docs/integration-guide.md

5 bước tích hợp với lệnh cụ thể:
1. OpenSpec viết spec → `/opsx:propose`
2. Beads tạo task graph → `bd create`, `bd dep`
3. CrewAI + agency-agents chạy pipeline → Python script
4. Superpowers quality gates → TDD, code review, verification
5. Beads lưu kết quả → `bd update`, `bd admin stats`

### docs/memory-analysis.md

4 tầng trí nhớ:
1. Context window (⚠️ giới hạn vật lý)
2. Cross-session (✅ Beads + OpenSpec + CrewAI Memory)
3. Cross-agent (✅ CrewAI scoped memory + Beads)
4. Dài hạn (✅ Dolt + LanceDB + file specs)

Đánh giá tổng: 7/10, 3 lỗ hổng còn lại + hướng nghiên cứu.

### docs/workflow.md

Ví dụ end-to-end "Xây forum chia sẻ kiến thức":
- Spec → Tasks → Execute → Verify → Report
- Mermaid diagram timeline
- Output mong đợi ở mỗi bước

### references/*.md

Mỗi file ~1-2 trang:
- Tên, link, mục đích chính
- Tính năng relevant cho hệ thống
- Commands/API thường dùng
- Kết nối với 4 repo còn lại

### research/

- Convention: `YYYY-MM-DD-<chủ-đề>.md`
- Note đầu tiên: phân tích ban đầu (lý do chọn 5 repo, ưu nhược, đánh giá memory)
- Experiments template: mục tiêu → cách thực hiện → kết quả → kết luận → bước tiếp

### examples/

3 ví dụ tăng dần độ phức tạp:
1. **basic-crew**: 2 agents CrewAI + backstory từ agency-agents (chạy được)
2. **spec-driven-crew**: Đọc spec file → tạo tasks → agents thực thi (minh họa luồng)
3. **full-pipeline**: 5 thành phần phối hợp (pseudo-working, comment chi tiết)

Mỗi example có README riêng với hướng dẫn chạy.

---

## Yêu cầu hệ thống

- Python 3.11+ (CrewAI)
- Node.js 18+ (OpenSpec)
- Go 1.21+ (Beads)
- Git
- Các repo cần clone:
  - `E:\Work\crewAI`
  - `E:\Work\agency-agents`
  - `E:\Work\beads`
  - `E:\Work\OpenSpec`
  - `E:\Work\superpowers`

---

## Ngoài phạm vi

- Không chứa code gốc của 5 repo (chỉ tham chiếu)
- Không phải production framework (là research hub)
- Không tự động hóa tích hợp (các example là minh họa, không phải tool)
