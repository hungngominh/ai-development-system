"""Smoke tests for the M4 pipeline scaffold.

Each stage currently raises NotImplementedError. These tests assert
the scaffold surface stays stable (signatures, imports, prompt files
on disk) and will be expanded as each slice lands. Once a stage gets
a real implementation, replace its corresponding NotImplementedError
test with behavior tests.
"""

import pytest

from ai_dev_system.debate.questions import (
    Decision,
    PipelineResult,
    coverage,
    critic,
    inventory,
    materializer,
    pipeline,
)


def test_stage_modules_expose_run():
    assert callable(inventory.run)
    assert callable(materializer.run)
    assert callable(critic.run)
    assert callable(coverage.run)
    assert callable(pipeline.run_pipeline)


def test_prompt_files_exist():
    assert inventory.PROMPT_PATH.exists()
    assert materializer.PROMPT_PATH.exists()
    assert critic.CRITIC_PROMPT_PATH.exists()
    assert critic.REWRITE_PROMPT_PATH.exists()


def test_prompts_are_loadable():
    assert "Decisions" in inventory.load_prompt() or "decisions" in inventory.load_prompt().lower()
    assert "Question" in materializer.load_prompt() or "question" in materializer.load_prompt().lower()
    assert critic.load_critic_prompt()
    assert critic.load_rewrite_prompt()


def test_materializer_not_yet_implemented():
    with pytest.raises(NotImplementedError, match="M4.2"):
        materializer.run([], brief_digest="", llm_client=None)


def test_critic_not_yet_implemented():
    with pytest.raises(NotImplementedError, match="M4.3"):
        critic.run([], brief_digest="", llm_client=None)


def test_coverage_not_yet_implemented():
    with pytest.raises(NotImplementedError, match="M4.4"):
        coverage.run([], [], brief_v2={})


def test_pipeline_not_yet_implemented():
    with pytest.raises(NotImplementedError, match="M4.5"):
        pipeline.run_pipeline({}, brief_digest="", llm_client=None)


def test_pipeline_result_dataclass():
    from ai_dev_system.debate.questions.models import CoverageReport

    decisions: list[Decision] = []
    result = PipelineResult(
        decisions=decisions,
        questions_final=[],
        coverage_report=CoverageReport(
            checks=[],
            covered_decision_ids=[],
            missing_decision_ids=[],
            domain_distribution={},
            classification_distribution={},
            total_questions=0,
        ),
        critic_iterations=0,
    )
    assert result.critic_iterations == 0
    assert result.questions_final == []
