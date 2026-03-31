import os

from ai_dev_system.verification.report import TaskSummaryEntry


def collect_evidence(
    run_id: str,
    conn,
) -> tuple[dict[str, TaskSummaryEntry], list[str]]:
    """
    Gather per-task summaries and evidence text from completed task_runs.

    Returns:
        task_summary: dict[task_id → TaskSummaryEntry] for all SUCCESS task_runs
        evidence: list of text excerpts (one per task that has output), for LLM judge
    """
    rows = conn.execute(
        """
        SELECT task_id, status, output_artifact_id
        FROM task_runs
        WHERE run_id = %s AND status = 'SUCCESS'
        """,
        (run_id,),
    ).fetchall()

    task_summary: dict[str, TaskSummaryEntry] = {}
    evidence: list[str] = []

    for row in rows:
        task_id = row["task_id"]
        output_artifact_id = row["output_artifact_id"]

        output_text: list[str] = []
        if output_artifact_id:
            artifact_row = conn.execute(
                "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
                (output_artifact_id,),
            ).fetchone()
            if artifact_row:
                content_ref = artifact_row["content_ref"]
                output_text = _read_text_files(content_ref)

        task_summary[task_id] = TaskSummaryEntry(
            task_id=task_id,
            done_definition_met=True,  # SUCCESS status means agent confirmed done_definition
            output_artifact_id=str(output_artifact_id) if output_artifact_id else None,
            verification_step_results=output_text,
        )
        if output_text:
            combined = f"[{task_id}]\n" + "\n".join(output_text)
            evidence.append(combined)

    return task_summary, evidence


def _read_text_files(directory: str) -> list[str]:
    """Read all .txt and .log files in a directory. Returns list of file contents."""
    lines = []
    if not os.path.isdir(directory):
        return lines
    for fname in sorted(os.listdir(directory)):
        if fname.endswith((".txt", ".log", ".json")) and not fname.startswith("_"):
            fpath = os.path.join(directory, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read(4096)  # cap at 4KB per file to stay within LLM context
                lines.append(content)
            except OSError:
                pass
    return lines
