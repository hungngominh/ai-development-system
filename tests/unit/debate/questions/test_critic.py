"""M4.3 Critic Loop tests.

Covers: empty input, no-flags happy path, max_iter cap, each action
(keep, drop, merge, rewrite), merge with bad target, rewrite parse
failure, sha256 loop guard, MIN_SURVIVING early exit, critic JSON
parse degradation, invalid flag/action/qid handling, prompt helpers.
"""

import json

import pytest

from ai_dev_system.debate.questions import critic
from ai_dev_system.debate.questions.critic import (
    MAX_CRITIC_ITER,
    MIN_SURVIVING_QUESTIONS,
)
from ai_dev_system.debate.report import Question


# ---- helpers ----


class FakeLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise AssertionError("FakeLLM exhausted its canned responses")
        return self._responses.pop(0)


def _question(qid: str, text: str | None = None) -> Question:
    return Question(
        id=qid,
        text=text or f"Question text {qid}",
        classification="REQUIRED",
        domain="backend",
        agent_a="BackendArchitect",
        agent_b="ProductManager",
        source_decision_id=f"d-{qid}",
    )


def _flag(qid: str, action: str, **extra) -> dict:
    base = {
        "question_id": qid,
        "flag": "SHALLOW",
        "action": action,
        "reason": f"{action} {qid}",
    }
    base.update(extra)
    return base


def _empty() -> str:
    return json.dumps([])


def _rewrite_response(new_text: str, qid: str = "Q1") -> str:
    return json.dumps({"question_id": qid, "new_text": new_text})


def _make_questions(n: int) -> list[Question]:
    return [_question(f"Q{i + 1}") for i in range(n)]


# ---- empty / happy ----


def test_run_returns_empty_when_no_questions():
    llm = FakeLLM([])
    result, iters = critic.run([], brief_digest="d", llm_client=llm)
    assert result == []
    assert iters == 0
    assert llm.calls == []


def test_no_flags_exits_after_one_call_zero_iterations():
    questions = _make_questions(5)
    llm = FakeLLM([_empty()])
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert result == questions
    assert iters == 0
    assert len(llm.calls) == 1


def test_max_iter_caps_loop_at_two():
    questions = _make_questions(8)
    # always returns one keep flag (no actual mutation)
    flag = json.dumps([_flag("Q1", "keep")])
    llm = FakeLLM([flag, flag, flag, flag])  # extras shouldn't be consumed
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == MAX_CRITIC_ITER == 2
    assert len(llm.calls) == 2
    assert len(result) == 8


# ---- actions ----


def test_drop_action_removes_question():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "drop")]),
        _empty(),
    ])
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 1
    assert [q.id for q in result] == ["Q1", "Q2", "Q4", "Q5", "Q6"]


def test_merge_action_drops_source_keeps_target():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q5", "merge", merge_into="Q2")]),
        _empty(),
    ])
    result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert [q.id for q in result] == ["Q1", "Q2", "Q3", "Q4", "Q6"]


def test_merge_with_bad_target_converts_to_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q5", "merge", merge_into="Q999")]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="bad merge_into"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q5" not in [q.id for q in result]
    assert len(result) == 5


def test_merge_with_missing_target_converts_to_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q5", "merge")]),  # no merge_into
        _empty(),
    ])
    with pytest.warns(UserWarning, match="bad merge_into"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q5" not in [q.id for q in result]


def test_merge_into_self_converts_to_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q5", "merge", merge_into="Q5")]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="bad merge_into"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q5" not in [q.id for q in result]


def test_keep_action_is_noop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "keep"), _flag("Q4", "keep")]),
        _empty(),
    ])
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 1
    assert [q.id for q in result] == [f"Q{i+1}" for i in range(6)]
    assert all(q.text.startswith("Question text") for q in result)


def test_rewrite_action_updates_text():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite", flag="SHALLOW")]),
        _rewrite_response("Sharper version of Q3", qid="Q3"),
        _empty(),
    ])
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 1
    q3 = next(q for q in result if q.id == "Q3")
    assert q3.text == "Sharper version of Q3"


# ---- rewrite failure modes ----


