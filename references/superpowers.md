# Superpowers — 14 Software Development Skills

## Vai tro trong he thong

Superpowers la he thong phuong phap (methodology framework) cua AI Development System. Cung cap 14 skills chuan hoa cach AI agents lam viec, dam bao chat luong va tinh nhat quan xuyen suot toan bo quy trinh phat trien phan mem.

## Tinh nang chinh — 14 Skills

| # | Skill | Mo ta |
|---|---|---|
| 1 | **brainstorming** | Kham pha va tinh chinh thiet ke truoc khi bat tay vao implementation. Dam bao hieu dung yeu cau. |
| 2 | **writing-plans** | Viet ke hoach implementation chi tiet voi duong dan file cu the va code snippets. |
| 3 | **executing-plans** | Thuc thi ke hoach theo batch voi checkpoints kiem tra sau moi buoc. |
| 4 | **subagent-driven-development** | Tao agent moi (fresh context) cho moi task, ket hop two-stage review (self-review + external review). |
| 5 | **test-driven-development** | Bat buoc theo chu trinh RED-GREEN-REFACTOR. Viet test truoc, lam test pass, roi refactor. |
| 6 | **systematic-debugging** | Dieu tra loi theo 4 giai doan co he thong: reproduce, isolate, identify root cause, fix va verify. |
| 7 | **verification-before-completion** | Bat buoc co bang chung (evidence) truoc khi tuyen bo hoan thanh. Chay tests, kiem tra output thuc te. |
| 8 | **requesting-code-review** | Gui code review cho reviewer agent rieng biet, khong tu review chinh minh. |
| 9 | **receiving-code-review** | Tiep nhan feedback voi su nghiem tuc ky thuat. Xac minh goi y truoc khi ap dung, khong dong y mot cach may moc. |
| 10 | **dispatching-parallel-agents** | Phan phoi cac tasks doc lap cho nhieu agents chay song song, tang toc do thuc hien. |
| 11 | **using-git-worktrees** | Su dung git worktrees de tao workspace cach ly cho moi nhanh phat trien. |
| 12 | **finishing-a-development-branch** | Hoan thanh nhanh phat trien voi cac lua chon co cau truc: merge, tao PR, giu lai, hoac huy bo. |
| 13 | **writing-skills** | Tao skills moi de mo rong bo superpowers theo nhu cau du an. |
| 14 | **using-superpowers** | Tim va goi skill phu hop. Diem khoi dau cho moi cuoc hoi thoai. |

## Triet ly cot loi

- **"Evidence over claims"** — Khong bao gio tuyen bo "xong" ma khong co bang chung cu the (test pass, output dung, screenshot).
- **Systematic over ad-hoc** — Luon theo quy trinh co he thong thay vi lam tuy hung. Moi buoc co ly do va kiem chung.
- **YAGNI** (You Aren't Gonna Need It) — Khong xay dung tinh nang chua can. Chi lam nhung gi duoc yeu cau.
- **DRY** (Don't Repeat Yourself) — Tranh lap lai code va logic. Tai su dung toi da.

## Cach su dung

| Thao tac | Mo ta |
|---|---|
| Goi skill trong conversation | Su dung ten skill de kich hoat (vd: `/superpowers:brainstorming`) |
| Ket hop nhieu skills | Mot workflow thuong dung nhieu skills: brainstorming -> writing-plans -> executing-plans -> verification |
| Tuy chinh workflow | Chon skills phu hop voi tung loai cong viec (bug fix dung systematic-debugging, feature moi dung TDD) |

## Ket noi voi cac thanh phan khac

- **CrewAI** — Superpowers hoat dong nhu quality gates trong qua trinh CrewAI thuc thi. Vi du: verification-before-completion dam bao moi task thuc su hoan thanh truoc khi bao done. TDD dam bao code co tests.
- **OpenSpec** — Skill verification-before-completion kiem tra spec compliance qua `/opsx:verify`. Brainstorming ho tro qua trinh tao proposals chat luong hon.
- **Beads** — Verification tracking duoc ghi vao Beads audit trail. Evidence tu verification-before-completion luu trong interaction log. Trang thai issue chi chuyen sang done khi co bang chung.
