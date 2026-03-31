# Artifact Storage Contract

Tài liệu này define contract giữa control layer (DB) và file system (content).
Mọi component đọc/ghi artifact PHẢI tuân thủ tài liệu này.

---

## Nguyên tắc nền tảng

1. **File trước, DB sau** — artifact không bao giờ tồn tại trong DB mà không có file tương ứng
2. **Immutable once written** — không overwrite, không rename artifact đã promote
3. **Deterministic paths** — path sinh từ metadata, không phụ thuộc runtime state
4. **Checksum là source of truth về integrity** — không tin path một mình
5. **Two-phase move** — không bao giờ move trực tiếp vào final path; luôn qua staging (`.staging`)
6. **Version locking** — `get_next_version()` phải chạy trong transaction với `SELECT FOR UPDATE`
7. **Promotion idempotency** — chỉ một worker được promote mỗi task; guard bằng `output_artifact_id IS NULL`

---

## 1. Path Convention

### 1.1 Artifact Path

```
{storage_root}/runs/{run_id}/artifacts/{artifact_type}/v{version}/
```

| Component | Rule |
|---|---|
| `storage_root` | Config từ env, không hardcode. Ví dụ: `/data/ai-dev-system` hoặc `./data` |
| `run_id` | UUID dạng lowercase hyphenated: `abc12345-...` |
| `artifact_type` | Lowercase của enum: `spec_bundle`, `task_graph_approved`, `debate_report`, v.v. |
| `version` | Integer bắt đầu từ 1, không zero-pad: `v1`, `v2`, không phải `v01` |

**Ví dụ:**

```
/data/runs/abc12345-6789-.../artifacts/initial_brief/v1/
/data/runs/abc12345-6789-.../artifacts/debate_report/v1/
/data/runs/abc12345-6789-.../artifacts/spec_bundle/v1/
/data/runs/abc12345-6789-.../artifacts/spec_bundle/v2/        ← version mới sau regenerate
/data/runs/abc12345-6789-.../artifacts/task_graph_approved/v1/
```

**Rule bắt buộc:**
- ❌ Không overwrite folder version đã tồn tại
- ❌ Không reuse path cũ cho artifact mới
- ✅ Version mới luôn tạo folder mới

### 1.2 Cấu trúc nội dung theo artifact type

```
initial_brief/v1/
  initial_brief.json
  _complete.marker
  manifest.json

debate_report/v1/
  debate_report.json             ← full structured output
  questions/
    {question_id}.json           ← per-question detail (optional, nếu cần split)
  _complete.marker
  manifest.json

decision_log/v1/
  decision_log.json
  _complete.marker
  manifest.json

approved_answers/v1/
  approved_answers.json
  _complete.marker
  manifest.json

spec_bundle/v1/
  proposal.md
  design.md
  specs/
    functional.md
    non-functional.md
    acceptance-criteria.md
  _complete.marker
  manifest.json

task_graph_generated/v1/
  task_graph.json
  _complete.marker
  manifest.json

task_graph_approved/v1/
  task_graph.json                ← approved version (có thể khác generated)
  user_patch.json                ← nếu user edit, lưu diff riêng
  _complete.marker
  manifest.json

execution_log/v1/
  execution_log.json
  _complete.marker
  manifest.json
```

**`_complete.marker`** — ghi sau khi checksum xác nhận. Phân biệt folder hoàn chỉnh vs partial.
**`manifest.json`** — diagnostic mirror của artifact tại thời điểm promote. Cho phép verify integrity offline mà không cần query DB.

> ⚠️ **Manifest Truth Model:** `manifest.json` là **diagnostic mirror**, không phải source of truth.
> DB (`artifacts` table) là source of truth duy nhất.
> `manifest.artifact_id` có thể tham chiếu artifact không tồn tại nếu DB transaction bị rollback (orphan case).
> Không dùng manifest làm authoritative source — luôn verify qua DB.

