def _find(graph: list[dict], task_id: str) -> dict:
    for task in graph:
        if task["id"] == task_id:
            return task
    raise KeyError(f"Task {task_id} not found in graph")


def add_parallel(graph: list[dict], target_id: str, nodes: list[dict]) -> tuple[list[dict], bool]:
    """Attach sibling nodes under target. Target becomes composite.
    Children inherit target's deps. Downstream keeps depending on target (composite = done when all children done).
    """
    target = _find(graph, target_id)
    if target["execution_type"] == "composite":
        raise ValueError(f"Cannot add_parallel to already-composite node {target_id}")
    target["execution_type"] = "composite"

    for node in nodes:
        node.setdefault("deps", list(target["deps"]))
        node.setdefault("parent_id", target_id)
        node.setdefault("objective", "")
        node.setdefault("description", "")
        node.setdefault("tags", [])
        node.setdefault("verification_steps", [])
        node.setdefault("priority", target.get("priority", "medium"))
        node.setdefault("risk_level", target.get("risk_level", "medium"))
        node.setdefault("llm_enriched", False)
        graph.append(node)

    # DO NOT redirect downstream deps. Downstream keeps depending on composite target.
    return graph, True


def add_before(graph: list[dict], target_id: str, node: dict) -> tuple[list[dict], bool]:
    """Insert node before target. New node gets target's old deps. Target now depends on new node."""
    target = _find(graph, target_id)
    old_deps = list(target["deps"])

    node.setdefault("deps", old_deps)
    node.setdefault("parent_id", None)
    node.setdefault("objective", "")
    node.setdefault("description", "")
    node.setdefault("tags", [])
    node.setdefault("verification_steps", [])
    node.setdefault("priority", "medium")
    node.setdefault("risk_level", "medium")
    node.setdefault("llm_enriched", False)
    graph.append(node)

    target["deps"] = [node["id"]]
    return graph, True


def add_after(graph: list[dict], target_id: str, node: dict) -> tuple[list[dict], bool]:
    """Insert node after target. If target is composite, depends on all leaf children instead.
    Downstream deps redirected from target to new node."""
    target = _find(graph, target_id)
    if target["execution_type"] == "composite":
        children = [t for t in graph if t.get("parent_id") == target_id]
        dep_ids = [c["id"] for c in children] if children else [target_id]
        node.setdefault("deps", dep_ids)
    else:
        node.setdefault("deps", [target_id])

    node.setdefault("parent_id", None)
    node.setdefault("objective", "")
    node.setdefault("description", "")
    node.setdefault("tags", [])
    node.setdefault("verification_steps", [])
    node.setdefault("priority", "medium")
    node.setdefault("risk_level", "medium")
    node.setdefault("llm_enriched", False)
    graph.append(node)

    # Redirect downstream deps
    for task in graph:
        if target_id in task["deps"] and task["id"] != node["id"]:
            task["deps"] = [node["id"] if d == target_id else d for d in task["deps"]]

    return graph, True


def rule_database(spec: dict, graph: list[dict]) -> tuple[list[dict], bool]:
    hard = spec.get("constraints", {}).get("hard", [])
    if not any("database" in c.lower() or "postgresql" in c.lower() for c in hard):
        return graph, False
    return add_before(graph, target_id="TASK-IMPL", node={
        "id": "TASK-DESIGN.SCHEMA", "title": "Design database schema",
        "phase": "design_solution", "group": "design_phase",
        "execution_type": "atomic", "type": "design",
        "agent_type": "Database Specialist",
        "required_inputs": ["solution_design.md", "constraints.md"],
        "expected_outputs": ["schema.sql", "erd.md"],
        "done_definition": "Schema covers all entities, migration runs, indexes for common queries",
        "enriched_by": "rule", "created_by_rule": "RULE-DATABASE",
    })


def rule_product_split(spec: dict, graph: list[dict]) -> tuple[list[dict], bool]:
    if spec.get("scope", {}).get("type") != "product":
        return graph, False
    return add_parallel(graph, target_id="TASK-IMPL", nodes=[
        {"id": "TASK-IMPL.BACKEND", "title": "Implement backend",
         "phase": "implement", "group": "implement_phase",
         "execution_type": "atomic", "type": "coding",
         "agent_type": "Backend Developer",
         "required_inputs": ["solution_design.md"],
         "expected_outputs": ["backend source code"],
         "done_definition": "Backend API functional, tests pass",
         "enriched_by": "rule", "created_by_rule": "RULE-PRODUCT-SPLIT"},
        {"id": "TASK-IMPL.FRONTEND", "title": "Implement frontend",
         "phase": "implement", "group": "implement_phase",
         "execution_type": "atomic", "type": "coding",
         "agent_type": "Frontend Developer",
         "required_inputs": ["solution_design.md"],
         "expected_outputs": ["frontend source code"],
         "done_definition": "Frontend connects to backend, tests pass",
         "enriched_by": "rule", "created_by_rule": "RULE-PRODUCT-SPLIT"},
    ])


def rule_performance(spec: dict, graph: list[dict]) -> tuple[list[dict], bool]:
    signals = spec.get("success_signals", [])
    keywords = {"performance", "latency", "speed", "throughput", "response time"}
    if not any(any(kw in s.lower() for kw in keywords) for s in signals):
        return graph, False
    return add_after(graph, target_id="TASK-IMPL", node={
        "id": "TASK-IMPL.PERF", "title": "Performance testing",
        "phase": "implement", "group": "implement_phase",
        "execution_type": "atomic", "type": "testing",
        "agent_type": "Performance Engineer",
        "required_inputs": ["source code", "success_criteria.md"],
        "expected_outputs": ["performance_report.json"],
        "done_definition": "All performance signals tested, results documented",
        "enriched_by": "rule", "created_by_rule": "RULE-PERF",
    })


# ORDER MATTERS: add_before rules MUST run before add_parallel
RULES = [
    ("RULE-DATABASE", rule_database),
    ("RULE-PRODUCT-SPLIT", rule_product_split),
    ("RULE-PERF", rule_performance),
]


def apply_rules(graph: list[dict], spec: dict) -> tuple[list[dict], list[str]]:
    applied = []
    for rule_id, rule_fn in RULES:
        graph, changed = rule_fn(spec, graph)
        if changed:
            applied.append(rule_id)
    return graph, applied
