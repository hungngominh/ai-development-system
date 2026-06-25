"""Minimal local web dashboard for the AI Dev System.

Zero extra dependencies (Python stdlib http.server). Reads the same SQLite DB +
storage the CLI writes, so existing runs and debate reports show up. Lets you:
- list runs and their status,
- read a run's debate report (rendered),
- kick off a new project (stub = instant, or Max = real but slow).

Run:  py -3.12 -m ai_dev_system.webui   (then open http://localhost:8765)
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS

PORT = int(os.environ.get("AIDEV_UI_PORT", "8765"))
# A run that's RUNNING but whose progress log hasn't advanced in this many
# seconds is almost certainly orphaned — its background debate process died and
# nothing reconciled the run row. Max-mode debates emit a line every ~1-3 min,
# so several minutes of silence means "stuck", not "working".
STALE_SECONDS = int(os.environ.get("AIDEV_STALE_SECONDS", "300"))

_CSS = """
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background:#0f1117; color:#e6e6e6; }
a { color:#6db3ff; text-decoration:none; } a:hover { text-decoration:underline; }
header { background:#161a23; padding:14px 24px; border-bottom:1px solid #262b36; }
header h1 { margin:0; font-size:18px; } header .sub { color:#8a93a6; font-size:13px; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 24px; }
.card { background:#161a23; border:1px solid #262b36; border-radius:10px; padding:18px; margin-bottom:18px; }
h2 { font-size:15px; margin:0 0 12px; color:#c8d0de; }
table { width:100%; border-collapse:collapse; }
td, th { text-align:left; padding:8px 10px; border-bottom:1px solid #222733; font-size:14px; }
th { color:#8a93a6; font-weight:600; }
.badge { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:600; }
.b-pause { background:#3a2f10; color:#f0c060; } .b-done { background:#123a1c; color:#5fd07f; }
.b-run { background:#10263a; color:#5fb0f0; } .b-other { background:#2a2f3a; color:#9aa3b2; }
input, textarea, select, button { font:inherit; }
input, textarea, select { width:100%; background:#0f1117; color:#e6e6e6; border:1px solid #2a3140; border-radius:7px; padding:9px; }
label { display:block; font-size:13px; color:#8a93a6; margin:12px 0 5px; }
button { background:#2d6cdf; color:#fff; border:0; border-radius:7px; padding:10px 18px; font-weight:600; cursor:pointer; margin-top:14px; }
button:hover { background:#3a78ec; }
.q { border:1px solid #222733; border-radius:8px; padding:12px; margin-bottom:10px; }
.q .head { font-size:13px; color:#8a93a6; }
.q .text { margin:4px 0 8px; font-weight:600; }
.mod { color:#c8d0de; font-size:14px; } .caveat { color:#f0c060; font-size:13px; margin-top:6px; }
.muted { color:#8a93a6; font-size:13px; }
pre { white-space:pre-wrap; background:#0f1117; border:1px solid #222733; border-radius:7px; padding:10px; font-size:13px; }
"""


def _badge(status: str) -> str:
    s = status or ""
    cls = "b-other"
    if "PAUSED" in s:
        cls = "b-pause"
    elif s in ("COMPLETED",):
        cls = "b-done"
    elif "RUNNING" in s:
        cls = "b-run"
    return f'<span class="badge {cls}">{html.escape(s)}</span>'


def _page(title: str, body: str, head_extra: str = "") -> bytes:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"{head_extra}<style>{_CSS}</style></head><body>"
        f"<header><h1>AI Dev System <span class='sub'>· local dashboard</span></h1></header>"
        f"<div class='wrap'>{body}</div></body></html>"
    ).encode("utf-8")


def _config() -> Config:
    return Config.from_env()


def _list_runs():
    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        rows = conn.execute(
            "SELECT run_id, status, title, created_at, metadata "
            "FROM runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _find_report_path(run_id: str) -> Path | None:
    """Locate a run's debate_report.json via storage (by run_id)."""
    cfg = _config()
    p = Path(cfg.storage_root) / "runs" / run_id / "artifacts" / "debate_report" / "v1" / "debate_report.json"
    return p if p.exists() else None


def _parse_run_metadata(metadata) -> dict:
    try:
        return json.loads(metadata) if isinstance(metadata, str) else (metadata or {})
    except (TypeError, ValueError):
        return {}


def _is_task_spec_row(metadata) -> bool:
    """A runs row produced by the single-task-spec worker (metadata.kind)."""
    return _parse_run_metadata(metadata).get("kind") == "task_spec"


def _run_row_html(r: dict) -> str:
    rid = r["run_id"]
    meta = _parse_run_metadata(r.get("metadata"))
    kind = meta.get("kind") or ""
    if kind == "task_spec":
        href = f"/task-spec?id={html.escape(rid)}"
        tag = " <span class='muted'>· spec</span>"
    elif kind == "task_exec":
        spec_id = meta.get("spec_id") or rid
        href = f"/task-exec?id={html.escape(spec_id)}"
        tag = " <span class='muted'>· exec</span>"
    else:
        href = f"/run?id={html.escape(rid)}"
        tag = ""
    title = html.escape((r.get("title") or "")[:48])
    return (
        f"<tr><td><a href='{href}'>{html.escape(rid[:8])}</a></td>"
        f"<td>{_badge(r['status'])}</td><td>{title}{tag}</td>"
        f"<td class='muted'>{html.escape(str(r.get('created_at') or ''))[:19]}</td></tr>"
    )


def _home() -> bytes:
    runs = _list_runs()
    rows = "".join(_run_row_html(r) for r in runs) \
        or "<tr><td colspan='4' class='muted'>Chưa có run nào trong DB.</td></tr>"

    form = """
    <div class='card'><h2>Tạo project mới</h2>
    <form method='post' action='/start'>
      <label>Tên project</label><input name='name' placeholder='my-app' required>
      <label>Ý tưởng</label><textarea name='idea' rows='3' placeholder='Mô tả ý tưởng...' required></textarea>
      <label>Chế độ LLM</label>
      <select name='mode'>
        <option value='stub'>Stub — tức thì, không tốn Max (debate giả)</option>
        <option value='max'>Claude Max — thật, chậm (~nhiều phút)</option>
      </select>
      <button type='submit'>Bắt đầu debate →</button>
    </form>
    <p class='muted'>Sau khi chạy, refresh trang để thấy run mới + trạng thái.</p></div>
    """
    table = (
        "<div class='card'><h2>Runs</h2><table>"
        "<tr><th>Run</th><th>Status</th><th>Title</th><th>Created</th></tr>"
        f"{rows}</table></div>"
    )
    task_form = """
    <div class='card'><h2>Đặc tả 1 task</h2>
    <form method='post' action='/spec-task'>
      <label>Mô tả task</label><textarea name='idea' rows='3' placeholder='Mô tả 1 task/feature...' required></textarea>
      <label>Đường dẫn repo (tuỳ chọn — bật agentic đọc code thật)</label>
      <input name='repo' placeholder='vd: E:\\Work\\my-app'>
      <label>Chế độ LLM</label>
      <select name='mode'>
        <option value='stub'>Stub — tức thì (facet giả = needs_human)</option>
        <option value='max'>Claude Max — thật (~vài chục giây)</option>
      </select>
      <button type='submit'>Sinh TaskSpec →</button>
    </form>
    <p class='muted'>Trả về 20 facet (13 spec + 7 impl-docs) cho task.</p></div>
    """
    return _page("AI Dev System", form + task_form + table)


_APPROVABLE_STATUSES: dict[str, str] = {
    "PAUSED_AT_GATE_2": "RUNNING_PHASE_3",
    "PAUSED_FOR_DECISION": "RUNNING_EXECUTION",
    "PAUSED_AT_GATE_3": "RUNNING_PHASE_V",
    "PAUSED_AT_GATE_3B": "RUNNING_PHASE_V",
}

_TERMINAL_STATUSES = {"COMPLETED", "ABORTED", "FAILED"}


def _run_edit_form(run_id: str, title: str, metadata: str, status: str) -> str:
    rid = html.escape(run_id)
    approvable = status in _APPROVABLE_STATUSES
    approve_btn = ""
    if approvable:
        approve_btn = (
            "<form method=\"post\" action=\"/run-approve\" style=\"display:inline;margin-left:10px\">"
            f"<input type=\"hidden\" name=\"id\" value=\"{rid}\">"
            "<button type=\"submit\" style=\"background:#1a6b2a\">✓ Duyệt &amp; tiếp tục</button>"
            "</form>"
        )
    return (
        "<div class=\"card\"><h2>Chỉnh sửa Run</h2>"
        "<form method=\"post\" action=\"/run-edit\">"
        f"<input type=\"hidden\" name=\"id\" value=\"{rid}\">"
        "<label>Tiêu đề</label>"
        f"<input name=\"title\" value=\"{html.escape(title)}\" required>"
        "<label>Metadata (JSON)</label>"
        f"<textarea name=\"metadata\" rows=\"4\">{html.escape(metadata)}</textarea>"
        "<button type=\"submit\">Lưu ✓</button>"
        "</form>"
        f"{approve_btn}"
        "</div>"
    )


def _do_run_edit(run_id: str, title: str, metadata_str: str) -> str | bytes:
    """Process run edit. Returns redirect URL string or error page bytes."""
    from ai_dev_system.db.repos.runs import RunRepo

    run_id = run_id.strip()
    title = title.strip()
    if not run_id:
        return _page("400", "<div class='card'><p class='caveat'>400 — Thiếu run id.</p></div>")
    if not title:
        return _page("400", "<div class='card'><p class='caveat'>400 — Tiêu đề không được để trống.</p></div>")

    try:
        json.loads(metadata_str)
    except (json.JSONDecodeError, TypeError):
        return _page("400", "<div class='card'><p class='caveat'>400 — Metadata JSON không hợp lệ.</p></div>")

    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return _page("error", "<div class='card'><p class='caveat'>Run không tìm thấy.</p></div>")
        RunRepo(conn).update_title_and_metadata(run_id, title, metadata_str)
        conn.commit()
    finally:
        conn.close()

    log = _progress_log()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(f"[run-edit] {run_id[:8]} title={title!r} at {_dt.datetime.now(_dt.timezone.utc).isoformat()}\n")
    except OSError:
        pass

    return f"/run?id={urllib.parse.quote(run_id)}"


def _do_run_approve(run_id: str) -> str | bytes:
    """Process run approve. Returns redirect URL string or error page bytes."""
    from ai_dev_system.db.repos.runs import RunRepo

    run_id = run_id.strip()
    if not run_id:
        return _page("400", "<div class='card'><p class='caveat'>400 — Thiếu run id.</p></div>")

    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            "SELECT run_id, status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return _page("error", "<div class='card'><p class='caveat'>Run không tìm thấy.</p></div>")
        current_status = row["status"]
        next_status = _APPROVABLE_STATUSES.get(current_status)
        if next_status is None:
            return _page(
                "400",
                "<div class='card'><p class='caveat'>400 — Run không ở trạng thái có thể duyệt "
                f"(hiện tại: {html.escape(current_status)}).</p></div>",
            )
        RunRepo(conn).update_status(run_id, next_status)
        conn.commit()
    finally:
        conn.close()

    log = _progress_log()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(f"[run-approve] {run_id[:8]} {current_status}→{next_status} at {_dt.datetime.now(_dt.timezone.utc).isoformat()}\n")
    except OSError:
        pass

    return f"/run?id={urllib.parse.quote(run_id)}"


def _run_detail(run_id: str) -> bytes:
    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            "SELECT run_id, status, title, metadata, current_artifacts, created_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    status = row["status"] if row else ""
    running = status.startswith("RUNNING")
    head = "<p><a href='/'>← runs</a></p>"
    if row is None:
        head += "<div class='card muted'>Run không có trong DB. Vẫn thử đọc report từ storage…</div>"
        status_card = ""
        edit_card = ""
    else:
        phase = {"RUNNING_PHASE_1A": "đang sinh câu hỏi…",
                 "RUNNING_PHASE_1B": "đang debate…"}.get(status, "")
        status_card = (
            f"<div class='card'><h2>Run {html.escape(run_id[:8])}</h2>"
            f"<p>Status: {_badge(status)} <span class='muted'>{html.escape(phase)}</span></p>"
            f"<p class='muted'>{html.escape((row['title'] or ''))} · {html.escape(str(row['created_at'])[:19])}</p>"
            f"<details><summary class='muted'>current_artifacts</summary>"
            f"<pre>{html.escape(json.dumps(json.loads(row['current_artifacts'] or '{}'), indent=2, ensure_ascii=False))}</pre>"
            f"</details></div>"
        )
        edit_card = _run_edit_form(
            run_id,
            row["title"] or "",
            row["metadata"] or "{}",
            status,
        )

    report_path = _find_report_path(run_id)
    idle = _progress_idle_seconds(time.time())
    stale = _looks_stale(running, report_path is not None, idle)
    if status == "PAUSED_AT_GATE_1":
        body = head + status_card + edit_card + _gate1_review_page(run_id)
        return _page(f"Run {run_id[:8]} · Gate 1", body)
    elif report_path is None and running:
        card = _stale_card(run_id, idle, run_status=status) if stale else _progress_card()
        body = head + status_card + edit_card + card
    else:
        body = head + status_card + edit_card + _render_report(run_id)
    # Auto-refresh only while genuinely progressing: not once the report exists,
    # and not when stale — refreshing a dead run would falsely imply it's alive.
    live = running and report_path is None and not stale
    head_extra = "<meta http-equiv='refresh' content='5'>" if live else ""
    return _page(f"Run {run_id[:8]}", body, head_extra=head_extra)


def _progress_log() -> Path:
    return Path(_config().storage_root) / "ui_logs" / "start.log"


def _is_progress_line(ln: str) -> bool:
    """True for the human-readable progress lines the child emits to its log."""
    return (
        ln.startswith("[debate]") or ln.strip().startswith("round ")
        or "[Done]" in ln or "[Aborted]" in ln or "[Phase" in ln
        or "Pipeline error" in ln
    )


def _recent_progress(max_lines: int = 30) -> list[str]:
    log = _progress_log()
    if not log.exists():
        return []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    keep = [ln for ln in lines if _is_progress_line(ln)]
    return keep[-max_lines:]


def _progress_idle_seconds(now: float) -> float | None:
    """Seconds since the progress log was last written, or None if absent."""
    try:
        return now - _progress_log().stat().st_mtime
    except OSError:
        return None


def _looks_stale(
    running: bool,
    has_report: bool,
    idle_seconds: float | None,
    threshold: int = STALE_SECONDS,
) -> bool:
    """A run claims RUNNING but its progress log has gone silent past the
    threshold — the background process almost certainly died, orphaning it.

    Heuristic note: the log is shared across runs, so if a *different* run is
    actively writing, this returns False (fresh mtime). Good enough for the
    common single-run local case; it never produces a false "stale".
    """
    if not running or has_report or idle_seconds is None:
        return False
    return idle_seconds >= threshold


def _progress_card() -> str:
    lines = _recent_progress()
    pre = html.escape("\n".join(lines)) if lines else "(chưa có dòng tiến độ nào — chờ vài giây)"
    return (
        "<div class='card'><h2>Đang chạy — tiến độ gần nhất</h2>"
        "<p class='muted'>Trang tự refresh mỗi 5s. Debate report đầy đủ sẽ hiện khi chuyển sang "
        "PAUSED_AT_GATE_1.</p>"
        f"<pre>{pre}</pre></div>"
    )


def _spawn_resume_executor(run_id: str) -> None:
    """Spawn resume_executor as a detached background process."""
    cfg = _config()
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [
            sys.executable, "-m",
            "ai_dev_system.task_graph.resume_executor",
            "--id", run_id,
            "--storage-root", str(cfg.storage_root),
            "--database-url", str(cfg.database_url),
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        **popen_kwargs,
    )


def _stale_card(run_id: str, idle_seconds: float | None, run_status: str = "") -> str:
    """Shown when a RUNNING run's progress log has gone silent — the background
    process likely died. Stops the misleading auto-refresh and offers cleanup.
    """
    mins = int((idle_seconds or 0) // 60)
    lines = _recent_progress()
    pre = html.escape("\n".join(lines)) if lines else "(không có dòng tiến độ)"
    rid = html.escape(run_id)

    resume_btn = ""
    if run_status == "RUNNING_EXECUTION":
        resume_btn = (
            "<form method='post' action='/resume' style='display:inline;margin-right:12px'>"
            f"<input type='hidden' name='id' value='{rid}'>"
            "<button type='submit' style='background:#1a4b6b'>▶ Làm tiếp</button>"
            "</form>"
        )

    return (
        "<div class='card'><h2>⚠ Tiến trình có vẻ đã dừng</h2>"
        f"<p class='caveat'>Run đang ở trạng thái RUNNING nhưng log tiến độ đã đứng yên "
        f"~{mins} phút. Tiến trình debate nền nhiều khả năng đã chết (vd: server webui bị tắt "
        "giữa chừng). Trang đã ngừng tự refresh. Bạn có thể đánh dấu run này là ABORTED rồi "
        "chạy lại project.</p>"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px'>"
        + resume_btn +
        "<form method='post' action='/abort' style='display:inline;margin:0'>"
        f"<input type='hidden' name='id' value='{rid}'>"
        "<button type='submit'>Đánh dấu ABORTED &amp; dọn run</button>"
        "</form>"
        "</div>"
        f"<pre>{pre}</pre></div>"
    )


def _abort_run(run_id: str) -> None:
    """Mark an orphaned RUNNING run as ABORTED. No-op for terminal runs so we
    never clobber a COMPLETED/PAUSED state."""
    from ai_dev_system.db.repos.runs import RunRepo

    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row and str(row["status"]).startswith("RUNNING"):
            RunRepo(conn).update_status(run_id, "ABORTED")
            conn.commit()
    finally:
        conn.close()


def _render_task_spec(task: dict, facets: dict, spec_id: str | None = None) -> str:
    def _row(key: str) -> str:
        f = facets.get(key) or {"status": "needs_human", "content": "", "reason": ""}
        status = f.get("status")
        content = f.get("content") or ""
        if spec_id:
            escaped = html.escape(content)
            reasoning = html.escape(str(f.get("reasoning") or "")).strip()
            reasoning_block = (
                f"<details><summary class='muted' style='font-size:12px;cursor:pointer'>"
                f"reasoning</summary><div class='muted' style='font-size:12px;margin:4px 0 6px'>"
                f"{reasoning}</div></details>"
                if reasoning else ""
            )
            val = (
                reasoning_block
                + f"<textarea name='facet_{html.escape(key)}' rows='3' "
                f"placeholder='Nhập nội dung...'>{escaped}</textarea>"
            )
        elif status == "filled" and content:
            val = html.escape(content)
        elif status == "na":
            val = f"<span class='muted'>N/A — {html.escape(str(f.get('reason') or ''))}</span>"
        else:
            val = "<span class='caveat'>(cần làm rõ)</span>"
        return f"<tr><td class='muted'>{html.escape(key)}</td><td>{val}</td></tr>"

    spec_rows = "".join(_row(k) for k in SPEC_FACET_KEYS)
    exec_rows = "".join(_row(k) for k in EXEC_FACET_KEYS)
    title = html.escape(str(task.get("title") or "Task"))
    table = (
        "<table>"
        "<tr><th colspan='2' style='color:#5fb0f0;padding-top:10px'>Spec facets (13)</th></tr>"
        + spec_rows
        + "<tr><th colspan='2' style='color:#5fd07f;padding-top:14px'>Implementation documents (7)</th></tr>"
        + exec_rows
        + "</table>"
    )
    if spec_id:
        return (
            f"<form method='POST' action='/task-spec'>"
            f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
            f"<div class='card'><h2>Task spec · {title}</h2>"
            f"{table}"
            f"<button type='submit'>Lưu &amp; Duyệt</button>"
            f"</div></form>"
        )
    return f"<div class='card'><h2>Task spec · {title}</h2>{table}</div>"


def _save_task_spec_edits(spec_id: str, edits: dict, *, storage_root: str) -> None:
    path = Path(storage_root) / "task_specs" / f"{spec_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    facets = data.get("facets") or {}
    for key in FACET_KEYS:
        if key in edits:
            content = (edits[key] or "").strip()
            if content:
                facets[key] = {"status": "filled", "content": content, "reason": ""}
            elif (facets.get(key) or {}).get("status") != "na":
                facets[key] = {"status": "needs_human", "content": "", "reason": ""}
            # if stored status is "na" and content is blank, leave it unchanged
    data["facets"] = facets
    data["approved"] = True
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_task_spec(task: dict, facets: dict, *, storage_root: str) -> Path:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", str(task.get("title") or "task").lower()).strip("-")[:40] or "task"
    path = out_dir / f"{slug}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(
        json.dumps({"task": task, "facets": facets}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _task_spec_log_lines(spec_id: str) -> list[str]:
    log_path = Path(_config().storage_root) / "task_specs" / f"{spec_id}.log"
    if not log_path.exists():
        return []
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        return []


def _task_spec_log_card(spec_id: str, title: str = "Log tiến trình") -> str:
    lines = _task_spec_log_lines(spec_id)
    pre = html.escape("\n".join(lines)) if lines else "(chưa có log — worker đang khởi động…)"
    return f"<div class='card'><h2>{html.escape(title)}</h2><pre>{pre}</pre></div>"


def _task_spec_page(spec_id: str) -> bytes:
    path = Path(_config().storage_root) / "task_specs" / f"{spec_id}.json"
    if not path.exists():
        return _page("task spec", "<div class='card muted'>Không tìm thấy TaskSpec. "
                     "<a href='/'>← trang chủ</a></div>")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _page("task spec", f"<div class='card muted'>Lỗi đọc TaskSpec: {html.escape(str(exc))}</div>")
    status = data.get("status")
    if status == "done":
        facets = data.get("facets") or {}
        spec_needs_human = sum(
            1 for k, f in facets.items()
            if k in SPEC_FACET_KEYS and f.get("status") == "needs_human"
        )
        warning = ""
        if spec_needs_human == len(SPEC_FACET_KEYS):
            log_lines = _task_spec_log_lines(spec_id)
            log_pre = html.escape("\n".join(log_lines)) if log_lines else "(không có log)"
            warning = (
                "<div class='card'><h2>⚠ Tất cả facets đều trống</h2>"
                "<p class='caveat'>Claude CLI đã chạy nhưng không trả về nội dung nào — "
                "có thể đã timeout hoặc gặp lỗi nội bộ. Xem log bên dưới để biết chi tiết.</p>"
                f"<pre>{log_pre}</pre></div>"
            )
        approved_badge = ("<p><span class='badge b-done'>Đã duyệt ✓</span></p>"
                          if data.get("approved") else "")
        return _page("Task spec",
                     warning
                     + approved_badge
                     + _render_task_spec(data.get("task") or {}, facets, spec_id)
                     + "<p class='muted'><a href='/'>← trang chủ</a></p>")
    if status == "error":
        err = html.escape(str(data.get("error") or ""))
        return _page("task spec",
                     "<div class='card'><h2>Lỗi sinh TaskSpec</h2>"
                     f"<p class='caveat'>{err}</p></div>"
                     + _task_spec_log_card(spec_id))
    # running (or anything else)
    return _page("task spec",
                 "<div class='card'><h2>Đang chạy — sinh TaskSpec (agentic đọc repo)…</h2>"
                 "<p class='muted'>Trang tự refresh mỗi 5s.</p></div>"
                 + _task_spec_log_card(spec_id),
                 head_extra="<meta http-equiv='refresh' content='5'>")


def _spawn_task_spec_worker(idea: str, repo: str) -> str:
    idea = (idea or "")[:8000]
    repo = (repo or "")[:1000]
    spec_id = uuid.uuid4().hex[:12]
    out_dir = Path(_config().storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{spec_id}.json").write_text(
        json.dumps({"status": "running", "idea": idea, "repo": repo}, ensure_ascii=False),
        encoding="utf-8")
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
         "--id", spec_id, "--idea", idea, "--repo", repo,
         "--storage-root", str(_config().storage_root),
         "--database-url", str(_config().database_url)],
        cwd=str(Path(__file__).resolve().parents[2]), **popen_kwargs,
    )
    return spec_id


def _spawn_task_executor(spec_id: str) -> None:
    """Spawn single_task_executor as a detached background process."""
    cfg = _config()
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [
            sys.executable, "-m",
            "ai_dev_system.task_graph.single_task_executor",
            "--id", spec_id,
            "--storage-root", str(cfg.storage_root),
            "--database-url", str(cfg.database_url),
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        **popen_kwargs,
    )


def _task_exec_status(spec_id: str) -> dict:
    path = Path(_config().storage_root) / "task_specs" / f"{spec_id}-exec.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return {}


def _task_exec_log_lines(spec_id: str) -> list[str]:
    log_path = Path(_config().storage_root) / "task_specs" / f"{spec_id}-exec.log"
    if not log_path.exists():
        return []
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
    except OSError:
        return []


def _task_exec_diff(spec_id: str, run_id: str, cfg) -> str:
    """Read diff.txt from the EXECUTION_LOG artifact for this run."""
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            """
            SELECT a.content_ref FROM artifacts a
            JOIN task_runs tr ON tr.output_artifact_id = a.artifact_id
            WHERE tr.run_id = ? AND a.artifact_type = 'EXECUTION_LOG'
            ORDER BY a.created_at DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    finally:
        conn.close()
    if not row:
        return "(diff chưa có — execution đang xử lý hoặc không có output)"
    diff_file = Path(row["content_ref"]) / "diff.txt"
    if not diff_file.exists():
        return "(diff.txt không tìm thấy trong artifact)"
    try:
        return diff_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(lỗi đọc diff: {exc})"


def _task_exec_page(spec_id: str) -> bytes:
    exec_st = _task_exec_status(spec_id)
    log_lines = _task_exec_log_lines(spec_id)
    log_pre = html.escape("\n".join(log_lines)) if log_lines else "(chưa có log)"
    nav = "<p><a href='/'>← trang chủ</a></p>"
    branch = html.escape(exec_st.get("branch") or "")
    base = html.escape(exec_st.get("base_branch") or "")
    run_id = exec_st.get("run_id") or ""
    status = exec_st.get("status") or ""

    if not exec_st:
        return _page(
            "task exec",
            nav + "<div class='card muted'>Chưa có thông tin execution. "
            "Thử refresh sau vài giây.</div>",
            head_extra="<meta http-equiv='refresh' content='3'>",
        )

    branch_card = (
        "<div class='card'>"
        f"<h2>Branch</h2>"
        f"<p class='muted'>Branch: <b>{branch}</b> (từ {base})</p>"
        f"<p class='muted'>Run: {html.escape(run_id[:8]) if run_id else '—'}</p>"
        "</div>"
    )
    log_card = (
        f"<div class='card'><h2>Log tiến trình</h2><pre>{log_pre}</pre></div>"
    )

    if status == "running":
        return _page(
            "Task execution",
            nav + branch_card + log_card,
            head_extra="<meta http-equiv='refresh' content='5'>",
        )

    if status == "error":
        err = html.escape(exec_st.get("error") or "")
        return _page(
            "Task execution",
            nav + branch_card
            + f"<div class='card'><h2>⚠ Lỗi execution</h2>"
            f"<p class='caveat'>{err}</p></div>"
            + log_card,
        )

    # status == "done"
    cfg = _config()
    diff_text = _task_exec_diff(spec_id, run_id, cfg)
    diff_html = html.escape(diff_text)
    exec_status_badge = _badge(exec_st.get("exec_status") or "COMPLETED")
    diff_card = (
        f"<div class='card'><h2>Git diff — {exec_status_badge}</h2>"
        f"<pre style='max-height:600px;overflow-y:auto'>{diff_html}</pre></div>"
    )
    sid_escaped = html.escape(spec_id)
    action_card = (
        "<div class='card'><h2>Hành động</h2>"
        "<form method='post' action='/task-exec' style='display:inline;margin-right:12px'>"
        f"<input type='hidden' name='id' value='{sid_escaped}'>"
        "<input type='hidden' name='action' value='accept'>"
        "<button type='submit' style='background:#1a6b2a'>✓ Accept branch</button>"
        "</form>"
        "<form method='post' action='/task-exec' style='display:inline'>"
        f"<input type='hidden' name='id' value='{sid_escaped}'>"
        "<input type='hidden' name='action' value='reject'>"
        "<button type='submit' style='background:#6b1a1a'>✗ Reject &amp; xóa branch</button>"
        "</form>"
        f"<p class='muted' style='margin-top:10px'>Accept: giữ branch <b>{branch}</b>, "
        f"bạn merge thủ công. Reject: xóa branch, quay lại <b>{base}</b>.</p></div>"
    )
    return _page("Task execution", nav + branch_card + diff_card + action_card + log_card)


def _render_report(run_id: str) -> str:
    path = _find_report_path(run_id)
    if path is None:
        return "<div class='card muted'>Chưa có debate report cho run này.</div>"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"<div class='card muted'>Không đọc được report: {html.escape(str(exc))}</div>"

    results = data.get("results", [])
    items = []
    for q in results:
        qq = q.get("question", {})
        fin = q.get("final", {})
        rounds = q.get("rounds", [])
        mod = (fin.get("moderator_summary") or "").strip()
        caveat = (fin.get("caveat") or "").strip()
        items.append(
            "<div class='q'>"
            f"<div class='head'>[{html.escape(qq.get('classification',''))}] {html.escape(qq.get('id',''))} · "
            f"{html.escape(qq.get('agent_a',''))} vs {html.escape(qq.get('agent_b',''))} · "
            f"{len(rounds)} rounds · {html.escape(str(fin.get('resolution_status','')))} "
            f"(conf {html.escape(str(fin.get('confidence','')))})</div>"
            f"<div class='text'>{html.escape(qq.get('text',''))}</div>"
            f"<div class='mod'>{html.escape(mod[:600])}</div>"
            + (f"<div class='caveat'>⚠ {html.escape(caveat[:400])}</div>" if caveat else "")
            + "</div>"
        )
    body = "".join(items) or "<span class='muted'>(report rỗng)</span>"
    return f"<div class='card'><h2>Debate report · {len(results)} câu hỏi</h2>{body}</div>"


def _start(name: str, idea: str, mode: str) -> None:
    env = dict(os.environ)
    if mode == "stub":
        env["AI_DEV_STUB_LLM"] = "1"
    else:
        env.pop("AI_DEV_STUB_LLM", None)
    log_dir = Path(_config().storage_root) / "ui_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logf = open(log_dir / "start.log", "a", encoding="utf-8", errors="replace")
    logf.write(f"\n==== start name={name!r} mode={mode} ====\n")
    logf.flush()
    # Detach the child from this server's console. Without this, the debate
    # shares the server's console: closing the terminal or Ctrl+C-ing the
    # server delivers the same signal to the debate and kills it mid-run
    # (the bug that left runs stuck at RUNNING with frozen progress).
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "ai_dev_system.cli.main", "start",
         "--project-name", name, "--idea", idea],
        env=env, stdout=logf, stderr=subprocess.STDOUT,
        cwd=str(Path(__file__).resolve().parents[2]),
        **popen_kwargs,
    )


def _spec_task(idea: str, mode: str) -> bytes:
    idea = (idea or "").strip()
    if not idea:
        return _page("task spec", "<div class='card muted'>Nhập mô tả task trước đã. "
                     "<a href='/'>← về trang chủ</a></div>")
    try:
        if mode == "stub":
            from ai_dev_system.debate.llm import StubDebateLLMClient
            llm = StubDebateLLMClient()
        else:
            from ai_dev_system.llm_factory import make_real_llm_client
            llm = make_real_llm_client()
    except RuntimeError as exc:
        return _page("task spec", f"<div class='card muted'>LLM chưa cấu hình: "
                     f"{html.escape(str(exc))}</div>")
    from ai_dev_system.task_graph.single_task import spec_single_task
    result = spec_single_task(idea, llm)
    path = _save_task_spec(result["task"], result["facets"],
                           storage_root=_config().storage_root)
    body = (
        _render_task_spec(result["task"], result["facets"])
        + f"<p class='muted'>Đã lưu: {html.escape(str(path))} · <a href='/'>← trang chủ</a></p>"
    )
    return _page("Task spec", body)


# ---------------------------------------------------------------------------
# Gate 1 review helpers
# ---------------------------------------------------------------------------

def _apply_brief_edits_from_state(brief: dict, state) -> dict:
    """Return brief dict with all edits from session state applied sequentially."""
    from ai_dev_system.gate.gate1_review.editor import apply_edit
    current = dict(brief)
    for entry in state.brief_edits:
        result = apply_edit(current, entry.field_name, entry.operation, entry.value)
        if result.accepted:
            current = result.brief
    return current


def _render_gate1_form(run_id: str, ctx, state, extra_html: str = "") -> str:
    """Render the Gate 1 review HTML form (not a full page)."""
    from ai_dev_system.gate.gate1_review.editor import EDITABLE_FIELDS, LIST_FIELDS

    rid = html.escape(run_id)

    result_by_id = {
        qdr["question"]["id"]: qdr
        for qdr in ctx.debate_report.get("results", [])
    }
    brief = _apply_brief_edits_from_state(dict(ctx.brief), state)

    scope_warning = ""
    if state.scope_affected:
        scope_warning = (
            "<div class='card'><h2>⚠ Scope đã thay đổi</h2>"
            "<p class='caveat'>Bạn đã chỉnh sửa scope_in hoặc scope_out. "
            "Cần cân nhắc re-trigger debate pipeline để đảm bảo câu hỏi vẫn phản ánh đúng scope mới.</p></div>"
        )

    q_items = []
    for q in ctx.questions:
        qdr = result_by_id.get(q.id, {})
        final = qdr.get("final", {})
        a_pos = html.escape(str(final.get("agent_a_position") or "")[:300])
        b_pos = html.escape(str(final.get("agent_b_position") or "")[:300])
        mod_sum = html.escape(str(final.get("moderator_summary") or "")[:300])

        ri = state.resolved.get(q.id)
        cur_choice = ri.choice if ri else ""
        cur_override = html.escape(ri.override_text or "") if ri else ""
        a_chk = " checked" if cur_choice == "agent_a" else ""
        b_chk = " checked" if cur_choice == "agent_b" else ""
        m_chk = " checked" if cur_choice == "moderator" else ""
        o_chk = " checked" if cur_choice == "override" else ""
        qid = html.escape(q.id)

        q_items.append(
            f"<div class='q'>"
            f"<div class='head'>[{html.escape(q.classification)}] {qid} · "
            f"{html.escape(q.agent_a)} vs {html.escape(q.agent_b)}</div>"
            f"<div class='text'>{html.escape(q.text)}</div>"
            f"<label><input type='radio' name='q_{qid}_choice' value='agent_a'{a_chk}> "
            f"Agent A: {a_pos}</label><br>"
            f"<label><input type='radio' name='q_{qid}_choice' value='agent_b'{b_chk}> "
            f"Agent B: {b_pos}</label><br>"
            f"<label><input type='radio' name='q_{qid}_choice' value='moderator'{m_chk}> "
            f"Moderator: {mod_sum}</label><br>"
            f"<label><input type='radio' name='q_{qid}_choice' value='override'{o_chk}> "
            f"Override (nhập thủ công)</label>"
            f"<div style='margin-left:20px'>"
            f"<textarea name='q_{qid}_override_text' rows='2' "
            f"placeholder='Nhập câu trả lời override...' style='margin-top:6px'>{cur_override}</textarea>"
            f"</div>"
            f"</div>"
        )

    brief_rows = []
    for field_name in sorted(EDITABLE_FIELDS):
        cur_val = brief.get(field_name, "")
        ops = ["set", "append", "remove"] if field_name in LIST_FIELDS else ["set"]
        opts = "".join(f"<option value='{op}'>{op}</option>" for op in ops)
        fn = html.escape(field_name)
        brief_rows.append(
            f"<tr>"
            f"<td class='muted'>{fn}</td>"
            f"<td class='muted' style='font-size:12px'>{html.escape(str(cur_val)[:80])}</td>"
            f"<td><select name='brief_{fn}_op' style='width:auto'>"
            f"<option value=''>--</option>{opts}</select></td>"
            f"<td><input name='brief_{fn}_value' placeholder='giá trị mới'></td>"
            f"</tr>"
        )

    brief_section = (
        "<div class='card'><h2>Chỉnh sửa brief (tuỳ chọn)</h2>"
        "<table><tr><th>Field</th><th>Hiện tại</th><th>Operation</th><th>Giá trị mới</th></tr>"
        + "".join(brief_rows)
        + "</table></div>"
    )

    questions_section = (
        f"<div class='card'><h2>Gate 1 Review · {html.escape(ctx.project_name)} · "
        f"{len(ctx.questions)} câu hỏi</h2>"
        + "".join(q_items)
        + "<div style='margin-top:16px'>"
        + "<label style='display:block;margin-bottom:8px'>"
        + "<input type='checkbox' name='approved_all' value='1'> "
        + "Duyệt tất cả câu hỏi tự động (APPROVED_ALL)</label>"
        + "<div style='display:flex;gap:12px;flex-wrap:wrap'>"
        + "<button type='submit' formaction='/gate1-save' "
        + "style='background:#1a4b6b'>💾 Lưu tiến trình</button>"
        + "<button type='submit'>✓ Duyệt &amp; tiếp tục</button>"
        + "</div></div></div>"
    )

    return (
        extra_html
        + scope_warning
        + f"<form method='post' action='/gate1-approve'>"
        + f"<input type='hidden' name='id' value='{rid}'>"
        + questions_section
        + brief_section
        + "</form>"
    )


def _gate1_review_page(run_id: str, extra_html: str = "") -> str:
    """Load gate1 context + state, return review form HTML."""
    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        from ai_dev_system.gate.gate1_review.loader import load_gate1_context
        from ai_dev_system.gate.gate1_review.state import load_state
        try:
            ctx = load_gate1_context(run_id, conn)
        except ValueError as exc:
            return (
                "<div class='card'><h2>Lỗi tải Gate 1</h2>"
                f"<p class='caveat'>{html.escape(str(exc))}</p></div>"
            )
        except FileNotFoundError as exc:
            return (
                "<div class='card'><h2>Lỗi tải artifact</h2>"
                f"<p class='caveat'>{html.escape(str(exc))}</p></div>"
            )
        state = load_state(run_id, conn)
    finally:
        conn.close()

    return _render_gate1_form(run_id, ctx, state, extra_html=extra_html)


def _parse_gate1_choices(form: dict, questions) -> dict:
    """Extract q_*_choice and q_*_override_text from parse_qs form dict."""
    choices = {}
    for q in questions:
        choice = (form.get(f"q_{q.id}_choice") or [""])[0].strip()
        if choice in ("agent_a", "agent_b", "moderator", "override"):
            override_text = None
            if choice == "override":
                override_text = (form.get(f"q_{q.id}_override_text") or [""])[0].strip() or None
            choices[q.id] = (choice, override_text)
    return choices


def _do_gate1_save(run_id: str, form: dict) -> str:
    """Process /gate1-save POST. Updates session state. Returns redirect URL."""
    from ai_dev_system.gate.gate1_review.loader import load_gate1_context
    from ai_dev_system.gate.gate1_review.state import load_state, save_state
    from ai_dev_system.gate.gate1_review.editor import apply_edit, EDITABLE_FIELDS, NON_EDITABLE_FIELDS

    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        ctx = load_gate1_context(run_id, conn)
        state = load_state(run_id, conn)

        if (form.get("approved_all") or [""])[0].strip() in ("1", "true"):
            state.approved_all = True

        for q_id, (choice, override_text) in _parse_gate1_choices(form, ctx.questions).items():
            state.record_choice(q_id, choice, override_text)

        brief = _apply_brief_edits_from_state(dict(ctx.brief), state)
        for field_name in EDITABLE_FIELDS | NON_EDITABLE_FIELDS:
            op = (form.get(f"brief_{field_name}_op") or [""])[0].strip()
            val = (form.get(f"brief_{field_name}_value") or [""])[0].strip()
            if not op or not val:
                continue
            result = apply_edit(brief, field_name, op, val)
            if result.accepted:
                state.record_brief_edit(field_name, op, val)
                brief = result.brief

        save_state(run_id, state, conn)
    finally:
        conn.close()

    return f"/run?id={urllib.parse.quote(run_id)}"


def _do_gate1_approve(run_id: str, form: dict) -> bytes | str:
    """Process /gate1-approve POST. Validates, finalizes, or returns error page bytes."""
    from ai_dev_system.gate.gate1_review.loader import load_gate1_context
    from ai_dev_system.gate.gate1_review.state import load_state, save_state, clear_state
    from ai_dev_system.gate.gate1_review.editor import apply_edit, EDITABLE_FIELDS, NON_EDITABLE_FIELDS
    from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision

    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        ctx = load_gate1_context(run_id, conn)
        state = load_state(run_id, conn)

        if (form.get("approved_all") or [""])[0].strip() in ("1", "true"):
            state.approved_all = True

        for q_id, (choice, override_text) in _parse_gate1_choices(form, ctx.questions).items():
            state.record_choice(q_id, choice, override_text)

        brief = _apply_brief_edits_from_state(dict(ctx.brief), state)
        for field_name in EDITABLE_FIELDS | NON_EDITABLE_FIELDS:
            op = (form.get(f"brief_{field_name}_op") or [""])[0].strip()
            val = (form.get(f"brief_{field_name}_value") or [""])[0].strip()
            if not op or not val:
                continue
            result = apply_edit(brief, field_name, op, val)
            if result.accepted:
                state.record_brief_edit(field_name, op, val)
                brief = result.brief

        save_state(run_id, state, conn)

        unresolved = [q.id for q in ctx.questions if not state.is_resolved(q.id)]
        if unresolved:
            ids_str = ", ".join(html.escape(q) for q in unresolved)
            error_card = (
                "<div class='card'><h2>⚠ Chưa giải quyết tất cả câu hỏi</h2>"
                f"<p class='caveat'>Còn {len(unresolved)} câu hỏi chưa được chọn: {ids_str}</p></div>"
            )
            form_html = _render_gate1_form(run_id, ctx, state, extra_html=error_card)
            title = f"Gate 1 · {html.escape(ctx.project_name)}"
            return _page(title, "<p><a href='/'>← runs</a></p>" + form_html)

        result_by_id = {
            qdr["question"]["id"]: qdr
            for qdr in ctx.debate_report.get("results", [])
        }
        decisions = []
        for q in ctx.questions:
            qdr = result_by_id.get(q.id, {})
            final = qdr.get("final", {})
            ri = state.resolved.get(q.id)

            if ri is None:
                answer = final.get("moderator_summary") or ""
                resolution_type = "CONSENSUS"
                rationale = ""
            elif ri.choice == "agent_a":
                answer = final.get("agent_a_position") or ""
                resolution_type = "CONSENSUS"
                rationale = ""
            elif ri.choice == "agent_b":
                answer = final.get("agent_b_position") or ""
                resolution_type = "CONSENSUS"
                rationale = ""
            elif ri.choice == "moderator":
                answer = final.get("moderator_summary") or ""
                resolution_type = "CONSENSUS"
                rationale = ""
            else:
                answer = ri.override_text or ""
                resolution_type = "FORCED_HUMAN"
                rationale = ri.override_text or ""

            decisions.append(Decision(
                question_id=q.id,
                question_text=q.text,
                classification=q.classification,
                resolution_type=resolution_type,
                answer=answer,
                options_considered=[
                    final.get("agent_a_position") or "",
                    final.get("agent_b_position") or "",
                ],
                rationale=rationale,
            ))

        finalize_gate1(run_id, decisions, cfg.storage_root, conn)
        clear_state(run_id, conn)
    finally:
        conn.close()

    return f"/run?id={urllib.parse.quote(run_id)}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, body: bytes, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send(_home())
            elif parsed.path == "/run":
                qs = urllib.parse.parse_qs(parsed.query)
                rid = (qs.get("id") or [""])[0]
                self._send(_run_detail(rid))
            elif parsed.path == "/task-spec":
                qs = urllib.parse.parse_qs(parsed.query)
                self._send(_task_spec_page((qs.get("id") or [""])[0]))
            elif parsed.path == "/task-exec":
                qs = urllib.parse.parse_qs(parsed.query)
                self._send(_task_exec_page((qs.get("id") or [""])[0]))
            else:
                self._send(_page("404", "<div class='card'>Not found. <a href='/'>home</a></div>"), 404)
        except Exception as exc:  # noqa: BLE001
            self._send(_page("error", f"<div class='card'><pre>{html.escape(repr(exc))}</pre></div>"), 500)

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/abort":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                rid = (form.get("id") or [""])[0].strip()
                if rid:
                    _abort_run(rid)
                back = "/run?id=" + urllib.parse.quote(rid) if rid else "/"
                body = (
                    "<div class='card'><h2>Đã đánh dấu ABORTED ✓</h2>"
                    f"<p class='muted'>Run đã được dọn. <a href='{html.escape(back)}'>← về run</a></p></div>"
                )
                self._send(_page("aborted", body,
                                 head_extra=f"<meta http-equiv='refresh' content='2;url={html.escape(back)}'>"))
            elif path == "/task-spec":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                spec_id = (form.get("id") or [""])[0].strip()
                if spec_id:
                    edits = {key: (form.get(f"facet_{key}") or [""])[0] for key in FACET_KEYS}
                    _save_task_spec_edits(spec_id, edits, storage_root=str(_config().storage_root))
                    # Spawn executor when repo is available; redirect to exec progress page
                    try:
                        _spec_data = json.loads(
                            (Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
                            .read_text(encoding="utf-8")
                        )
                        if _spec_data.get("repo"):
                            # Only spawn if not already running or done
                            _exec_st = _task_exec_status(spec_id)
                            if _exec_st.get("status") not in ("running", "done"):
                                _spawn_task_executor(spec_id)
                            redirect = f"/task-exec?id={urllib.parse.quote(spec_id)}"
                        else:
                            redirect = f"/task-spec?id={urllib.parse.quote(spec_id)}"
                    except Exception:  # noqa: BLE001
                        redirect = f"/task-spec?id={urllib.parse.quote(spec_id)}"
                else:
                    redirect = "/"
                self._send(_page("saved", "<div class='card'><h2>Đã lưu &amp; duyệt ✓</h2></div>",
                                 head_extra=f"<meta http-equiv='refresh' content='1;url={html.escape(redirect)}'>"))
            elif path == "/task-exec":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                spec_id = (form.get("id") or [""])[0].strip()
                action = (form.get("action") or [""])[0].strip()
                if not spec_id:
                    self._send(
                        _page("error", "<div class='card muted'>Thiếu spec id.</div>"), 400
                    )
                    return
                exec_st = _task_exec_status(spec_id)
                branch = exec_st.get("branch") or ""
                base = exec_st.get("base_branch") or ""
                repo = ""
                try:
                    _s = json.loads(
                        (Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
                        .read_text(encoding="utf-8")
                    )
                    repo = _s.get("repo") or ""
                except Exception:  # noqa: BLE001
                    pass
                if action == "accept":
                    body = (
                        "<div class='card'><h2>Branch accepted ✓</h2>"
                        f"<p>Branch <b>{html.escape(branch)}</b> đã được giữ lại.</p>"
                        f"<p class='muted'>Để merge: "
                        f"<code>git checkout {html.escape(base)} &amp;&amp; "
                        f"git merge --no-ff {html.escape(branch)}</code></p>"
                        "<p><a href='/'>← trang chủ</a></p></div>"
                    )
                    self._send(_page("accepted", body))
                elif action == "reject":
                    msg = "Branch đã bị xóa."
                    if branch and repo:
                        try:
                            subprocess.run(
                                ["git", "checkout", base],
                                cwd=repo, capture_output=True,
                                text=True, encoding="utf-8",
                            )
                            subprocess.run(
                                ["git", "branch", "-D", branch],
                                cwd=repo, capture_output=True,
                                text=True, encoding="utf-8",
                            )
                        except Exception as exc:  # noqa: BLE001
                            msg = f"Lỗi xóa branch: {html.escape(str(exc))}"
                    body = (
                        f"<div class='card'><h2>Branch rejected ✗</h2><p>{msg}</p>"
                        "<p><a href='/'>← trang chủ</a></p></div>"
                    )
                    self._send(_page("rejected", body))
                else:
                    self._send(
                        _page("error", "<div class='card muted'>Action không hợp lệ.</div>"), 400
                    )
            elif path == "/spec-task":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                idea = (form.get("idea") or [""])[0].strip()
                mode = (form.get("mode") or ["stub"])[0]
                repo = (form.get("repo") or [""])[0].strip()
                if repo:
                    spec_id = _spawn_task_spec_worker(idea, repo)
                    self._send(_page("task spec",
                        "<div class='card'><h2>Đã khởi động (agentic) ✓</h2>"
                        f"<p class='muted'>Đọc repo + sinh facet ở chạy nền.</p></div>",
                        head_extra=f"<meta http-equiv='refresh' content='2;url=/task-spec?id={urllib.parse.quote(spec_id)}'>"))
                else:
                    self._send(_spec_task(idea, mode))
            elif path == "/resume":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                rid = (form.get("id") or [""])[0].strip()
                if not rid:
                    self._send(_page("error", "<div class='card muted'>Thiếu run id.</div>"), 400)
                    return
                _spawn_resume_executor(rid)
                back = f"/run?id={urllib.parse.quote(rid)}"
                body = (
                    "<div class='card'><h2>Đã khởi động lại ✓</h2>"
                    f"<p class='muted'>Resume executor đang chạy nền cho run <b>{html.escape(rid[:8])}</b>.</p>"
                    f"<p><a href='{html.escape(back)}'>← về run</a></p></div>"
                )
                self._send(_page("resumed", body,
                                 head_extra=f"<meta http-equiv='refresh' content='3;url={html.escape(back)}'>"))
            elif path == "/run-edit":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                rid = (form.get("id") or [""])[0].strip()
                title = (form.get("title") or [""])[0]
                metadata_str = (form.get("metadata") or ["{}"])[0]
                result = _do_run_edit(rid, title, metadata_str)
                if isinstance(result, str):
                    self.send_response(302)
                    self.send_header("Location", result)
                    self.end_headers()
                else:
                    self._send(result, 400)
            elif path == "/run-approve":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                rid = (form.get("id") or [""])[0].strip()
                result = _do_run_approve(rid)
                if isinstance(result, str):
                    self.send_response(302)
                    self.send_header("Location", result)
                    self.end_headers()
                else:
                    self._send(result, 400)
            elif path in ("/gate1-save", "/gate1-approve"):
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                rid = (form.get("id") or [""])[0].strip()
                if not rid:
                    self._send(_page("error", "<div class='card muted'>Thiếu run id.</div>"), 400)
                    return
                if path == "/gate1-save":
                    redirect = _do_gate1_save(rid, form)
                    self.send_response(302)
                    self.send_header("Location", redirect)
                    self.end_headers()
                else:
                    result = _do_gate1_approve(rid, form)
                    if isinstance(result, str):
                        self.send_response(302)
                        self.send_header("Location", result)
                        self.end_headers()
                    else:
                        self._send(result, 200)
            elif path == "/start":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                name = (form.get("name") or [""])[0].strip() or "demo"
                idea = (form.get("idea") or [""])[0].strip()
                mode = (form.get("mode") or ["stub"])[0]
                if idea:
                    _start(name, idea, mode)
                note = (
                    "Stub: chỉ vài giây — trang sẽ tự refresh."
                    if mode == "stub"
                    else "Claude Max: chạy nhiều phút. Run xuất hiện ngay ở trạng thái "
                         "RUNNING_PHASE_1A; refresh để theo dõi tới PAUSED_AT_GATE_1."
                )
                body = (
                    "<div class='card'><h2>Đã khởi động debate ✓</h2>"
                    f"<p>{html.escape(name)} · chế độ <b>{html.escape(mode)}</b></p>"
                    f"<p class='muted'>{note}</p>"
                    "<p><a href='/'>← về danh sách runs ngay</a></p></div>"
                )
                self._send(_page("started", body, head_extra="<meta http-equiv='refresh' content='5;url=/'>"))
            else:
                self._send(_page("404", "<div class='card'>Not found.</div>"), 404)
        except Exception as exc:  # noqa: BLE001
            self._send(_page("error", f"<div class='card'><pre>{html.escape(repr(exc))}</pre></div>"), 500)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AI Dev System dashboard -> http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