```json
// manifest.json example
{
  "artifact_id": "abc123...",
  "artifact_type": "SPEC_BUNDLE",
  "version": 1,
  "run_id": "xyz789...",
  "content_checksum": "sha256:deadbeef...",
  "content_size": 48291,
  "promoted_at": "2026-03-29T10:00:00Z",
  "files": [
    { "path": "proposal.md",              "checksum": "sha256:...", "size": 1024 },
    { "path": "design.md",                "checksum": "sha256:...", "size": 2048 },
    { "path": "specs/functional.md",      "checksum": "sha256:...", "size": 4096 },
    { "path": "specs/non-functional.md",  "checksum": "sha256:...", "size": 1536 },
    { "path": "specs/acceptance-criteria.md", "checksum": "sha256:...", "size": 2048 }
  ]
}
```

### 1.3 Task Output Path

```
{storage_root}/runs/{run_id}/tasks/{task_id}/attempt-{n}/
```

| Component | Rule |
|---|---|
| `task_id` | Giữ nguyên string từ task_graph: `TASK-1`, `TASK-2` |
| `n` | attempt_number: `1`, `2`, `3` |

**Ví dụ:**

```
/data/runs/abc.../tasks/TASK-3/attempt-1/
  output/
    schema.sql
    erd.md
  logs/
    agent.log
  metadata.json                  ← { "status": "SUCCESS", "started_at": ..., "completed_at": ... }

/data/runs/abc.../tasks/TASK-3/attempt-2/
  output/
    schema.sql                   ← attempt 2 có thể overwrite output trong folder này
  logs/
    agent.log
  metadata.json
```

**Rule:**
- Mỗi attempt = folder riêng, immutable sau khi complete
- `output/` là nơi agent ghi file thật
- `metadata.json` được ghi khi attempt complete (success hoặc fail)

### 1.4 Temp Path (staging trước khi promote)

```
{storage_root}/tmp/runs/{run_id}/tasks/{task_id}/attempt-{n}/
```

Temp path dùng trong quá trình execution. Chỉ move sang final path khi validate xong.
Nằm trên cùng filesystem với final path để `rename()` là atomic.

---

## 2. Checksum Strategy

### 2.1 Checksum cho file đơn

```python
import hashlib

def checksum_file(path: str) -> str:
    """SHA-256 của nội dung file."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()
```

### 2.2 Checksum cho folder (deterministic)

```python
import os
import hashlib

def checksum_folder(folder_path: str) -> str:
    """
    SHA-256 deterministic cho toàn bộ folder.

    Algorithm:
      1. Collect tất cả file paths (relative từ folder_path)
      2. Sort theo path (không phụ thuộc OS order)
      3. Hash từng file, concat "relative_path:file_hash"
      4. SHA-256 của chuỗi concat

    KHÔNG phụ thuộc: OS, filesystem order, timestamp, inode.
    """
    entries = []

    for root, dirs, files in os.walk(folder_path):
        dirs.sort()  # walk theo thứ tự deterministic
        for filename in sorted(files):
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, folder_path)
            file_hash = checksum_file(abs_path)
            entries.append(f"{rel_path}:{file_hash}")

    combined = "\n".join(entries)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()


def checksum_artifact(content_ref: str) -> tuple[str, int]:
    """
    Trả về (checksum, size_bytes).
    content_ref là path tới file hoặc folder.
    """
    if os.path.isfile(content_ref):
        checksum = checksum_file(content_ref)
        size = os.path.getsize(content_ref)
    else:
        checksum = checksum_folder(content_ref)
        size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(content_ref)
            for f in files
        )
    return checksum, size
```

### 2.3 Input Checksum (idempotency)

`input_checksum` trong bảng `artifacts` dùng để detect "cùng input → đã có artifact rồi".

```python
def compute_input_checksum(
    input_artifact_ids: list[UUID],
    checksum_scope: ChecksumScope,
    raw_text: str | None = None
) -> str:
    if checksum_scope == 'raw_input':
        # INITIAL_BRIEF: hash trực tiếp từ user text
        assert raw_text is not None
        return hashlib.sha256(raw_text.encode('utf-8')).hexdigest()

    elif checksum_scope == 'artifact_inputs':
        # Hash từ content_checksum của từng input artifact
        checksums = db.query("""
            SELECT content_checksum FROM artifacts
            WHERE artifact_id = ANY($ids)
            ORDER BY artifact_id  -- sort để deterministic
        """, ids=input_artifact_ids)
        combined = "\n".join(row.content_checksum for row in checksums)
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()

    elif checksum_scope == 'composed':
        # Kết hợp nhiều nguồn — define per artifact type khi cần
        raise NotImplementedError("composed scope defined per artifact type")
```

