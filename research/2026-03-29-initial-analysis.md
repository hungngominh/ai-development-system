# Phân tích ban đầu: Hệ thống AI Development 5 thành phần

Ngày: 2026-03-29

## Bối cảnh
We have 5 open-source repos and want to understand how they complement each other for building a complete AI development system.

## Tại sao chọn 5 repo này

Each repo fills a specific gap:

### agency-agents
- **Ưu:** 100+ agent prompts chuyên biệt, multi-tool support, community-driven
- **Nhược:** Chỉ là prompt, không có orchestration, không có memory
- **Vai trò:** Cung cấp "chuyên môn" cho AI agents

### CrewAI
- **Ưu:** Multi-agent orchestration, unified memory (LanceDB), sequential + hierarchical processes, tool use
- **Nhược:** Agent prompts generic, không có spec workflow, không có persistent task tracking
- **Vai trò:** Điều phối agents, quản lý memory

### OpenSpec
- **Ưu:** Spec-driven development bắt buộc "nghĩ trước code sau", validation nghiêm ngặt, 20+ tool support
- **Nhược:** Không có execution engine, không track tasks, không có memory
- **Vai trò:** Đảm bảo quy trình

### Superpowers
- **Ưu:** 14 skills bao phủ toàn bộ dev lifecycle, "evidence over claims", TDD bắt buộc
- **Nhược:** Chỉ là skills/processes, cần host tool để chạy, không có persistent storage
- **Vai trò:** Đảm bảo phương pháp và chất lượng

### Beads
- **Ưu:** Dolt-powered persistent storage, dependency graph, audit trail, compaction, version history
- **Nhược:** Không có AI execution, không có agent prompts, không có spec workflow
- **Vai trò:** Lưu vết và báo cáo

## Cách chúng bổ sung cho nhau

| Khả năng | agency-agents | CrewAI | OpenSpec | Superpowers | Beads |
|---|---|---|---|---|---|
| Agent prompts | ✅ | ❌ | ❌ | ❌ | ❌ |
| Orchestration | ❌ | ✅ | ❌ | ❌ | ❌ |
| Spec workflow | ❌ | ❌ | ✅ | ❌ | ❌ |
| Quality gates | ❌ | ❌ | ❌ | ✅ | ❌ |
| Task tracking | ❌ | ❌ | ❌ | ❌ | ✅ |
| Memory | ❌ | ✅ | ❌ | ❌ | ✅ |
| Audit trail | ❌ | ❌ | ✅ (archive) | ❌ | ✅ |

## Đánh giá trí nhớ AI

**Score: 7/10**

- **Cross-session:** ✅ (Beads + OpenSpec + CrewAI Memory)
- **Cross-agent:** ✅ (CrewAI scoped memory + Beads)
- **Long-term:** ✅ (Dolt + LanceDB + file specs)
- **Intra-session:** ⚠️ (context window physical limit)

See `docs/memory-analysis.md` for full analysis.

## Câu hỏi mở
1. Cách tốt nhất để tích hợp Beads task tracking vào CrewAI execution loop?
2. Có thể dùng OpenSpec validation để auto-verify CrewAI output?
3. Superpowers skills có thể inject vào CrewAI agent backstory?
4. Làm sao giải quyết intra-session memory overflow?
