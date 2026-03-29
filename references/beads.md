# Beads — Distributed Graph Issue Tracker

## Vai tro trong he thong

Beads la he thong luu vet (tracking) cua AI Development System. Theo doi toan bo trang thai cong viec qua dependency graph, audit trail bat bien, va interaction log — cho phep truy vet moi thay doi tu luc tao den luc hoan thanh.

## Tinh nang chinh

- **Dolt-powered SQL database** — Co so du lieu version-controlled, ho tro branch, merge, diff giong Git nhung cho data. Moi thay doi deu co the revert hoac so sanh.
- **Dependency graph** — Ho tro **16+ loai dependency**:
  - `blocks` / `blocked-by` — Task nay chan task kia
  - `parent` / `child` — Quan he phan cap
  - `waits-for` / `waited-by` — Cho doi ket qua
  - `relates-to` — Lien quan nhung khong phu thuoc
  - `duplicates` / `duplicated-by` — Trung lap
  - Va nhieu loai khac...
- **Immutable event audit trail** — Moi thay doi trang thai duoc ghi lai vinh vien, khong the sua doi. Dam bao tinh minh bach va kha nang truy vet.
- **Interaction log** — Ghi lai toan bo LLM calls, tool calls, va ket qua lien quan den moi issue. Huu ich cho debugging va phan tich hieu suat.
- **Statistics** — Thong ke tu dong: lead time, blocked count, cycle time, throughput. Giup do luong hieu qua quy trinh.
- **Compaction** — AI summarization tu dong don dep va tom tat lich su dai, giu nguyen thong tin quan trong.
- **Molecule orchestration** — To chuc nhieu beads thanh molecules voi cac che do: `swarm` (nhieu agent cung xu ly) va `patrol` (agent tuan tra kiem tra).
- **Federation** — Ket noi nhieu Beads instances qua mang, dong bo issues giua cac team.
- **Merge slots** — Quan ly thu tu merge de tranh conflict khi nhieu agents lam viec song song.

## Commands thuong dung

| Command | Mo ta |
|---|---|
| `bd create <title>` | Tao issue moi |
| `bd list` | Liet ke tat ca issues (co bo loc trang thai) |
| `bd ready` | Hien thi cac issues san sang de lam (khong bi block) |
| `bd blocked` | Hien thi cac issues dang bi chan |
| `bd update <id> <field> <value>` | Cap nhat thong tin issue |
| `bd close <id>` | Dong issue khi hoan thanh |
| `bd show <id>` | Xem chi tiet mot issue |
| `bd show --as-of <timestamp>` | Xem trang thai issue tai mot thoi diem cu the trong qua khu |
| `bd dep add <from> <type> <to>` | Them dependency giua hai issues |
| `bd admin stats` | Xem thong ke tong quat (lead time, throughput, v.v.) |

## Ket noi voi cac thanh phan khac

- **OpenSpec** — Specs va tasks tu OpenSpec duoc tao thanh Beads issues. Moi spec artifact co the anh xa sang mot issue voi dependency graph tuong ung.
- **CrewAI** — Khi CrewAI thuc thi tasks, trang thai duoc cap nhat trong Beads (in-progress, blocked, done). Interaction log ghi lai moi LLM call tu CrewAI agents.
- **Superpowers** — Skill verification-before-completion yeu cau kiem tra trang thai Beads truoc khi dong issue. Evidence duoc ghi vao audit trail.