### 2.4 Integrity Check

```python
def verify_artifact_integrity(artifact: Artifact) -> bool:
    """Kiểm tra content trên disk khớp với DB record."""
    if not os.path.exists(artifact.content_ref):
        return False
    actual_checksum, actual_size = checksum_artifact(artifact.content_ref)
    return (
        actual_checksum == artifact.content_checksum
        and actual_size == artifact.content_size
    )
```

### 2.5 Checksum Scope và Giới Hạn

> ⚠️ **`content_checksum` dùng cho integrity verification — không phải deduplication.**

`content_checksum` trong DB là SHA-256 của **toàn bộ folder artifact** sau khi promote, bao gồm cả `_complete.marker` và `manifest.json`. Do marker chứa `promoted_at` timestamp, checksum này **không deterministic** giữa các lần promote cùng content.

| Mục đích | Dùng được? | Cách làm |
|---|---|---|
| Verify file không bị corrupt | ✅ | `verify_artifact_integrity()` |
| Detect content thay đổi sau promote | ✅ | So sánh `content_checksum` với recompute |
| Dedupe — phát hiện "cùng content đã có artifact" | ❌ | Không dùng `content_checksum`; dùng `input_checksum` (Section 2.3) |
| Semantic equality giữa 2 artifacts | ❌ | Không dùng `content_checksum` |

**Nếu sau này cần content-level dedupe:** tính `content_checksum_raw` (checksum folder không bao gồm marker/manifest) trước khi ghi marker, lưu riêng. V1 không cần.

---

## 3. Promotion Protocol

Promotion là quá trình chuyển task output thành artifact chính thức trong DB.
Đây là chỗ dễ bug nhất — phải follow đúng thứ tự.

### 3.1 Full Protocol (6 bước)

```
Step 1: Task execution → ghi vào TEMP path
Step 2: Wait for stable (tránh partial write từ agent chưa flush)
Step 3: Validate output
Step 4: Check disk space
Step 5: Two-phase atomic move: TEMP → STAGING → FINAL
Step 6: Compute checksum trên FINAL
Step 7: DB transaction (version lock + promotion guard + artifact + task_run + current_artifacts + event)
```

### 3.2 Pseudo-code

