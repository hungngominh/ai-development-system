from datetime import datetime, timezone
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import apply_rules
from ai_dev_system.task_graph.enricher import enrich_all
from ai_dev_system.task_graph.validator import validate_graph


class GraphValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Graph validation failed: {errors}")


def generate_task_graph(spec_bundle_content, approved_brief, spec_artifact_id, llm=None):
    graph = build_skeleton()
    graph, rules_applied = apply_rules(graph, approved_brief)
    graph = enrich_all(graph, spec_bundle_content, llm)
    errors = validate_graph(graph)
    if errors:
        raise GraphValidationError(errors)
    return {
        "graph_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_bundle_version": 1,
        "source_spec_artifact_id": spec_artifact_id,
        "generator_version": "1.0.0",
        "rules_applied": rules_applied,
        "llm_enriched": llm is not None,
        "tasks": graph,
    }
