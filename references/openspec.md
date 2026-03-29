# OpenSpec — Spec-Driven Development

## Vai tro trong he thong

OpenSpec la he thong quy trinh (process engine) cua AI Development System. Dinh nghia va quan ly toan bo vong doi phat trien tu y tuong (proposal) den thiet ke (design) den nhiem vu (tasks), dam bao moi cong viec deu bat nguon tu specs ro rang.

## Tinh nang chinh

- **Artifact graph** — Chuoi san pham co cau truc: `proposal` (y tuong) -> `specs` (dac ta chi tiet) -> `design` (thiet ke ky thuat) -> `tasks` (nhiem vu cu the). Moi artifact lien ket voi nhau tao thanh do thi truy xuat nguon goc.
- **RFC 2119 keywords** — Su dung `MUST`, `SHALL`, `SHOULD`, `MAY` de phan biet yeu cau bat buoc va tuy chon. Dam bao moi nguoi hieu dung muc do quan trong.
- **Given/When/Then scenarios** — Dinh nghia hanh vi mong doi bang format BDD (Behavior-Driven Development). Moi scenario la mot test case co the kiem chung.
- **Validation engine** — Tu dong kiem tra tinh hop le cua specs: cau truc dung, keywords su dung chinh xac, scenarios day du, khong co conflict.
- **Delta-based changes** — Moi thay doi duoc danh dau ro rang: `ADDED` (them moi), `MODIFIED` (sua doi), `REMOVED` (xoa bo). Giup review nhanh va chinh xac.
- **Archive voi timestamp** — Moi phien ban spec duoc luu tru voi timestamp, cho phep quay lai bat ky thoi diem nao.
- **20+ AI tool integrations** — Hoat dong voi nhieu AI tools: Claude Code, Cursor, Aider, Windsurf, Gemini CLI, va nhieu tool khac.
- **Custom schemas** — Ho tro dinh nghia schema rieng cho tung du an, mo rong cau truc artifact theo nhu cau.

## Commands thuong dung

| Command | Mo ta |
|---|---|
| `openspec init` | Khoi tao OpenSpec trong du an, tao cau truc thu muc `.openspec/` |
| `/opsx:propose` | Tao proposal moi — mo ta y tuong va muc tieu |
| `/opsx:new` | Tao spec moi tu proposal da duyet |
| `/opsx:continue` | Tiep tuc lam viec tren spec hien tai (them chi tiet, sua doi) |
| `/opsx:ff` | Fast-forward — cap nhat spec len phien ban moi nhat |
| `/opsx:apply` | Ap dung spec vao code — tao tasks va bat dau implementation |
| `/opsx:verify` | Kiem tra implementation co phu hop voi spec khong |
| `/opsx:archive` | Luu tru phien ban hien tai cua spec voi timestamp |
| `openspec validate` | Chay validation engine — kiem tra cau truc va tinh hop le cua tat ca specs |

## Ket noi voi cac thanh phan khac

- **Beads** — Moi task trong OpenSpec artifact graph duoc tao thanh Beads issue. Dependency giua specs anh xa sang dependency graph trong Beads.
- **CrewAI** — Specs duoc chuyen thanh CrewAI tasks voi `description` tu spec content va `expected_output` tu Given/When/Then scenarios. Proposal co the dinh nghia ca crew structure.
- **Superpowers** — Validation engine cua OpenSpec hoat dong nhu quality gate. Skill verification-before-completion yeu cau `/opsx:verify` pass truoc khi dong task.