```python
def promote_output(
    task_run: TaskRun,
    promoted_output: PromotedOutput,  # từ task_graph: { name, artifact_type }
    temp_output_path: str,
) -> UUID:
    """
    Promote một output file thành artifact ACTIVE.
    Trả về artifact_id.

    INVARIANT: file phải tồn tại trên disk TRƯỚC khi insert vào DB.
    """

    # --- STEP 1: Xác định paths ---
    staging_path = build_artifact_path(...) + ".staging"
    final_path   = build_artifact_path(...)
    # e.g. /data/runs/{run_id}/artifacts/spec_bundle/v{n}.staging
    #      /data/runs/{run_id}/artifacts/spec_bundle/v{n}/
    #
    # Cả staging và final đều phải cùng filesystem với temp để rename() là atomic.

    # --- STEP 2: Wait for stable output ---
    # Tránh case agent chưa flush hết file khi ta bắt đầu checksum/move.
    # Track 3 signals: total_size, file_count, max_mtime.
    # File rename trong folder làm max_mtime thay đổi dù total_size không đổi.
    wait_until_stable(temp_output_path, poll_interval_ms=100, stable_duration_ms=200)
    # wait_until_stable checks: total_size, file_count, max(mtime per file)
    # stable = tất cả 3 không đổi trong stable_duration liên tiếp.

    # --- STEP 3: Validate output ---
    validate_output(temp_output_path, promoted_output)
    # Nếu fail → raise exception → Task Executor mark FAILED; temp giữ để debug.

    # --- STEP 4: Check disk space ---
    required = estimate_size(temp_output_path)
    check_free_space(final_path, required_bytes=required * 2)  # buffer 2x
    # Nếu fail → raise DiskFullError → Task Executor mark FAILED (ENVIRONMENT_ERROR).

    # --- STEP 5: Two-phase atomic move ---
    #
    # Tại sao 2 bước thay vì 1?
    # Nếu crash trong khi copy vào final_path trực tiếp → final_path có thể partial.
    # Cleanup job sẽ không biết đó là partial hay hoàn chỉnh.
    # .staging convention cho phép cleanup job xử lý an toàn: bất kỳ .staging nào cũng là garbage.
    #
    os.makedirs(staging_path, exist_ok=False)   # fail nếu staging đã tồn tại
    atomic_move(temp_output_path, staging_path)  # rename: atomic trên cùng filesystem
    # → crash ở đây: temp mất, staging partial → cleanup job xóa .staging → safe

    staging_checksum, staging_size = checksum_artifact(staging_path)  # verify staging trước bước tiếp

    os.rename(staging_path, final_path)          # atomic rename lần 2: staging → final
    # → crash ở đây: staging mất, final chưa có → không có artifact path "official" → safe

    # --- STEP 6: Compute checksum trên FINAL ---
    content_checksum, content_size = checksum_artifact(final_path)
    # Double-check: cả checksum lẫn size phải khớp với staging.
    # So cả 2 để phòng case filesystem bug / truncation với hash collision (cực hiếm nhưng không bỏ qua).
    if content_checksum != staging_checksum or content_size != staging_size:
        raise IntegrityError(
            f"Integrity mismatch after final rename — "
            f"checksum: {staging_checksum!r} vs {content_checksum!r}, "
            f"size: {staging_size} vs {content_size}"
        )

    # --- STEP 6b: Write _complete.marker ---
    # Marker file được ghi SAU khi checksum xác nhận — là tín hiệu "folder này hoàn chỉnh".
    # Cleanup jobs và debug tools dùng marker này để phân biệt partial vs complete.
    # Orphan cleanup chỉ log folder có _complete.marker; folder không có marker là suspicious.
    with open(os.path.join(final_path, '_complete.marker'), 'w') as f:
        f.write(json.dumps({
            "artifact_type": promoted_output.artifact_type,
            "content_checksum": content_checksum,
            "content_size": content_size,
            "promoted_at": datetime.utcnow().isoformat(),
        }))
    # Ghi xong marker → recalculate checksum vì folder đã thay đổi
    content_checksum, content_size = checksum_artifact(final_path)

    # --- STEP 7: DB transaction ---
    #
    # CRITICAL: version phải được lock trong transaction để tránh race.
    # Promotion guard (output_artifact_id IS NULL) đảm bảo chỉ 1 worker thắng.
    #
    with db.transaction():

        # 7a: Lock version counter — tránh 2 worker cùng lấy version giống nhau
        #
        # PHANTOM EDGE CASE: nếu chưa có artifact nào cùng type trong run này,
        # SELECT MAX(...) không lock row nào → 2 transaction đều thấy max=0 → version conflict.
        #
        # Fix: lock một sentinel row cố định per (run_id, artifact_type).
        # Sentinel row được tạo khi task_graph được approve (không cần giá trị, chỉ cần existence).
        # Nếu không có sentinel → dùng table-level lock làm fallback.
        #
        db.execute("""
            INSERT INTO artifact_version_locks (run_id, artifact_type)
            VALUES ($run_id, $type)
            ON CONFLICT DO NOTHING
        """, run_id=task_run.run_id, type=promoted_output.artifact_type)

        lock_row = db.query_one("""
            SELECT current_version FROM artifact_version_locks
            WHERE run_id = $run_id AND artifact_type = $type
            FOR UPDATE
        """, run_id=task_run.run_id, type=promoted_output.artifact_type)

        next_version = lock_row.current_version + 1

        db.execute("""
            UPDATE artifact_version_locks
            SET current_version = $v
            WHERE run_id = $run_id AND artifact_type = $type
        """, v=next_version, run_id=task_run.run_id, type=promoted_output.artifact_type)

        # 7b: Promotion guard — chỉ proceed nếu task vẫn RUNNING và chưa có output artifact
        #
        # completed_at IS NULL là guard thứ ba:
        # Nếu worker A đã mark FAILED (completed_at = now()), worker B chậm không được overwrite.
        # status = 'RUNNING' đã cover case này, nhưng completed_at IS NULL thêm 1 lớp tường.
        #
        guarded = db.execute("""
            SELECT 1 FROM task_runs
            WHERE task_run_id = $id
              AND status = 'RUNNING'
              AND output_artifact_id IS NULL
              AND completed_at IS NULL
            FOR UPDATE
        """, id=task_run.id)
        if not guarded:
            # Worker khác đã promote hoặc task đã bị mark FAILED/ABORTED
            raise PromotionConflictError(f"task_run {task_run.id} không còn eligible để promote")

        # 7c: Supersede artifact ACTIVE cũ cùng type
        db.execute("""
            UPDATE artifacts
            SET status = 'SUPERSEDED'
            WHERE run_id = $run_id
              AND artifact_type = $type
              AND status = 'ACTIVE'
        """, run_id=task_run.run_id, type=promoted_output.artifact_type)

        # 7d: Insert artifact mới
        artifact_id = db.query_one("""
            INSERT INTO artifacts (
                run_id, artifact_type, version, status,
                created_by, input_artifact_ids,
                content_ref, content_checksum, content_size
            ) VALUES (
                $run_id, $type, $version, 'ACTIVE',
                'system', $input_ids,
                $content_ref, $content_checksum, $content_size
            )
            RETURNING artifact_id
        """,
            run_id=task_run.run_id,
            type=promoted_output.artifact_type,
            version=next_version,
            input_ids=task_run.input_artifact_ids,
            content_ref=final_path,
            content_checksum=content_checksum,
            content_size=content_size,
        ).artifact_id

        # 7e: Update task_run — guard đầy đủ trong WHERE
        updated = db.execute("""
            UPDATE task_runs
            SET status = 'SUCCESS',
                output_ref = $output_ref,
                output_artifact_id = $artifact_id,
                completed_at = now()
            WHERE task_run_id = $id
              AND status = 'RUNNING'
              AND output_artifact_id IS NULL
              AND completed_at IS NULL
        """,
            output_ref=final_path,
            artifact_id=artifact_id,
            id=task_run.id,
        )
        if updated.rowcount == 0:
            raise PromotionConflictError("task_run đã bị update bởi worker khác")

        # 7f: Update runs.current_artifacts
        artifact_key = ARTIFACT_TYPE_TO_KEY[promoted_output.artifact_type]
        if artifact_key is not None:  # EXECUTION_LOG không map vào current_artifacts
            db.execute("""
                UPDATE runs
                SET current_artifacts = jsonb_set(
                        current_artifacts,
                        $key_path,
                        to_jsonb($artifact_id::text)
                    ),
                    last_activity_at = now()
                WHERE run_id = $run_id
            """,
                key_path=f'{{{artifact_key}}}',
                artifact_id=str(artifact_id),
                run_id=task_run.run_id,
            )

        # 7g: Insert events
        db.execute("""
            INSERT INTO events (run_id, task_run_id, event_type, actor, payload)
            VALUES
              ($run_id, $task_run_id, 'ARTIFACT_CREATED', 'system',
               jsonb_build_object('artifact_id', $artifact_id, 'version', $version)),
              ($run_id, $task_run_id, 'TASK_COMPLETED', 'system',
               jsonb_build_object('artifact_id', $artifact_id))
        """, ...)

    return artifact_id
```

