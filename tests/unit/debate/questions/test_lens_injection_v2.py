import json

from ai_dev_system.debate.questions import inventory, materializer
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    def __init__(self, response):
        self.system_seen = None
        self.user_seen = None
        self._response = response
    def complete(self, system, user):
        self.system_seen = system
        self.user_seen = user
        return self._response


_INV_RESPONSE = json.dumps([
    {"id": f"d{i}", "summary": "s", "classification": "REQUIRED",
     "domain_hints": ["psychology"], "blocks_what": [], "has_safe_default": False,
     "brief_field_refs": ["scope_in"]}
    for i in range(8)
])


def test_inventory_injects_profile_into_user_prompt():
    llm = _CaptureLLM(_INV_RESPONSE)
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    inventory.run({"scope_in": []}, llm, profile=profile)
    assert "couple psychology" in llm.user_seen


def test_inventory_empty_profile_no_profile_text():
    llm = _CaptureLLM(_INV_RESPONSE)
    inventory.run({"scope_in": []}, llm, profile=ProjectProfile.empty())
    assert "PROJECT PROFILE" not in llm.user_seen


def test_materializer_injects_profile():
    resp = json.dumps([{"text": "q?", "domain": "psychology",
                        "agent_a": "BehavioralPsychologist", "agent_b": "ProductManager",
                        "source_decision_id": "d1"}])
    llm = _CaptureLLM(resp)
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions = [Decision(id="d1", summary="s", classification="REQUIRED",
                          domain_hints=["psychology"], blocks_what=["f"], has_safe_default=False)]
    materializer.run(decisions, "digest", llm, profile=profile)
    assert "couple psychology" in llm.user_seen
