# tests/integration/test_pipeline_full.py
from ai_dev_system.pipeline import run_spec_pipeline
from ai_dev_system.gate.stub import StubGateIO
from ai_dev_system.gate.stub_gate2 import StubGate2IO
from ai_dev_system.task_graph.generator import generate_task_graph
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.task_graph.validator import validate_graph


def test_full_pipeline_idea_to_task_graph(conn, config, project_id):
    """End-to-end: raw idea → spec bundle → task graph → approved graph."""
    # Phase A: Spec Pipeline
    gate1_io = StubGateIO(
        edits={
            "problem": "No internal knowledge sharing",
            "goal": "Forum for developers",
            "target_users": "Internal team (~50)",
            "constraints": {"hard": ["Must use PostgreSQL"], "soft": ["Prefer Python"]},
            "scope": {"type": "product", "complexity_hint": "medium"},
            "success_signals": ["Search results in < 5s"],
        },
        approve=True,
    )
    bundle = run_spec_pipeline(
        raw_idea="Build a forum for sharing knowledge",
        config=config, conn=conn, project_id=project_id, io=gate1_io,
    )

    # Read spec files back
    spec_content = {}
    for filename, path in bundle.files.items():
        spec_content[filename] = path.read_text()

    # Phase B: Task Graph — need the approved brief
    # In real pipeline we'd read from APPROVED_BRIEF artifact. Here we reconstruct it.
    brief = {
        "problem": "No internal knowledge sharing",
        "goal": "Forum for developers",
        "target_users": "Internal team (~50)",
        "constraints": {"hard": ["Must use PostgreSQL"], "soft": ["Prefer Python"]},
        "scope": {"type": "product", "complexity_hint": "medium"},
        "success_signals": ["Search results in < 5s"],
    }

    envelope = generate_task_graph(spec_content, brief, "test-artifact-id")
    assert len(envelope["rules_applied"]) >= 2  # DATABASE + PRODUCT-SPLIT
    assert validate_graph(envelope["tasks"]) == []

    # Gate 2: Approve
    gate2_io = StubGate2IO(action="approve")
    result = run_gate_2(envelope, gate2_io)
    assert result.status == "approved"

    # Verify graph structure
    ids = {t["id"] for t in result.graph["tasks"]}
    assert "TASK-PARSE" in ids
    assert "TASK-DESIGN" in ids
    assert "TASK-DESIGN.SCHEMA" in ids
    assert "TASK-IMPL" in ids
    assert "TASK-IMPL.BACKEND" in ids
    assert "TASK-IMPL.FRONTEND" in ids
    assert "TASK-VALIDATE" in ids