### 3.3 Nếu transaction fail sau khi file đã move

```
Situation: file ở final_path tồn tại, DB rollback → artifact_id không tồn tại.

Xử lý:
  - File này là "orphan" — tồn tại trên disk nhưng không có DB record.
  - KHÔNG tự động xóa (có thể cần recovery).
  - Orphan Cleanup Job sẽ detect và xử lý (xem Section 5).
  - Behavior này là intentional và safe.
```

### 3.4 Nếu validation fail (Step 3) hoặc disk full (Step 4)

```
Situation: output không hợp lệ hoặc không đủ disk, file vẫn ở temp path.

Xử lý:
  - Raise exception → caller (Task Executor) mark task_run FAILED.
  - File temp giữ nguyên để debug.
  - Temp Cleanup Job xóa sau N ngày (xem 5.3).
```

### 3.5 Staging cleanup

```
Bất kỳ folder nào có suffix .staging đều là garbage — có thể xóa an toàn.
Staging không bao giờ là trạng thái cuối cùng hợp lệ.
Cleanup Job xóa .staging cũ hơn 30 phút (đủ để tránh race với promotion đang chạy).
```

---

## 4. Non-Promoted Output

Task output KHÔNG nằm trong `promoted_outputs` vẫn được lưu, nhưng chỉ qua `task_run.output_ref`:

