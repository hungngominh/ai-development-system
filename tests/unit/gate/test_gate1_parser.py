"""Unit tests for gate.gate1_review.parser (G4).

Tests 30+ input patterns matching the spec gate1-skill-redesign §Input Parser.
"""

from __future__ import annotations

import pytest

from ai_dev_system.gate.gate1_review.parser import ParseResult, parse_user_input


# ---- helpers ----


def _parse(text: str, pending_forced=0, pending_pf=0) -> ParseResult:
    return parse_user_input(text, pending_forced=pending_forced,
                            pending_parse_failed=pending_pf)


# ---- choose A ----


@pytest.mark.parametrize("text", [
    "Q3 chọn A",
    "q3 chon A",
    "Q3 option A",
    "Q3 agent A",
    "Q3 pick A",
    "Q3 CHỌN A",
])
def test_choose_a_variants(text):
    r = _parse(text)
    assert r.action_type == "answer"
    assert r.target == "Q3"
    assert r.choice == "agent_a"
    assert r.accepted is True


# ---- choose B ----


@pytest.mark.parametrize("text", [
    "Q5 chọn B",
    "Q5 option B",
    "Q5 agent B",
    "Q5 pick B",
])
def test_choose_b_variants(text):
    r = _parse(text)
    assert r.action_type == "answer"
    assert r.target == "Q5"
    assert r.choice == "agent_b"


# ---- approve moderator ----


@pytest.mark.parametrize("text", [
    "Q3 approve moderator",
    "Q3 đồng ý moderator",
    "Q3 dong y moderator",
    "Q3 accept moderator",
    "Q3 ok mod",
    "Q3 chấp nhận moderator",
])
def test_approve_moderator_variants(text):
    r = _parse(text)
    assert r.action_type == "answer"
    assert r.target == "Q3"
    assert r.choice == "moderator"


# ---- override ----


@pytest.mark.parametrize("text, expected_payload", [
    ("Q3: dùng PostgreSQL FTS", "dùng PostgreSQL FTS"),
    ("Q3 → dùng X", "dùng X"),
    ("Q3 - use Redis instead", "use Redis instead"),
    ("Q3   dùng Kafka", "dùng Kafka"),
])
def test_override_variants(text, expected_payload):
    r = _parse(text)
    assert r.action_type == "answer"
    assert r.target == "Q3"
    assert r.choice == "override"
    assert r.payload == expected_payload


def test_override_non_empty_payload_required():
    r = _parse("Q3:")
    # empty override → unknown or rejected
    assert r.accepted is False


# ---- show / expand ----


@pytest.mark.parametrize("text", [
    "show Q3",
    "xem Q3",
    "expand Q3",
    "mở rộng Q3",
])
def test_show_question(text):
    r = _parse(text)
    assert r.action_type == "expand"
    assert r.target == "Q3"


@pytest.mark.parametrize("text", [
    "show brief",
    "xem brief",
])
def test_show_brief(text):
    r = _parse(text)
    assert r.action_type == "expand"
    assert r.target == "brief"


@pytest.mark.parametrize("text", [
    "expand optional",
    "mở rộng optional",
    "xem optional",
])
def test_expand_optional(text):
    r = _parse(text)
    assert r.action_type == "expand"
    assert r.target == "auto_resolved"


# ---- approve all ----


def test_approve_all_no_pending_accepted():
    r = _parse("approve all", pending_forced=0, pending_pf=0)
    assert r.action_type == "approve_all"
    assert r.accepted is True


def test_approve_all_with_pending_forced_rejected():
    r = _parse("approve all", pending_forced=2, pending_pf=0)
    assert r.action_type == "approve_all"
    assert r.accepted is False
    assert "2" in r.message


def test_approve_all_with_pending_parse_failed_rejected():
    r = _parse("approve all", pending_forced=0, pending_pf=1)
    assert r.action_type == "approve_all"
    assert r.accepted is False


def test_approve_all_with_both_pending_rejected():
    r = _parse("approve all", pending_forced=3, pending_pf=1)
    assert r.accepted is False
    assert "4" in r.message or "3" in r.message


# ---- confirm ----


@pytest.mark.parametrize("text", [
    "confirm",
    "xác nhận",
    "done",
    "finalize",
])
def test_confirm_variants(text):
    r = _parse(text)
    assert r.action_type == "confirm"
    assert r.accepted is True


# ---- abort ----


@pytest.mark.parametrize("text", [
    "abort",
    "hủy",
    "cancel",
    "thoát",
])
def test_abort_variants(text):
    r = _parse(text)
    assert r.action_type == "abort"
    assert r.accepted is True


# ---- unknown / empty ----


def test_empty_input_rejected():
    r = _parse("")
    assert r.action_type == "unknown"
    assert r.accepted is False


def test_whitespace_only_rejected():
    r = _parse("   ")
    assert r.action_type == "unknown"
    assert r.accepted is False


def test_gibberish_unknown():
    r = _parse("asdfghjkl qwerty")
    assert r.action_type == "unknown"
    assert r.accepted is False


def test_unknown_includes_hint_in_message():
    r = _parse("do something weird")
    assert "Q1" in r.message or "confirm" in r.message or "chọn" in r.message.lower()


# ---- question ID normalisation ----


def test_qid_normalised_to_uppercase():
    r = _parse("q5 chọn A")
    assert r.target == "Q5"


def test_qid_with_underscore():
    r = _parse("Q3_auth chọn B")
    assert r.target == "Q3_AUTH"


# ---- ParseResult fields ----


def test_answer_result_has_message():
    r = _parse("Q1 chọn A")
    assert r.message  # non-empty


def test_confirm_accepted_true():
    r = _parse("confirm")
    assert r.accepted is True
