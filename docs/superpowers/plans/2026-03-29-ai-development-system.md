# AI Development System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tạo research hub tài liệu hóa hệ thống AI development 5 thành phần, phục vụ chia sẻ kiến thức cho team.

**Architecture:** Monorepo tham chiếu — chỉ chứa docs, references, research notes và code mẫu. Không duplicate source từ 5 repo gốc. Ngôn ngữ tiếng Việt, thuật ngữ kỹ thuật giữ tiếng Anh.

**Tech Stack:** Markdown, Mermaid diagrams, Python (examples)

**Spec:** `docs/superpowers/specs/2026-03-29-ai-development-system-design.md`

---

## File Structure

```
ai-development-system/
├── README.md                              # Trang chủ: tổng quan, quick start, mục lục
├── CHANGELOG.md                           # Lịch sử thay đổi
├── .gitignore                             # Ignore patterns
│
├── docs/
│   ├── architecture.md                    # Kiến trúc 5 thành phần
│   ├── integration-guide.md               # Hướng dẫn tích hợp 5 bước
│   ├── memory-analysis.md                 # Phân tích 4 tầng trí nhớ AI
│   ├── workflow.md                        # Luồng end-to-end với ví dụ forum
│   └── diagrams/
│       ├── system-overview.md             # Mermaid: tổng thể
│       ├── data-flow.md                   # Mermaid: luồng dữ liệu
│       └── memory-layers.md              # Mermaid: 4 tầng memory
│
├── references/
│   ├── crewai.md
│   ├── agency-agents.md
│   ├── beads.md
│   ├── openspec.md
│   └── superpowers.md
│
├── research/
│   ├── README.md                          # Convention viết notes
│   ├── 2026-03-29-initial-analysis.md     # Phân tích ban đầu
│   └── experiments/
│       └── README.md                      # Template thí nghiệm
│
└── examples/
    ├── README.md                          # Hướng dẫn chạy examples
    ├── 01-basic-crew/
    │   ├── README.md
    │   └── crew_with_agents.py
    ├── 02-spec-driven-crew/
    │   ├── README.md
    │   └── spec_to_crew.py
    └── 03-full-pipeline/
        ├── README.md
        └── full_pipeline.py
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `.gitignore`
- Create: `CHANGELOG.md`

- [ ] **Step 1: Tạo .gitignore**

```gitignore
__pycache__/
*.pyc
.env
.venv/
node_modules/
.DS_Store
Thumbs.db
*.swp
*.swo
```

- [ ] **Step 2: Tạo CHANGELOG.md**

```markdown
# Changelog

Định dạng dựa trên [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/).

## [Unreleased]

### Thêm mới
- Khởi tạo dự án research hub
- Design spec cho hệ thống AI development 5 thành phần
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore CHANGELOG.md
git commit -m "Khởi tạo project: .gitignore và CHANGELOG"
```

---

## Task 2: README.md — Trang chủ

**Files:**
- Create: `README.md`

- [ ] **Step 1: Viết README.md**

Nội dung bao gồm:
1. Tiêu đề + mô tả ngắn
2. Bảng 5 thành phần với analogy:
   - `agency-agents` = Chuyên môn (biết gì, làm gì)
   - `CrewAI` = Điều phối (ai làm trước, truyền output)
   - `OpenSpec` = Quy trình (spec trước code sau)
   - `Superpowers` = Phương pháp (TDD, review, verification)
   - `Beads` = Lưu vết (tracking, audit trail, báo cáo)
3. Mermaid diagram tổng quan hệ thống
4. Quick Start: clone hub → clone 5 repos → đọc integration guide → chạy example
5. Mục lục link đến docs/, references/, research/, examples/
6. Yêu cầu hệ thống: Python >=3.10, Node.js 18+, Go 1.25+, Git

- [ ] **Step 2: Verify links trong README trỏ đúng path**

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Thêm README: tổng quan hệ thống, quick start, mục lục"
```

---

## Task 3: docs/diagrams/ — Sơ đồ Mermaid

**Files:**
- Create: `docs/diagrams/system-overview.md`
- Create: `docs/diagrams/data-flow.md`
- Create: `docs/diagrams/memory-layers.md`

- [ ] **Step 1: Tạo system-overview.md**

Mermaid graph TD hiển thị 5 thành phần + mối quan hệ chính.
Bao gồm: Input → OpenSpec → Beads → CrewAI ↔ agency-agents → Superpowers → Output.

- [ ] **Step 2: Tạo data-flow.md**

Mermaid diagram chi tiết hơn system-overview: hiển thị Superpowers tham gia mọi giai đoạn, Beads theo dõi xuyên suốt. Không đơn giản hóa như sơ đồ trong spec.

- [ ] **Step 3: Tạo memory-layers.md**