```python
def save_raw_output(task_run: TaskRun, temp_output_path: str) -> str:
    """
    Lưu output không được promote — không tạo artifact record.
    Chỉ move vào final task output path.
    """
    final_path = build_task_output_path(
        storage_root=config.storage_root,
        run_id=task_run.run_id,
        task_id=task_run.task_id,
        attempt_number=task_run.attempt_number,
    )
    atomic_move(temp_output_path, final_path)

    # Chỉ update task_run.output_ref — không tạo artifact
    db.execute("""
        UPDATE task_runs
        SET output_ref = $path
        WHERE task_run_id = $id
    """, path=final_path, id=task_run.id)

    return final_path
```

---

## 5. Orphan Cleanup

Chạy như background job, không blocking, mỗi 1 giờ đủ cho v1.

### 5.1 Check DB → FS (artifact không có file)

```python
def check_db_to_fs() -> None:
    """Tìm artifact mà content_ref không tồn tại trên disk."""
    active_artifacts = db.query("""
        SELECT artifact_id, content_ref, content_checksum
        FROM artifacts
        WHERE status IN ('ACTIVE', 'DRAFT')
    """)

    for artifact in active_artifacts:
        if not os.path.exists(artifact.content_ref):
            # File mất — artifact "ảo"
            db.execute("""
                UPDATE artifacts
                SET status = 'FAILED',
                    annotations = jsonb_set(
                        annotations, '{integrity_error}',
                        '"content_ref_missing"'
                    )
                WHERE artifact_id = $id AND status NOT IN ('SUPERSEDED', 'FAILED')
            """, id=artifact.artifact_id)

            emit_alert(f"ARTIFACT_MISSING: {artifact.artifact_id} → {artifact.content_ref}")

        elif not verify_artifact_integrity(artifact):
            # File tồn tại nhưng checksum mismatch
            emit_alert(f"ARTIFACT_CORRUPTED: {artifact.artifact_id}")
            # Không auto-change status — cần human review
```

### 5.2 Check FS → DB (file không có artifact)

```python
ORPHAN_MIN_AGE_MINUTES = 15  # 10 phút không đủ nếu promotion bị slow (network, DB lock)
                               # 15 phút là conservative safe window

def check_fs_to_db() -> None:
    """Tìm folder artifact trên disk mà không có DB record."""
    artifact_root = os.path.join(config.storage_root, 'runs')
    now = time.time()

    for run_dir in os.listdir(artifact_root):
        run_id = run_dir
        artifacts_path = os.path.join(artifact_root, run_id, 'artifacts')
        if not os.path.isdir(artifacts_path):
            continue

        for type_dir in os.listdir(artifacts_path):
            for version_dir in os.listdir(os.path.join(artifacts_path, type_dir)):
                content_ref = os.path.join(artifacts_path, type_dir, version_dir)

                # Bỏ qua .staging — garbage theo convention, cleanup riêng
                if version_dir.endswith('.staging'):
                    continue

                # Bỏ qua folder còn mới
                folder_age_minutes = (now - os.path.getmtime(content_ref)) / 60
                if folder_age_minutes < ORPHAN_MIN_AGE_MINUTES:
                    continue

                # Bỏ qua folder có recent write activity (bất kỳ file nào thay đổi trong 15 phút)
                # Phòng case promotion stuck > 10 phút nhưng vẫn đang chạy
                max_mtime = max(
                    os.path.getmtime(os.path.join(root, f))
                    for root, _, files in os.walk(content_ref)
                    for f in files
                ) if os.path.isdir(content_ref) else os.path.getmtime(content_ref)
                if (now - max_mtime) / 60 < ORPHAN_MIN_AGE_MINUTES:
                    continue

                # Kiểm tra có artifact record không
                exists = db.query_one("""
                    SELECT 1 FROM artifacts
                    WHERE run_id = $run_id
                      AND artifact_type = $type
                      AND content_ref = $ref
                """, run_id=run_id, type=type_dir.upper(), ref=content_ref)

                if not exists:
                    # Nếu có _complete.marker → legitimate orphan (promotion OK, DB rollback)
                    has_marker = os.path.exists(os.path.join(content_ref, '_complete.marker'))
                    log_orphan(content_ref, severity='high' if has_marker else 'low')
                    # high severity: promotion đã xong, DB mới fail → cần check thủ công
                    # low severity:  folder không có marker → promotion chưa hoàn chỉnh, safe cleanup
```

