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
from ai_dev_system.task_graph.facets import FACET_KEYS

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
            "SELECT run_id, status, title, created_at FROM runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _find_report_path(run_id: str) -> Path | None:
    """Locate a run's debate_report.json via storage (by run_id)."""
    cfg = _config()
    p = Path(cfg.storage_root) / "runs" / run_id / "artifacts" / "debate_report" / "v1" / "debate_report.json"
    return p if p.exists() else None


def _home() -> bytes:
    runs = _list_runs()
    rows = "".join(
        f"<tr><td><a href='/run?id={html.escape(r['run_id'])}'>{html.escape(r['run_id'][:8])}</a></td>"
        f"<td>{_badge(r['status'])}</td><td>{html.escape((r.get('title') or '')[:48])}</td>"
        f"<td class='muted'>{html.escape(str(r.get('created_at') or ''))[:19]}</td></tr>"
        for r in runs
    ) or "<tr><td colspan='4' class='muted'>Chưa có run nào trong DB.</td></tr>"

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
      <label>Chế độ LLM</label>
      <select name='mode'>
        <option value='stub'>Stub — tức thì (facet giả = needs_human)</option>
        <option value='max'>Claude Max — thật (~vài chục giây)</option>
      </select>
      <button type='submit'>Sinh TaskSpec →</button>
    </form>
    <p class='muted'>Trả về 8 facet (Input/Auth/Business rule/DB/Response/Error/NFR/Test) cho task.</p></div>
    """
    return _page("AI Dev System", form + task_form + table)


def _run_detail(run_id: str) -> bytes:
    cfg = _config()
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            "SELECT run_id, status, title, current_artifacts, created_at FROM runs WHERE run_id = ?",
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

    report_path = _find_report_path(run_id)
    idle = _progress_idle_seconds(time.time())
    stale = _looks_stale(running, report_path is not None, idle)
    if report_path is None and running:
        card = _stale_card(run_id, idle) if stale else _progress_card()
        body = head + status_card + card
    else:
        body = head + status_card + _render_report(run_id)
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


def _stale_card(run_id: str, idle_seconds: float | None) -> str:
    """Shown when a RUNNING run's progress log has gone silent — the background
    process likely died. Stops the misleading auto-refresh and offers cleanup.
    """
    mins = int((idle_seconds or 0) // 60)
    lines = _recent_progress()
    pre = html.escape("\n".join(lines)) if lines else "(không có dòng tiến độ)"
    return (
        "<div class='card'><h2>⚠ Tiến trình có vẻ đã dừng</h2>"
        f"<p class='caveat'>Run đang ở trạng thái RUNNING nhưng log tiến độ đã đứng yên "
        f"~{mins} phút. Tiến trình debate nền nhiều khả năng đã chết (vd: server webui bị tắt "
        "giữa chừng). Trang đã ngừng tự refresh. Bạn có thể đánh dấu run này là ABORTED rồi "
        "chạy lại project.</p>"
        "<form method='post' action='/abort' style='margin:0'>"
        f"<input type='hidden' name='id' value='{html.escape(run_id)}'>"
        "<button type='submit'>Đánh dấu ABORTED &amp; dọn run</button>"
        "</form>"
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


def _render_task_spec(task: dict, facets: dict) -> str:
    rows = []
    for key in FACET_KEYS:
        f = facets.get(key) or {"status": "needs_human", "content": "", "reason": ""}
        status = f.get("status")
        if status == "filled" and f.get("content"):
            val = html.escape(str(f["content"]))
        elif status == "na":
            val = f"<span class='muted'>N/A — {html.escape(str(f.get('reason') or ''))}</span>"
        else:  # needs_human (or empty filled)
            val = "<span class='caveat'>(cần làm rõ)</span>"
        rows.append(f"<tr><td class='muted'>{html.escape(key)}</td><td>{val}</td></tr>")
    title = html.escape(str(task.get("title") or "Task"))
    return (
        f"<div class='card'><h2>Task spec · {title}</h2>"
        "<table>" + "".join(rows) + "</table></div>"
    )


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
            else:
                self._send(_page("404", "<div class='card'>Not found. <a href='/'>home</a></div>"), 404)
        except Exception as exc:  # noqa: BLE001
            self._send(_page("error", f"<div class='card'><pre>{html.escape(repr(exc))}</pre></div>"), 500)

    def do_POST(self):
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
        elif path == "/spec-task":
            length = int(self.headers.get("Content-Length", "0"))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            idea = (form.get("idea") or [""])[0].strip()
            mode = (form.get("mode") or ["stub"])[0]
            self._send(_spec_task(idea, mode))
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


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AI Dev System dashboard -> http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
