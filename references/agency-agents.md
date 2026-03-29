# agency-agents — Specialized AI Agent Prompts

## Vai tro trong he thong

agency-agents la kho chuyen mon (expertise library) cua AI Development System. Cung cap hon 100 agent prompts chuyen biet, duoc to chuc theo division, san sang su dung lam backstory cho CrewAI hoac cai dat truc tiep vao cac AI tools.

## Tinh nang chinh

- **100+ agents** phan bo tren **13+ divisions**:
  - **Engineering** — Software engineers, architects, DevOps, database specialists
  - **Design** — UI/UX designers, design system architects
  - **Marketing** — Content strategists, SEO specialists, social media managers
  - **Sales** — Sales engineers, account managers
  - **Product** — Product managers, business analysts
  - **Testing** — QA engineers, automation specialists, performance testers
  - **Support** — Technical support, customer success
  - **Spatial Computing** — AR/VR developers, 3D engineers
  - **Game Dev** — Game designers, game programmers
  - **Academic** — Research analysts, technical writers
  - **Paid Media** — Ad specialists, campaign managers
  - **Specialized** — Domain-specific experts (legal tech, healthcare, fintech...)
  - **Project Management** — Scrum masters, project coordinators

- **Cau truc moi agent**: identity (ten, vai tro), mission (muc tieu chinh), capabilities (ky nang cu the), workflow (quy trinh lam viec), deliverables (san pham dau ra).

- **Multi-tool support** — Moi agent co the duoc su dung voi: Claude Code, Cursor, Aider, Windsurf, Gemini CLI, Kimi.

- **Scripts tien ich**:
  - `convert.sh` — Chuyen doi dinh dang agent giua cac AI tools
  - `install.sh` — Cai dat agent vao tool cu the (vd: copy vao `.cursorrules`)

## Commands/API thuong dung

| Thao tac | Mo ta |
|---|---|
| Doc file `.md` cua agent | Lay noi dung lam backstory cho CrewAI Agent |
| `./scripts/convert.sh <agent> <tool>` | Chuyen doi agent prompt sang dinh dang cua tool cu the |
| `./scripts/install.sh <agent> <tool>` | Cai dat agent vao AI tool (Cursor, Aider, v.v.) |
| Duyet theo division | Tim agent phu hop trong thu muc `agents/<division>/` |
| Copy backstory vao `Agent(backstory=...)` | Su dung truc tiep trong code CrewAI |

## Ket noi voi cac thanh phan khac

- **CrewAI** — Moi agent `.md` la nguon `backstory` khi tao CrewAI Agent. Vai tro va capabilities cua agent dinh huong cach CrewAI phan cong tasks.
- **Superpowers** — Cac skill nhu subagent-driven-development va requesting-code-review anh huong den chat luong output cua agent. Agent prompts co the tham chieu superpowers de dam bao tuan thu quy trinh.
- **OpenSpec** — Agent thuc hien cong viec theo specs tu OpenSpec. Deliverables cua agent can phu hop voi expected output trong spec.