### 5.3 Staging cleanup

```python
STAGING_MAX_AGE_MINUTES = 30

def cleanup_staging_files() -> None:
    """Xóa .staging folders quá cũ. Staging không bao giờ là trạng thái cuối hợp lệ."""
    artifact_root = os.path.join(config.storage_root, 'runs')
    cutoff = time.time() - (STAGING_MAX_AGE_MINUTES * 60)

    for path in glob(f"{artifact_root}/*/artifacts/*/*.staging"):
        if os.path.getmtime(path) < cutoff:
            shutil.rmtree(path)
            log_cleanup(f"Removed stale staging: {path}")
```

### 5.4 Temp Cleanup (phân biệt SUCCESS và FAILED)

```python
def cleanup_temp_files() -> None:
    """
    Xóa temp output — giữ lâu hơn nếu task FAILED để debug.

    Policy:
      - Task SUCCESS: xóa temp sau 1 giờ (không cần nữa)
      - Task FAILED:  giữ temp 72 giờ (để debug), sau đó xóa
      - Unknown:      giữ 24 giờ
    """
    temp_root = os.path.join(config.storage_root, 'tmp')
    now = time.time()

    for attempt_path in glob(f"{temp_root}/runs/*/tasks/*/attempt-*/"):
        run_id, task_id, attempt_n = parse_attempt_path(attempt_path)

        task_run = db.query_one("""
            SELECT status FROM task_runs
            WHERE run_id = $run_id AND task_id = $task_id AND attempt_number = $n
        """, run_id=run_id, task_id=task_id, n=attempt_n)

        if task_run is None:
            max_age_hours = 24
        elif task_run.status == 'SUCCESS':
            max_age_hours = 1
        elif task_run.status in ('FAILED', 'ABORTED'):
            max_age_hours = 72
        else:
            continue  # task đang chạy — không xóa

        cutoff = now - (max_age_hours * 3600)
        if os.path.getmtime(attempt_path) < cutoff:
            shutil.rmtree(attempt_path)
```

---

## 6. Promoted Output Contract (task_graph.json)

Task Generator phải khai báo `promoted_outputs` trong task_graph. Đây là contract bắt buộc.

### 6.1 Format trong task_graph.json

```json
{
  "tasks": [
    {
      "task_id": "TASK-1",
      "agent_type": "DatabaseSpecialist",
      "promoted_outputs": [
        {
          "name": "schema.sql",
          "artifact_type": "EXECUTION_LOG",
          "description": "PostgreSQL schema file"
        },
        {
          "name": "erd.md",
          "artifact_type": "EXECUTION_LOG",
          "description": "Entity relationship diagram"
        }
      ],
      "non_promoted_outputs": ["migration_notes.txt", "agent.log"]
    }
  ]
}
```

**Lưu ý v1:** Hầu hết task output dùng `EXECUTION_LOG` làm artifact_type.
Chỉ dùng type khác khi output thật sự là một artifact loại đó (ví dụ task tái generate spec_bundle).

### 6.2 Validation rule

```python
VALID_PROMOTABLE_TYPES = {
    'EXECUTION_LOG',    # default cho hầu hết task output
    'SPEC_BUNDLE',      # chỉ khi task tái tạo spec
    'TASK_GRAPH_GENERATED',  # chỉ khi task tái tạo task graph
}

def validate_promotion_config(task: Task) -> None:
    for output in task.promoted_outputs:
        if output.artifact_type not in VALID_PROMOTABLE_TYPES:
            raise ValueError(f"Task {task.task_id}: invalid promoted artifact_type {output.artifact_type}")
```

---

## 7. DB ↔ FS Mapping Reference

| DB field | FS path | Ý nghĩa |
|---|---|---|
| `artifacts.content_ref` | `.../artifacts/{type}/v{n}/` | Folder chứa nội dung artifact |
| `artifacts.content_checksum` | SHA-256 của toàn bộ folder (gồm cả marker + manifest) | Integrity check |
| `artifacts.content_size` | Tổng bytes | Quick sanity check |
| `task_runs.output_ref` | `.../tasks/{id}/attempt-{n}/output/` | Raw output của task |
| `task_runs.output_artifact_id` | FK → `artifacts.artifact_id` | Nếu output được promote |

