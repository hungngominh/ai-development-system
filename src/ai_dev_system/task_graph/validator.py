CORE_IDS = {"TASK-PARSE", "TASK-DESIGN", "TASK-IMPL", "TASK-VALIDATE"}

REQUIRED_FIELDS = {"id", "title", "phase", "type", "deps", "execution_type",
                   "agent_type", "required_inputs", "expected_outputs",
                   "done_definition", "enriched_by"}


def validate_graph(graph: list[dict]) -> list[str]:
    errors = []
    ids = {t["id"] for t in graph}

    missing_core = CORE_IDS - ids
    if missing_core:
        errors.append(f"Missing core nodes: {missing_core}")

    for task in graph:
        for dep in task.get("deps", []):
            if dep not in ids:
                errors.append(f"{task['id']} depends on unknown {dep}")

    if _has_cycle(graph):
        errors.append("Graph contains cycle")

    if len(ids) != len(graph):
        seen = set()
        for task in graph:
            if task["id"] in seen:
                errors.append(f"Duplicate ID: {task['id']}")
            seen.add(task["id"])

    # Check key existence (not truthiness — empty list is valid!)
    for task in graph:
        for field in REQUIRED_FIELDS:
            if field not in task:
                errors.append(f"{task['id']} missing required field: {field}")

    composite_ids = {t["id"] for t in graph if t["execution_type"] == "composite"}
    for cid in composite_ids:
        children = [t for t in graph if t.get("parent_id") == cid]
        if not children:
            errors.append(f"Composite node {cid} has no children")

    for task in graph:
        if task["execution_type"] == "atomic":
            children = [t for t in graph if t.get("parent_id") == task["id"]]
            if children:
                errors.append(f"Atomic node {task['id']} has children: {[c['id'] for c in children]}")

    return errors


def _has_cycle(graph: list[dict]) -> bool:
    """Kahn's algorithm."""
    in_degree = {t["id"]: 0 for t in graph}
    adj = {t["id"]: [] for t in graph}
    for task in graph:
        for dep in task.get("deps", []):
            if dep in adj:
                adj[dep].append(task["id"])
                in_degree[task["id"]] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return visited != len(graph)