Mermaid diagram 4 tầng memory:
- Tầng 1: Context window → Beads compaction
- Tầng 2: Cross-session → Beads DB + OpenSpec specs + CrewAI Memory
- Tầng 3: Cross-agent → CrewAI scoped memory + Beads dependency
- Tầng 4: Dài hạn → Dolt + LanceDB + file specs

- [ ] **Step 4: Verify Mermaid syntax hợp lệ**

- [ ] **Step 5: Commit**

```bash
git add docs/diagrams/
git commit -m "Thêm sơ đồ Mermaid: system overview, data flow, memory layers"
```

---

## Task 4: docs/architecture.md — Kiến trúc tổng thể

**Files:**
- Create: `docs/architecture.md`

- [ ] **Step 1: Viết architecture.md**

Nội dung:
1. Tổng quan kiến trúc (link đến diagrams/system-overview.md)
2. Vai trò từng thành phần (tóm tắt, link đến references/ cho chi tiết)
3. Bảng mapping vấn đề → giải pháp:
   - "AI không có chuyên môn sâu" → agency-agents
   - "Agent không phối hợp được" → CrewAI
   - "Code trước nghĩ sau" → OpenSpec
   - "Không kiểm soát chất lượng" → Superpowers
   - "Mất dấu vết công việc" → Beads
4. Giới hạn đã biết (3 lỗ hổng memory + các giới hạn khác)

- [ ] **Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "Thêm tài liệu kiến trúc: 5 thành phần, mapping vấn đề, giới hạn"
```

---

## Task 5: docs/integration-guide.md — Hướng dẫn tích hợp

**Files:**
- Create: `docs/integration-guide.md`

- [ ] **Step 1: Viết integration-guide.md**

5 bước tích hợp, mỗi bước có:
- Mục đích
- Công cụ sử dụng
- Lệnh cụ thể
- Output mong đợi
- Link đến reference tương ứng

Bước 1: OpenSpec → `/opsx:propose`, `/opsx:new`
Bước 2: Beads → `bd create`, `bd dep add`
Bước 3: CrewAI + agency-agents → Python script mẫu
Bước 4: Superpowers → brainstorming, TDD, code review
Bước 5: Beads → `bd update`, `bd close`, `bd admin stats`

- [ ] **Step 2: Commit**

```bash
git add docs/integration-guide.md
git commit -m "Thêm hướng dẫn tích hợp 5 bước với lệnh cụ thể"
```

---

## Task 6: docs/memory-analysis.md — Phân tích trí nhớ AI

**Files:**
- Create: `docs/memory-analysis.md`

- [ ] **Step 1: Viết memory-analysis.md**

Nội dung từ phân tích trong hội thoại:
1. Vấn đề: AI dễ "mất trí nhớ" ở 4 tầng
2. Bảng đánh giá từng tầng (giải pháp hiện có, mức độ giải quyết)
3. Chi tiết cách mỗi repo đóng góp:
   - CrewAI Memory: unified memory, semantic search, scoped, composite scoring
   - Beads: persistent Dolt DB, audit trail, compaction
   - OpenSpec: specs trên disk, archive history
4. 3 lỗ hổng còn lại + hướng nghiên cứu tiềm năng
5. Đánh giá tổng: 7/10
6. Link đến diagrams/memory-layers.md

- [ ] **Step 2: Commit**

```bash
git add docs/memory-analysis.md
git commit -m "Thêm phân tích 4 tầng trí nhớ AI: giải pháp và lỗ hổng"
```

---

## Task 7: docs/workflow.md — Luồng end-to-end

**Files:**
- Create: `docs/workflow.md`

- [ ] **Step 1: Viết workflow.md**

Ví dụ cụ thể "Xây forum chia sẻ kiến thức":
1. Yêu cầu đầu vào
2. OpenSpec: proposal → specs → design → tasks
3. Beads: tạo task graph với dependencies
4. CrewAI: Product Manager → Backend Architect → Frontend Developer
5. Superpowers: TDD mỗi bước, code review, verification
6. Beads: close tasks, audit trail, `bd admin stats`
7. Mermaid sequence diagram cho toàn bộ luồng
8. Output mong đợi ở mỗi giai đoạn

- [ ] **Step 2: Commit**

```bash
git add docs/workflow.md
git commit -m "Thêm workflow end-to-end: ví dụ xây forum chia sẻ kiến thức"
```

---

## Task 8: references/ — 5 thẻ tham chiếu

**Files:**
- Create: `references/crewai.md`
- Create: `references/agency-agents.md`
- Create: `references/beads.md`
- Create: `references/openspec.md`
- Create: `references/superpowers.md`

- [ ] **Step 1: Viết crewai.md**

Vai trò trong hệ thống, tính năng chính (Agent, Task, Crew, Process, Memory, Flow), commands thường dùng, kết nối với 4 repo.

- [ ] **Step 2: Viết agency-agents.md**

Vai trò, cấu trúc agent file (frontmatter + sections), danh mục divisions, cách dùng với CrewAI (backstory), multi-tool support.

- [ ] **Step 3: Viết beads.md**

Vai trò, commands thường dùng (create, list, ready, blocked, update, close, admin stats, show), audit trail, dependency types, kết nối.

- [ ] **Step 4: Viết openspec.md**

Vai trò, workflow (propose → apply → archive), artifact graph, validation rules, commands thường dùng, kết nối.

- [ ] **Step 5: Viết superpowers.md**

Vai trò, danh sách 14 skills với mô tả 1 dòng, skill quan trọng nhất cho hệ thống, cách kích hoạt, kết nối.

- [ ] **Step 6: Commit**

```bash
git add references/
git commit -m "Thêm 5 thẻ tham chiếu: CrewAI, agency-agents, Beads, OpenSpec, Superpowers"
```

---

## Task 9: research/ — Ghi chú nghiên cứu

**Files:**
- Create: `research/README.md`
- Create: `research/2026-03-29-initial-analysis.md`
- Create: `research/experiments/README.md`

- [ ] **Step 1: Viết research/README.md**

Convention:
- File name: `YYYY-MM-DD-<chủ-đề>.md`
- Mỗi note có: tiêu đề, ngày, context, nội dung chính, kết luận, câu hỏi mở
- Khuyến khích đồng nghiệp đóng góp notes

- [ ] **Step 2: Viết 2026-03-29-initial-analysis.md**

Ghi lại từ hội thoại hôm nay:
- Tại sao chọn 5 repo này
- Phân tích ưu nhược từng repo
- Cách chúng bổ sung cho nhau
- Đánh giá memory 7/10
- Câu hỏi mở cho nghiên cứu tiếp

- [ ] **Step 3: Viết research/experiments/README.md**

Template:
```
# <Tên thí nghiệm>
Ngày: YYYY-MM-DD

