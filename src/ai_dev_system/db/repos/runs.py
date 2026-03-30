import uuid
import psycopg
import psycopg.types.json


class RunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def create(self, project_id: str, pipeline_type: str) -> str:
        """Create a new run. Returns run_id."""
        run_id = str(uuid.uuid4())
        initial_artifacts = {
            "initial_brief_id": None, "debate_report_id": None,
            "decision_log_id": None, "approved_answers_id": None,
            "approved_brief_id": None, "spec_bundle_id": None,
            "task_graph_gen_id": None, "task_graph_approved_id": None,
        }
        self.conn.execute("""
            INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
            VALUES (%s, %s, 'RUNNING_PHASE_1A', %s, %s, '{}')
        """, (run_id, project_id, f"Pipeline: {pipeline_type}",
              psycopg.types.json.Jsonb(initial_artifacts)))
        return run_id

    def update_current_artifact(self, run_id: str, key: str, artifact_id: str) -> None:
        self.conn.execute("""
            UPDATE runs
            SET current_artifacts = jsonb_set(current_artifacts, %s, to_jsonb(%s::text)),
                last_activity_at = now()
            WHERE run_id = %s
        """, (f"{{{key}}}", artifact_id, run_id))

    def update_status(self, run_id: str, status: str) -> None:
        self.conn.execute("""
            UPDATE runs SET status = %s, last_activity_at = now() WHERE run_id = %s
        """, (status, run_id))