def test_rewrite_invalid_json_becomes_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        "not valid json {",
        _empty(),
    ])
    with pytest.warns(UserWarning, match="not valid JSON"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q3" not in [q.id for q in result]


def test_rewrite_empty_new_text_becomes_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        json.dumps({"new_text": "   "}),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="missing or empty new_text"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q3" not in [q.id for q in result]


def test_rewrite_non_dict_response_becomes_drop():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        json.dumps(["array", "not", "object"]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="must be an object"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q3" not in [q.id for q in result]


# ---- sha256 loop guard ----


def test_loop_guard_drops_rewrite_to_previously_seen_text():
    # Q3's rewrite produces Q1's exact text -> must drop
    questions = _make_questions(6)
    q1_text = questions[0].text
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        _rewrite_response(q1_text, qid="Q3"),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="loop guard"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert "Q3" not in [q.id for q in result]
    # Q1 still present, unchanged
    q1 = next(q for q in result if q.id == "Q1")
    assert q1.text == q1_text


def test_loop_guard_blocks_oscillation_across_iterations():
    # Iter 1: rewrite Q3 to "version A" (accepted).
    # Iter 2: rewrite Q3 again, LLM emits original Q3 text -> seen -> drop.
    questions = _make_questions(6)
    original_q3 = questions[2].text
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        _rewrite_response("version A", qid="Q3"),
        json.dumps([_flag("Q3", "rewrite")]),
        _rewrite_response(original_q3, qid="Q3"),
    ])
    with pytest.warns(UserWarning, match="loop guard"):
        result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 2
    assert "Q3" not in [q.id for q in result]


def test_loop_guard_allows_genuinely_new_rewrite():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "rewrite")]),
        _rewrite_response("Completely fresh phrasing for Q3", qid="Q3"),
        _empty(),
    ])
    result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    q3 = next(q for q in result if q.id == "Q3")
    assert q3.text == "Completely fresh phrasing for Q3"


# ---- min surviving guard ----


def test_min_surviving_triggers_early_exit():
    questions = _make_questions(6)
    # Drop 3 in one shot -> 3 surviving < 5 -> warn + stop
    llm = FakeLLM([
        json.dumps([
            _flag("Q1", "drop"),
            _flag("Q2", "drop"),
            _flag("Q3", "drop"),
        ]),
        # would never be consumed
    ])
    with pytest.warns(UserWarning, match=f"< {MIN_SURVIVING_QUESTIONS}"):
        result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 1
    assert len(result) == 3
    assert len(llm.calls) == 1


# ---- critic degradation ----


def test_critic_invalid_json_treated_as_no_flags():
    questions = _make_questions(6)
    llm = FakeLLM(["totally not json"])
    with pytest.warns(UserWarning, match="not valid JSON"):
        result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 0
    assert result == questions


def test_critic_non_array_response_treated_as_no_flags():
    questions = _make_questions(6)
    llm = FakeLLM([json.dumps({"flags": []})])
    with pytest.warns(UserWarning, match="must be a JSON array"):
        result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 0
    assert result == questions


# ---- invalid flag input ----


def test_unknown_question_id_in_flag_is_ignored():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q999", "drop")]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="unknown question_id"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert len(result) == 6


def test_invalid_action_is_ignored():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([_flag("Q3", "delete")]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="invalid action"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert len(result) == 6


def test_invalid_flag_name_is_ignored():
    questions = _make_questions(6)
    llm = FakeLLM([
        json.dumps([{
            "question_id": "Q3",
            "action": "drop",
            "flag": "SUSPICIOUS",
            "reason": "bad",
        }]),
        _empty(),
    ])
    with pytest.warns(UserWarning, match="invalid flag"):
        result, _ = critic.run(questions, brief_digest="d", llm_client=llm)
    assert len(result) == 6


# ---- combined ----


def test_mixed_actions_applied_atomically_in_one_pass():
    questions = _make_questions(8)
    llm = FakeLLM([
        json.dumps([
            _flag("Q1", "drop"),
            _flag("Q2", "keep"),
            _flag("Q3", "merge", merge_into="Q4"),
            _flag("Q5", "rewrite", flag="SHALLOW"),
        ]),
        _rewrite_response("Improved Q5", qid="Q5"),
        _empty(),
    ])
    result, iters = critic.run(questions, brief_digest="d", llm_client=llm)
    assert iters == 1
    ids = [q.id for q in result]
    assert ids == ["Q2", "Q4", "Q5", "Q6", "Q7", "Q8"]
    q5 = next(q for q in result if q.id == "Q5")
    assert q5.text == "Improved Q5"


# ---- prompt helpers ----


def test_load_critic_prompt_present():
    text = critic.load_critic_prompt()
    assert "{questions_json}" in text
    assert "{brief_digest}" in text


def test_load_rewrite_prompt_present():
    text = critic.load_rewrite_prompt()
    assert "{question_json}" in text
    assert "{flag}" in text


def test_split_prompt_rejects_missing_user_section():
    with pytest.raises(ValueError, match="missing USER"):
        critic._split_prompt("SYSTEM\nno user block")
