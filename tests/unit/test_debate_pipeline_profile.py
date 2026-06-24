from ai_dev_system.debate_pipeline import _question_path
from ai_dev_system.feature_flags import FeatureFlags
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    def __init__(self):
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        import json
        return json.dumps([{
            "id": "Q1", "text": "t", "classification": "REQUIRED",
            "domain": "psychology", "agent_a": "BehavioralPsychologist",
            "agent_b": "ProductManager",
        }])


def test_question_path_threads_profile_into_legacy():
    llm = _CaptureLLM()
    profile = ProjectProfile(vertical="couples app", primary_personas=[],
                             key_dimensions=["couple psychology"], emotional_stakes=[])
    flags = FeatureFlags()  # all off → legacy path
    questions, decisions, digest = _question_path(
        flags, {"idea": "x"}, None, llm, profile=profile,
    )
    assert decisions is None  # legacy path
    assert "couple psychology" in llm.system_seen  # lens reached the generator


def test_question_path_without_profile_is_legacy_default():
    llm = _CaptureLLM()
    flags = FeatureFlags()
    _question_path(flags, {"idea": "x"}, None, llm)  # profile defaults to None
    # no lens block appended
    assert "PROJECT PROFILE" not in (llm.system_seen or "")
