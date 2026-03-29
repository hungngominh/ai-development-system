# CrewAI — Multi-Agent Orchestration Framework

## Vai tro trong he thong

CrewAI la framework dieu phoi (orchestration) trung tam cua AI Development System. No cho phep dinh nghia, to chuc va chay nhieu AI agent cung lam viec tren mot tap nhiem vu theo quy trinh tuan tu hoac phan cap.

## Tinh nang chinh

- **Agent** — Dinh nghia mot AI agent voi cac thuoc tinh: `role`, `goal`, `backstory`, `llm`. Moi agent dai dien cho mot vai tro chuyen mon cu the.
- **Task** — Mo ta mot nhiem vu can thuc hien, gan cho agent cu the, co `description`, `agent`, `context` (ket qua tu task khac), `expected_output`.
- **Crew** — Nhom cac agent va task lai thanh mot don vi thuc thi. Nhan vao danh sach `agents`, `tasks`, va `process`.
- **Process** — Chien luoc thuc thi: `sequential` (tuan tu, task nay xong moi den task kia) hoac `hierarchical` (co manager agent phan phoi cong viec).
- **Memory** — He thong bo nho thong nhat, co pham vi (scoped), su dung LanceDB de luu tru. Ho tro semantic search va composite scoring de agent nho lai thong tin tu cac lan chay truoc.
- **Flow** — Dieu phoi event-driven, cho phep cac crew kich hoat lan nhau dua tren su kien va ket qua.

## Commands/API thuong dung

| Command / API | Mo ta |
|---|---|
| `Agent(role, goal, backstory, llm)` | Tao mot agent moi voi vai tro, muc tieu, backstory va model LLM |
| `Task(description, agent, context, expected_output)` | Tao mot task, gan cho agent, dinh nghia dau ra mong doi |
| `Crew(agents, tasks, process)` | To chuc agents va tasks thanh mot crew de thuc thi |
| `crew.kickoff()` | Bat dau chay crew — thuc hien tat ca tasks theo process da chon |
| `crew.kickoff(inputs={...})` | Chay crew voi cac bien dau vao dong |
| `Process.sequential` | Chay tasks theo thu tu, output task truoc lam context cho task sau |
| `Process.hierarchical` | Manager agent tu dong phan phoi tasks cho cac agent phu hop |
| `@CrewBase` | Decorator dinh nghia crew class voi cau hinh khai bao |
| `@agent`, `@task`, `@crew` | Decorators dinh nghia agent, task, crew trong crew class |

## Ket noi voi cac thanh phan khac

- **agency-agents** — Noi dung file `.md` cua moi agent trong agency-agents duoc su dung lam `backstory` khi tao CrewAI Agent. Day la nguon chuyen mon cho tung vai tro.
- **Beads** — CrewAI tasks anh xa sang Beads issues de theo doi trang thai (created, in-progress, blocked, done). Ket qua task duoc ghi vao interaction log.
- **OpenSpec** — Specs tu OpenSpec duoc chuyen doi thanh CrewAI tasks. Moi spec artifact (proposal, design, task) co the tro thanh mot Task voi expected_output ro rang.
- **Superpowers** — Cac skill nhu verification-before-completion va test-driven-development hoat dong nhu quality gates trong qua trinh CrewAI thuc thi tasks.
