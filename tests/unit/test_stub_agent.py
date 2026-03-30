import os
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.agents.base import PromotedOutput

def test_stub_agent_creates_output_files(tmp_path):
    agent = StubAgent()
    promoted = [PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")]
    result = agent.run(
        task_id="TASK-1",
        output_path=str(tmp_path),
        promoted_outputs=promoted,
    )
    assert result.success
    assert os.path.exists(os.path.join(str(tmp_path), "result.json"))

def test_stub_agent_creates_all_promoted_files(tmp_path):
    agent = StubAgent()
    promoted = [
        PromotedOutput(name="a.json", artifact_type="EXECUTION_LOG"),
        PromotedOutput(name="b.json", artifact_type="EXECUTION_LOG"),
    ]
    result = agent.run("TASK-2", str(tmp_path), promoted)
    assert result.success
    assert os.path.exists(os.path.join(str(tmp_path), "a.json"))
    assert os.path.exists(os.path.join(str(tmp_path), "b.json"))
    assert len(result.promoted_outputs) == 2

def test_stub_agent_no_promoted_outputs(tmp_path):
    agent = StubAgent()
    result = agent.run("TASK-3", str(tmp_path), [])
    assert result.success
    assert result.output_path == str(tmp_path)