**Table bổ sung: `artifact_version_locks`**

```sql
CREATE TABLE artifact_version_locks (
    run_id          UUID        NOT NULL,
    artifact_type   TEXT        NOT NULL,
    current_version INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, artifact_type)
);
```

- Được insert khi task_graph approved (tạo trước cho tất cả artifact types sẽ xuất hiện trong run)
- `FOR UPDATE` trên table này là version lock — đảm bảo không phantom khi chưa có artifact nào
- `current_version` sync với `MAX(version)` trong `artifacts` trong trường hợp bình thường

> ⚠️ **Version Lock Drift:** `current_version` có thể lệch so với `MAX(version)` nếu crash xảy ra giữa `UPDATE artifact_version_locks` và `INSERT INTO artifacts` (step 7a và 7d trong promotion protocol).
> Drift này **không gây corrupt dữ liệu** — chỉ skip một version number.
> **Không xử lý trong hot path.** Reconciliation job (chạy cùng orphan cleanup, mỗi giờ) sẽ detect và reset `current_version = MAX(version)` nếu phát hiện lệch.

---

## 8. Path Builder Reference

```python
def build_artifact_path(storage_root, run_id, artifact_type, version) -> str:
    type_slug = artifact_type.lower().replace('_', '_')  # enum đã là snake_case
    return os.path.join(storage_root, 'runs', str(run_id), 'artifacts', type_slug, f'v{version}')

def build_task_output_path(storage_root, run_id, task_id, attempt_number) -> str:
    return os.path.join(storage_root, 'runs', str(run_id), 'tasks', task_id, f'attempt-{attempt_number}')

def build_temp_path(storage_root, run_id, task_id, attempt_number) -> str:
    return os.path.join(storage_root, 'tmp', 'runs', str(run_id), 'tasks', task_id, f'attempt-{attempt_number}')


ARTIFACT_TYPE_TO_KEY = {
    'INITIAL_BRIEF':        'initial_brief_id',
    'DEBATE_REPORT':        'debate_report_id',
    'DECISION_LOG':         'decision_log_id',
    'APPROVED_ANSWERS':     'approved_answers_id',
    'SPEC_BUNDLE':          'spec_bundle_id',
    'TASK_GRAPH_GENERATED': 'task_graph_gen_id',
    'TASK_GRAPH_APPROVED':  'task_graph_approved_id',
    'EXECUTION_LOG':        None,   # không map vào current_artifacts
}
```

---

## 9. Failure Mode Matrix

| Failure xảy ra ở | File state | DB state | Hành động |
|---|---|---|---|
| Step 3 (validate fail) | temp tồn tại | không có record | Task FAILED; temp giữ 72h |
| Step 4 (disk full) | temp tồn tại | không có record | Task FAILED (ENVIRONMENT_ERROR); temp giữ |
| Step 5a (move temp → staging fail) | temp partial hoặc mất | không có record | Task FAILED; cleanup temp |
| Step 5b (rename staging → final fail) | staging tồn tại | không có record | Staging cleanup job xóa sau 30 phút |
| Step 6 (checksum mismatch sau rename) | final partial hoặc corrupt | không có record | Task FAILED; alert; orphan cleanup |
| Step 6b (_complete.marker fail) | final tồn tại, không có marker | không có record | Task FAILED; orphan cleanup (low severity) |
| Step 7 (DB transaction fail) | final tồn tại | rollback | Orphan file; Cleanup Job detect sau ≥10 phút |
| Crash giữa Step 5b và Step 7 | final tồn tại | không có record | Orphan file; Cleanup Job detect |
| Step 7 promotion guard fail | final tồn tại | không có record | Orphan file; worker khác đã thắng |
| Step 7 thành công, crash sau | final tồn tại | artifact ACTIVE | Trạng thái hợp lệ — safe |

**Nguyên tắc bất biến:**
- Orphan file (file tồn tại, không có DB record) = acceptable; Cleanup Job xử lý
- Corrupt state (DB record ACTIVE, không có file) = unacceptable; phải detect và alert ngay
- Staging folder = always garbage; safe to delete bất kỳ lúc nào (sau min age)
