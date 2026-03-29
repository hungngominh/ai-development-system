# Kiến trúc hệ thống

## Tổng quan

Hệ thống AI Development System gồm 5 thành phần chính, mỗi thành phần đảm nhận một vai trò cụ thể trong quy trình phát triển phần mềm với sự hỗ trợ của AI. Các thành phần được thiết kế để bổ sung lẫn nhau: từ việc cung cấp chuyên môn cho AI agent, điều phối chúng làm việc cùng nhau, đảm bảo quy trình chặt chẽ, kiểm soát chất lượng, đến lưu trữ toàn bộ lịch sử làm việc.

Xem sơ đồ tổng quan tại [docs/diagrams/system-overview.md](diagrams/system-overview.md).

## 5 thành phần

### 1. agency-agents — Chuyên môn

agency-agents cung cấp thư viện prompt chuyên biệt cho các AI agent, được tổ chức theo division: engineering, design, marketing, sales, và nhiều lĩnh vực khác. Mỗi file agent định nghĩa đầy đủ identity, mission, capabilities, workflow cụ thể, và deliverables mong đợi. Các prompt này được sử dụng làm backstory input khi khởi tạo CrewAI agent, giúp mỗi agent có "chuyên môn sâu" thay vì chỉ là một LLM chung chung. Nhờ đó, mỗi agent hiểu rõ vai trò của mình và biết cần làm gì trong từng tình huống cụ thể.

Chi tiết: [references/agency-agents.md](../references/agency-agents.md)

### 2. CrewAI — Điều phối

CrewAI là Python framework chịu trách nhiệm orchestration nhiều agent cùng làm việc trên một dự án. Framework hỗ trợ cả sequential process (agent làm tuần tự) và hierarchical process (có manager agent phân công). Hệ thống memory thống nhất sử dụng LanceDB với semantic search, scoped memory theo agent/task, và composite scoring để truy xuất thông tin liên quan nhất. Các agent giao tiếp với nhau thông qua cơ chế task context passing — output của agent trước trở thành input cho agent sau.

Chi tiết: [references/crewai.md](../references/crewai.md)

### 3. OpenSpec — Quy trình

OpenSpec là framework phát triển theo spec-driven approach, bắt buộc "nghĩ trước khi code". Quy trình đi theo artifact graph rõ ràng: proposal → specs (yêu cầu MUST/SHALL với Given/When/Then scenario) → design → tasks. Trước khi bắt đầu implementation, hệ thống validate toàn bộ specs để đảm bảo không có liên kết thiếu hoặc yêu cầu mâu thuẫn. Mỗi thay đổi được archive kèm timestamp để đảm bảo traceability.

Chi tiết: [references/openspec.md](../references/openspec.md)

### 4. Superpowers — Phương pháp

Superpowers gồm 14 skill bao phủ toàn bộ vòng đời phát triển phần mềm: brainstorming, planning, TDD, code review, systematic debugging, verification-before-completion, parallel dispatch, git worktrees, và nhiều kỹ năng khác. Triết lý cốt lõi là "evidence over claims" — mọi khẳng định phải có bằng chứng cụ thể từ kết quả thực thi, không chấp nhận việc tuyên bố "đã xong" mà không chứng minh. Các skill hoạt động như guardrails, đảm bảo AI agent tuân thủ quy trình chất lượng.

Chi tiết: [references/superpowers.md](../references/superpowers.md)

### 5. Beads — Lưu vết

Beads là distributed graph issue tracker xây trên Dolt (Git cho database), chuyên theo dõi toàn bộ công việc trong hệ thống. Task graph có dependency-aware giúp hiểu rõ task nào phụ thuộc task nào, từ đó xác định thứ tự thực hiện và phát hiện bottleneck. Mọi event được ghi vào immutable audit trail, kết hợp interaction log (LLM calls, tool calls) để biết chính xác AI đã làm gì. Hệ thống cung cấp statistics (lead time, blocked count) và hỗ trợ `--as-of` để xem trạng thái tại bất kỳ thời điểm nào trong quá khứ.

Chi tiết: [references/beads.md](../references/beads.md)

## Bảng mapping vấn đề → giải pháp

| Vấn đề | Thành phần | Cách giải quyết |
|---|---|---|
| AI không có chuyên môn sâu | agency-agents | Prompt chuyên biệt với workflow và deliverables cụ thể |
| Các agent không phối hợp được | CrewAI | Orchestration tự động, context passing, shared memory |
| Code trước nghĩ sau | OpenSpec | Spec validation bắt buộc trước khi code |
| Không kiểm soát chất lượng | Superpowers | TDD bắt buộc, code review tự động, verification |
| Mất dấu vết công việc | Beads | Audit trail bất biến, dependency graph, thống kê |
| AI mất trí nhớ | CrewAI Memory + Beads + OpenSpec | 3 tầng persistent storage |

## Giới hạn đã biết

1. **Intra-session memory**: Context window là giới hạn vật lý của LLM. Khi conversation vượt quá kích thước context, thông tin cũ bị mất. Các cơ chế memory (LanceDB, Beads) giảm thiểu vấn đề này nhưng chưa giải quyết triệt để vì việc retrieve đúng thông tin cần thiết vẫn là bài toán khó.

2. **Handoff quality**: Output giữa các agent có thể mất implicit knowledge — những hiểu biết ngầm không được ghi rõ trong output. Agent sau chỉ nhận được những gì agent trước viết ra, không phải toàn bộ reasoning process.

3. **Memory accuracy**: Hệ thống không tự động validate xem memory cũ còn đúng không. Thông tin đã lưu có thể trở nên outdated khi codebase thay đổi, nhưng vẫn được retrieve và sử dụng như thể còn chính xác.
