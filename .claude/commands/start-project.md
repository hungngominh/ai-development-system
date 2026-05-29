---
name: start-project
description: >
  Phase 1a entry point. Khởi động intake wizard (thu thập ~30 trường brief
  qua interactive Python CLI), hỗ trợ resume run đang COLLECTING_INTAKE,
  và hướng dẫn user sang bước debate sau khi brief promoted.
---

# Start Project Skill

Invoke: `/start-project`, `/start-project <project-name>`, hoặc
`/start-project --run-id <uuid>` (resume).

The intake wizard tự nó là **interactive Python CLI** — hỏi từng câu một,
nhận stdin từ user trong terminal. Skill này KHÔNG chạy wizard trực tiếp;
nó là **launcher/dispatcher** in lệnh chính xác cho user, sau đó xác minh
trạng thái run trong DB.

## State Machine

```
PARSE_ARGS → (DETECT_EXISTING | COLLECT_PROJECT_NAME) → LAUNCH → VERIFY → NEXT_STEPS
                                                                        ↘ PAUSED_HINT
```

## PARSE_ARGS

Đọc các argument sau dấu `/start-project`:

- `--run-id <uuid>` → mode = `resume`, set `run_id` = uuid.
- Một chuỗi đơn (không bắt đầu bằng `--`) → mode = `start`, set
  `project_name` = chuỗi đó.
- Không có gì → mode = `start`, `project_name` chưa biết.

## DETECT_EXISTING (mode=resume)

Verify run tồn tại và resumable:

```bash
ai-dev intake show --run-id <run_id> --json
```

Parse JSON. Nếu `status` = `intake_in_progress` → tiếp tục sang LAUNCH (resume).
Nếu `intake_complete` → báo user "run này đã promoted brief rồi" + hiển thị
brief_id từ `--json`, kết thúc skill.
Lỗi/không tìm thấy → báo lỗi + đề xuất chạy `/start-project` không có `--run-id`.

## COLLECT_PROJECT_NAME (mode=start, project_name unknown)

Hỏi:

> *"Tên project? (slug ngắn, dùng để nhóm các run liên quan, vd: 'forum-kien-thuc')"*

Accept một dòng, strip whitespace. Nếu rỗng → hỏi lại. KHÔNG tự sinh slug —
CLI sẽ chuẩn hoá.

## LAUNCH

In ra lệnh chính xác user phải chạy **trong terminal riêng** (KHÔNG chạy
qua Bash tool — wizard cần stdin tương tác):

**Mode = start:**

````
Mở terminal khác và chạy:

    ai-dev intake start --project-name "<project_name>"

Wizard sẽ hỏi ~30 câu (15–30 phút). Một số command hữu ích trong wizard:
  skip   bỏ qua câu hiện tại (critical fields sẽ thành assumption)
  back   quay câu trước
  save   tạm dừng, resume sau bằng `/start-project --run-id <run_id>`
  show   xem brief hiện tại
  ?      yêu cầu AI đề xuất (cho field non-sensitive)

Khi xong (hoặc tạm dừng), quay lại đây và gõ `done` để tôi kiểm tra trạng thái.
````

**Mode = resume:**

````
Mở terminal khác và chạy:

    ai-dev intake resume --run-id <run_id>

Wizard sẽ tiếp tục từ field bạn đã pause. Khi xong, quay lại đây và gõ
`done`.
````

Đợi user trả lời. Accept: `done`, `xong`, `ok`, hoặc bất cứ tín hiệu hoàn
thành nào. Cũng accept `huỷ` / `abort` → in lệnh `ai-dev intake abort --run-id
<run_id>` (nếu biết run_id) hoặc kết thúc skill.

## VERIFY

Sau khi user báo done, cần xác định run_id. Trong mode=resume đã có sẵn.
Trong mode=start, user chưa cho run_id — query DB qua một `python -c` block
(không có dedicated `ai-dev intake list` command trong slice này):

```bash
python -c "
import json
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
conn = get_connection(Config.from_env().database_url)
rows = conn.execute(
    '''SELECT run_id, status, intake_brief_id, last_activity_at
       FROM runs
       WHERE project_id = ? AND status IN ('COLLECTING_INTAKE','READY_FOR_DEBATE','ABORTED')
       ORDER BY last_activity_at DESC LIMIT 1''',
    ('<project_name>',)
).fetchall()
print(json.dumps([dict(r) for r in rows]))
"
```

> Note: `project_id` trên CLI hiện slug-hoá từ `--project-name` (xem
> `intake.py:_slugify`). Nếu user nhập tên có dấu/khoảng trắng, slug-hoá
> lại trước khi query (`lower()`, non-alphanumeric → `-`, strip `-`).

Parse JSON row. Theo `status`:

- `READY_FOR_DEBATE` + `intake_brief_id` non-null → wizard hoàn tất, sang
  NEXT_STEPS.
- `COLLECTING_INTAKE` → wizard bị pause (user gõ `save`). Sang PAUSED_HINT.
- `ABORTED` → user đã abort. Báo "đã hủy", kết thúc.
- Không có row → user chưa chạy wizard / chưa save. Lặp lại LAUNCH instructions.

## NEXT_STEPS (status = READY_FOR_DEBATE)

In:

````
✅ Intake hoàn tất.
   Run ID    : <run_id>
   Brief ID  : <intake_brief_id>

Bước tiếp theo: chạy debate trên brief này.

    # (Phase 1b debate v2 chưa wire xong — tạm thời dùng nhánh legacy
    #  nếu cần debate ngay. Plan S7 sẽ kết nối finalize_spec với brief v2.)

Khi debate xong, dùng `/review-debate --run-id <run_id>` để Gate 1.
````

> Nếu sau S7 đã có `ai-dev phase-b debate --run-id`, in lệnh đó thay vì
> chú thích "chưa wire". Cập nhật skill này khi S7 land.

## PAUSED_HINT (status = COLLECTING_INTAKE)

In:

````
💾 Wizard đã pause sau khi bạn gõ `save`. Resume bằng:

    /start-project --run-id <run_id>

hoặc trực tiếp:

    ai-dev intake resume --run-id <run_id>
````

Kết thúc skill.

## Error Handling

- DB không kết nối được → in `python -c` traceback và hướng dẫn user kiểm
  tra `AI_DEV_DATABASE_URL`.
- `ai-dev` không có trong PATH → đề xuất `python -m ai_dev_system.cli.main`
  thay thế.
- User quay lại không gõ done (gõ câu hỏi khác) → trả lời câu hỏi bình
  thường, vẫn nhớ rằng skill đang chờ user chạy wizard.