## Mục tiêu
## Cách thực hiện
## Kết quả
## Kết luận
## Bước tiếp theo
```

- [ ] **Step 4: Commit**

```bash
git add research/
git commit -m "Thêm research: convention, phân tích ban đầu, template thí nghiệm"
```

---

## Task 10: examples/ — Code mẫu

**Files:**
- Create: `examples/README.md`
- Create: `examples/01-basic-crew/README.md`
- Create: `examples/01-basic-crew/crew_with_agents.py`
- Create: `examples/02-spec-driven-crew/README.md`
- Create: `examples/02-spec-driven-crew/spec_to_crew.py`
- Create: `examples/03-full-pipeline/README.md`
- Create: `examples/03-full-pipeline/full_pipeline.py`

- [ ] **Step 1: Viết examples/README.md**

Tổng quan 3 examples, yêu cầu cài đặt (Python, crewai package, các CLI tools), hướng dẫn chung.

- [ ] **Step 2: Viết 01-basic-crew/**

README + Python script:
- 2 CrewAI agents (Product Manager, Backend Architect)
- Backstory đọc từ file agency-agents (relative path `../../agency-agents/`)
- 1 task đơn giản: viết PRD cho forum
- Sequential process
- Comment tiếng Việt giải thích từng bước

- [ ] **Step 3: Viết 02-spec-driven-crew/**

README + Python script:
- Đọc spec file OpenSpec (markdown) → parse requirements
- Tạo CrewAI tasks từ requirements
- Agents thực thi theo spec
- Comment giải thích luồng OpenSpec → CrewAI

- [ ] **Step 4: Viết 03-full-pipeline/**

README + Python script:
- Pseudo-working pipeline 5 thành phần
- Gọi OpenSpec CLI (subprocess) → tạo spec
- Gọi Beads CLI (subprocess) → tạo tasks
- CrewAI + agency-agents → thực thi
- Superpowers concepts → quality check (simulated)
- Comment chi tiết từng giai đoạn
- Ghi rõ: đây là minh họa, cần adapt cho production

- [ ] **Step 5: Commit**

```bash
git add examples/
git commit -m "Thêm 3 code examples: basic crew, spec-driven, full pipeline"
```

---

## Task 11: Final review và push

- [ ] **Step 1: Review toàn bộ links trong README.md**

Đảm bảo mọi link relative path trỏ đúng file.

- [ ] **Step 2: Review CHANGELOG.md**

Cập nhật danh sách đầy đủ những gì đã thêm.

- [ ] **Step 3: Commit CHANGELOG update**

```bash
git add CHANGELOG.md
git commit -m "Cập nhật CHANGELOG: liệt kê toàn bộ nội dung v0.1.0"
```

- [ ] **Step 4: Thêm remote và push**

```bash
git remote add origin https://github.com/hungngominh/ai-development-system.git
git branch -M main
git push -u origin main
```
