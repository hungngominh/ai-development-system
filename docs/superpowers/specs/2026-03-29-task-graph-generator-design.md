# Task Graph Generator v1 — Design

> Date: 2026-03-29
> Status: Draft
> Goal: Compile spec bundle (IR level 1) into executable task graph (IR level 2)
> Depends on: spec-pipeline-design (spec bundle contract)

---

## 1. Overview

```
Spec Bundle (5 files) → [Skeleton] → [Rules] → [LLM Enrich] → task_graph.generated.json
                         deterministic  deterministic  constrained
```

Three-stage compiler pipeline:
1. **Skeleton Builder** — creates invariant 4-phase backbone (always the same)
2. **Rule Engine** — injects optional nodes based on spec signals (deterministic)
3. **LLM Enricher** — fills task content from spec context (constrained, optional)

### Design Principles

- **Deterministic structure**: graph shape is never decided by LLM
- **LLM as constrained compiler**: LLM fills content fields only, cannot mutate graph topology
- **Core invariant**: 4 backbone nodes always present regardless of project type
- **Optional = attach, not replace**: rules add nodes to backbone, never remove or replace core nodes
- **Traceable**: every node knows who created it (skeleton, rule, or LLM enrichment)
- **Graceful degradation**: works without LLM (skeleton + rules produce a valid graph)

---

## 2. Task Node Schema

```json
{
  "id": "TASK-IMPL.BACKEND",
  "title": "string",
  "objective": "string",
  "description": "string",

  "phase": "parse_spec | design_solution | implement | validate",
  "parent_id": null,
  "group": "implement_phase",
  "execution_type": "atomic | composite",

  "type": "design | coding | testing | integration",
  "tags": ["string"],
  "deps": ["TASK-ID"],

  "agent_type": "string",
  "required_inputs": ["spec file or artifact ref"],
  "expected_outputs": ["file or artifact"],
  "done_definition": "string",
  "verification_steps": ["string"],

  "priority": "high | medium | low",
  "risk_level": "low | medium | high",

  "enriched_by": "skeleton | rule | llm",
  "llm_enriched": false,
  "created_by_rule": null
}
```

### Field Semantics

| Field | Purpose | Mutable by LLM? |
|-------|---------|-----------------|
| `id` | Stable identifier, dot-notation hierarchy: `TASK-IMPL.BACKEND` | No |
| `phase` | Links to backbone phase | No |
| `parent_id` | UI/grouping only (NOT execution dependency) | No |
| `group` | Logical grouping for visualization | No |
| `execution_type` | `atomic` = worker executes directly, `composite` = has children | No |
| `deps` | Execution dependencies (DAG edges) | No |
| `title` | Human-readable task name | Yes |
| `objective` | 1-2 sentence goal | Yes |
| `description` | Detailed, project-specific context | Yes |
| `done_definition` | Measurable completion criteria | Yes |
| `verification_steps` | Checklist for validation | Yes |
| `enriched_by` | Who last touched content (`skeleton` → `rule` → `llm`). For origin, see `created_by_rule`. | No |
| `llm_enriched` | Whether LLM has filled content | No |
| `created_by_rule` | Rule ID that created this node (null for skeleton nodes) | No |

### Key Design Decisions

- **No `status` field** — execution state lives in `task_runs` DB table only. The graph stores `initial_state` (not shown) only if needed for DB seeding. This avoids dual-source-of-truth drift.
- **`parent_id` vs `deps`** — `parent_id` is for visualization/grouping. `deps` is for execution ordering. A node can have `parent_id: "TASK-IMPL"` but `deps: ["TASK-DESIGN"]`. These are independent concerns.
- **`execution_type`** — tells the worker whether to execute this node directly (`atomic`) or skip it and execute its children (`composite`). Core nodes that get children via rules become `composite`.
- **Dot-notation IDs** — `TASK-IMPL.BACKEND` makes hierarchy parseable and diff-friendly.

---

## 3. Graph Envelope

The full `task_graph.generated.json`:

```json
{
  "graph_version": 1,
  "generated_at": "2026-03-29T16:00:00Z",
  "spec_bundle_version": 1,
  "source_spec_artifact_id": "uuid of SPEC_BUNDLE artifact",
  "generator_version": "1.0.0",
  "rules_applied": ["RULE-DATABASE", "RULE-PRODUCT-SPLIT"],
  "llm_enriched": true,

  "tasks": [
    { /* task node */ },
    { /* task node */ }
  ]
}
```

### Envelope Fields

| Field | Purpose |
|-------|---------|
| `graph_version` | Schema version for this graph format |
| `generated_at` | ISO timestamp |
| `spec_bundle_version` | Version of spec bundle used as input |
| `source_spec_artifact_id` | DB artifact ID for traceability |
| `generator_version` | Code version of the generator |
| `rules_applied` | Which rules fired (audit trail) |
| `llm_enriched` | Whether LLM enrichment was applied |

---

## 4. Core Skeleton (Invariant)

4 phases, always present, always in this order:

```python
# src/ai_dev_system/task_graph/skeleton.py

CORE_SKELETON = [
    {
        "id": "TASK-PARSE",
        "title": "Parse and validate spec bundle",
        "objective": "Extract structured information from spec files, detect contradictions",
        "description": "",
        "phase": "parse_spec",
        "parent_id": None,
        "group": "parse_phase",
        "execution_type": "atomic",
        "type": "design",
        "tags": ["spec", "validation"],
        "deps": [],
        "agent_type": "Spec Analyst",
        "required_inputs": ["problem.md", "requirements.md", "constraints.md",
                            "success_criteria.md", "assumptions.md"],
        "expected_outputs": ["parsed_spec_summary.json"],
        "done_definition": "All 5 spec files parsed, no contradictions found",
        "verification_steps": [
            "All required spec files present",
            "No contradictions between constraints and requirements",
            "Summary JSON validates against schema",
        ],
        "priority": "high",
        "risk_level": "low",
        "enriched_by": "skeleton",
        "llm_enriched": False,
        "created_by_rule": None,
    },
    {
        "id": "TASK-DESIGN",
        "title": "Design technical solution",
        "objective": "Create architecture and design decisions based on parsed spec",
        "description": "",
        "phase": "design_solution",
        "parent_id": None,
        "group": "design_phase",
        "execution_type": "atomic",
        "type": "design",
        "tags": ["architecture", "design"],
        "deps": ["TASK-PARSE"],
        "agent_type": "Solution Architect",
        "required_inputs": ["parsed_spec_summary.json"],
        "expected_outputs": ["solution_design.md"],
        "done_definition": "Design addresses all requirements and respects all hard constraints",
        "verification_steps": [
            "Every requirement in requirements.md has a corresponding design decision",
            "No hard constraint violated",
            "Assumptions explicitly listed",
        ],
        "priority": "high",
        "risk_level": "medium",
        "enriched_by": "skeleton",
        "llm_enriched": False,
        "created_by_rule": None,
    },
    {
        "id": "TASK-IMPL",
        "title": "Implement solution",
        "objective": "Build the solution according to the design",
        "description": "",
        "phase": "implement",
        "parent_id": None,
        "group": "implement_phase",
        "execution_type": "atomic",  # becomes "composite" if rules add children
        "type": "coding",
        "tags": ["implementation"],
        "deps": ["TASK-DESIGN"],
        "agent_type": "Developer",
        "required_inputs": ["solution_design.md"],
        "expected_outputs": ["source code"],
        "done_definition": "Implementation matches design, all hard constraints satisfied",
        "verification_steps": [
            "Code compiles/runs without errors",
            "Matches design decisions",
            "Hard constraints satisfied",
        ],
        "priority": "high",
        "risk_level": "medium",
        "enriched_by": "skeleton",
        "llm_enriched": False,
        "created_by_rule": None,
    },
    {
        "id": "TASK-VALIDATE",
        "title": "Validate against success criteria",
        "objective": "Verify the implementation meets all success criteria from spec",
        "description": "",
        "phase": "validate",
        "parent_id": None,
        "group": "validate_phase",
        "execution_type": "atomic",
        "type": "testing",
        "tags": ["validation", "qa"],
        "deps": ["TASK-IMPL"],
        "agent_type": "QA Engineer",
        "required_inputs": ["source code", "success_criteria.md"],
        "expected_outputs": ["validation_report.json"],
        "done_definition": "All success criteria checked, report generated",
        "verification_steps": [
            "Each success signal has a test result",
            "Report includes pass/fail for each criterion",
            "No critical failures",
        ],
        "priority": "high",
        "risk_level": "low",
        "enriched_by": "skeleton",
        "llm_enriched": False,
        "created_by_rule": None,
    },
]


def build_skeleton() -> list[dict]:
    """Return deep copy of core skeleton. Always 4 nodes."""
    import copy
    return copy.deepcopy(CORE_SKELETON)
```

---

## 5. Rule Engine

### Rule Actions

| Action | Semantics |
|--------|-----------|
| `add_parallel(target, nodes)` | Attach N sibling nodes under target. Children inherit target's deps. Target becomes `composite`. Downstream keeps depending on target (composite = done when all children done). |
| `add_before(target, node)` | Insert node before target. New node gets target's old deps. Target now depends on new node. |
| `add_after(target, node)` | Insert node after target. If target is composite, new node depends on all leaf children instead. Downstream deps redirected from target to new node. |

### v1 Rules (3 only)

```python
# src/ai_dev_system/task_graph/rules.py

def rule_product_split(spec: dict, graph: list[dict]) -> list[dict]:
    """If scope.type == 'product', split TASK-IMPL into backend + frontend + integration."""
    if spec.get("scope", {}).get("type") != "product":
        return graph, False

    return add_parallel(graph, target_id="TASK-IMPL", nodes=[
        {
            "id": "TASK-IMPL.BACKEND",
            "title": "Implement backend",
            "phase": "implement",
            "group": "implement_phase",
            "execution_type": "atomic",
            "type": "coding",
            "agent_type": "Backend Developer",
            "required_inputs": ["solution_design.md"],
            "expected_outputs": ["backend source code"],
            "done_definition": "Backend API functional, tests pass",
            "enriched_by": "rule",
            "created_by_rule": "RULE-PRODUCT-SPLIT",
        },
        {
            "id": "TASK-IMPL.FRONTEND",
            "title": "Implement frontend",
            "phase": "implement",
            "group": "implement_phase",
            "execution_type": "atomic",
            "type": "coding",
            "agent_type": "Frontend Developer",
            "required_inputs": ["solution_design.md"],
            "expected_outputs": ["frontend source code"],
            "done_definition": "Frontend connects to backend, tests pass",
            "enriched_by": "rule",
            "created_by_rule": "RULE-PRODUCT-SPLIT",
        },
    ])


def rule_database(spec: dict, graph: list[dict]) -> list[dict]:
    """If hard constraints mention database/postgresql, add schema design before impl."""
    hard = spec.get("constraints", {}).get("hard", [])
    if not any("database" in c.lower() or "postgresql" in c.lower() for c in hard):
        return graph, False

    return add_before(graph, target_id="TASK-IMPL", node={
        "id": "TASK-DESIGN.SCHEMA",
        "title": "Design database schema",
        "phase": "design_solution",
        "group": "design_phase",
        "execution_type": "atomic",
        "type": "design",
        "agent_type": "Database Specialist",
        "required_inputs": ["solution_design.md", "constraints.md"],
        "expected_outputs": ["schema.sql", "erd.md"],
        "done_definition": "Schema covers all entities, migration runs, indexes for common queries",
        "enriched_by": "rule",
        "created_by_rule": "RULE-DATABASE",
    })


def rule_performance(spec: dict, graph: list[dict]) -> list[dict]:
    """If success signals mention performance/latency/speed, add perf testing after impl."""
    signals = spec.get("success_signals", [])
    keywords = {"performance", "latency", "speed", "throughput", "response time"}
    if not any(any(kw in s.lower() for kw in keywords) for s in signals):
        return graph, False

    return add_after(graph, target_id="TASK-IMPL", node={
        "id": "TASK-IMPL.PERF",
        "title": "Performance testing",
        "phase": "implement",
        "group": "implement_phase",
        "execution_type": "atomic",
        "type": "testing",
        "agent_type": "Performance Engineer",
        "required_inputs": ["source code", "success_criteria.md"],
        "expected_outputs": ["performance_report.json"],
        "done_definition": "All performance signals tested, results documented",
        "enriched_by": "rule",
        "created_by_rule": "RULE-PERF",
    })


# Ordered rule list — applied sequentially.
# ORDER MATTERS: add_before/add_after rules MUST run before add_parallel,
# because add_parallel copies target's deps to children. If add_before runs
# after add_parallel, children won't inherit the new dependency.
RULES = [
    ("RULE-DATABASE", rule_database),         # add_before TASK-IMPL
    ("RULE-PRODUCT-SPLIT", rule_product_split), # add_parallel on TASK-IMPL
    ("RULE-PERF", rule_performance),           # add_after TASK-IMPL
]


def apply_rules(graph: list[dict], spec: dict) -> tuple[list[dict], list[str]]:
    """Apply all rules. Returns (modified_graph, list_of_applied_rule_ids)."""
    applied = []
    for rule_id, rule_fn in RULES:
        graph, changed = rule_fn(spec, graph)
        if changed:
            applied.append(rule_id)
    return graph, applied
```

### Graph Mutation Primitives

```python
# src/ai_dev_system/task_graph/rules.py

def add_parallel(graph: list[dict], target_id: str, nodes: list[dict]) -> list[dict]:
    """Attach sibling nodes under target. Target becomes composite.
    All new nodes inherit target's deps. Children depend on target's deps.
    Target stays in the DAG chain — downstream still depends on target.
    Worker treats composite as "done when all children done".
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

    # DO NOT redirect downstream deps away from target.
    # Downstream nodes keep depending on the composite target.
    # Worker resolves composite as "done when all children done".
    # This keeps composite node in the chain for: logging, aggregation,
    # checkpoint, and future add_after operations.

    return graph, True


def add_before(graph: list[dict], target_id: str, node: dict) -> list[dict]:
    """Insert node before target. New node gets target's old deps.
    Target now depends on new node only.
    """
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


def add_after(graph: list[dict], target_id: str, node: dict) -> list[dict]:
    """Insert node after target. New node depends on target.
    If target is composite, new node depends on all leaf children instead.
    Anything that depended on target now depends on new node.
    """
    target = _find(graph, target_id)
    if target["execution_type"] == "composite":
        # Attach to leaf children, not composite parent
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


def _find(graph: list[dict], task_id: str) -> dict:
    for task in graph:
        if task["id"] == task_id:
            return task
    raise KeyError(f"Task {task_id} not found in graph")
```

---

## 6. LLM Enricher

### Purpose

Fill `title`, `objective`, `description`, `done_definition`, `verification_steps` with project-specific content from spec bundle. Cannot change graph topology.

### Interface

```python
# src/ai_dev_system/task_graph/enricher.py

class LLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        """Send prompt, return text response."""
        ...

ENRICHABLE_FIELDS = {"title", "objective", "description",
                     "done_definition", "verification_steps"}

def enrich_task(task: dict, spec_content: dict[str, str], llm: LLMClient) -> dict:
    """Enrich a single task with LLM-generated content.

    spec_content: {"problem.md": "...", "requirements.md": "...", ...}
    Returns task with content fields updated. Structure fields unchanged.
    """
    prompt = _build_prompt(task, spec_content)
    try:
        response = llm.complete(prompt)
        enrichment = _parse_response(response)
    except (LLMError, JSONDecodeError, ValidationError):
        # Deterministic fallback — task keeps skeleton/rule defaults
        return task

    # Guardrail: only content fields, never structure
    for key in list(enrichment.keys()):
        if key not in ENRICHABLE_FIELDS:
            del enrichment[key]

    task.update(enrichment)
    task["llm_enriched"] = True
    task["enriched_by"] = "llm"
    return task


def enrich_all(graph: list[dict], spec_content: dict[str, str],
               llm: LLMClient | None = None) -> list[dict]:
    """Enrich all atomic tasks. Skip composite tasks.
    If llm is None, return graph unchanged (no-op)."""
    if llm is None:
        return graph
    for task in graph:
        if task["execution_type"] == "atomic":
            enrich_task(task, spec_content, llm)
    return graph
```

### Prompt Contract

```python
def _build_prompt(task: dict, spec_content: dict[str, str]) -> str:
    # Truncate spec content to avoid token limits
    problem = spec_content.get("problem.md", "")[:800]
    requirements = spec_content.get("requirements.md", "")[:800]
    constraints = spec_content.get("constraints.md", "")[:800]

    return f"""You are enriching a task in a software development execution plan.

## Task Context
- ID: {task['id']}
- Phase: {task['phase']}
- Type: {task['type']}
- Current title: {task['title']}
- Agent: {task['agent_type']}
- Inputs: {task['required_inputs']}
- Outputs: {task['expected_outputs']}

## Project Spec
### Problem
{problem}

### Requirements
{requirements}

### Constraints
{constraints}

## Instructions
Enrich this task with project-specific details. Return ONLY valid JSON:

```json
{{
  "title": "specific title mentioning the actual project",
  "objective": "1-2 sentences — what this task achieves for this specific project",
  "description": "detailed description with project-specific context, technical approach",
  "done_definition": "measurable completion criteria specific to this project",
  "verification_steps": ["specific step 1", "specific step 2", "specific step 3"]
}}
```

Rules:
- Be specific to THIS project (reference actual requirements, constraints, tech choices)
- done_definition must be measurable, not vague
- verification_steps must be actionable (a QA engineer can follow them)
- Do NOT add or reference tasks, dependencies, or execution structure"""
```

### Guardrails Summary

| Guardrail | Enforcement |
|-----------|-------------|
| Schema validation | `_parse_response` rejects non-JSON or missing required keys |
| Content-only mutation | Explicit allowlist (`ENRICHABLE_FIELDS`) |
| Deterministic fallback | Any LLM error → keep skeleton/rule defaults, log warning |
| No retry | Single attempt per task. LLM failure is non-fatal |
| No DAG mutation | LLM never sees graph structure, only single task + spec |
| Token limits | Spec content truncated in prompt |

---

## 7. Graph Validator

```python
# src/ai_dev_system/task_graph/validator.py

CORE_IDS = {"TASK-PARSE", "TASK-DESIGN", "TASK-IMPL", "TASK-VALIDATE"}

REQUIRED_FIELDS = {"id", "title", "phase", "type", "deps", "execution_type",
                   "agent_type", "required_inputs", "expected_outputs",
                   "done_definition", "enriched_by"}

def validate_graph(graph: list[dict]) -> list[str]:
    """Validate graph integrity. Returns list of errors (empty = valid)."""
    errors = []
    ids = {t["id"] for t in graph}

    # Core skeleton present
    missing_core = CORE_IDS - ids
    if missing_core:
        errors.append(f"Missing core nodes: {missing_core}")

    # All deps reference existing tasks
    for task in graph:
        for dep in task.get("deps", []):
            if dep not in ids:
                errors.append(f"{task['id']} depends on unknown {dep}")

    # No cycles
    if _has_cycle(graph):
        errors.append("Graph contains cycle")

    # No duplicate IDs
    if len(ids) != len(graph):
        seen = set()
        for task in graph:
            if task["id"] in seen:
                errors.append(f"Duplicate ID: {task['id']}")
            seen.add(task["id"])

    # All tasks have required fields
    for task in graph:
        for field in REQUIRED_FIELDS:
            if not task.get(field) and task.get(field) != False:
                errors.append(f"{task['id']} missing required field: {field}")

    # Composite nodes must have children
    composite_ids = {t["id"] for t in graph if t["execution_type"] == "composite"}
    for cid in composite_ids:
        children = [t for t in graph if t.get("parent_id") == cid]
        if not children:
            errors.append(f"Composite node {cid} has no children")

    # Atomic nodes must not have children
    for task in graph:
        if task["execution_type"] == "atomic":
            children = [t for t in graph if t.get("parent_id") == task["id"]]
            if children:
                errors.append(f"Atomic node {task['id']} has children: {[c['id'] for c in children]}")

    return errors


def _has_cycle(graph: list[dict]) -> bool:
    """Kahn's algorithm for cycle detection."""
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
```

---

## 8. Generator Orchestrator

```python
# src/ai_dev_system/task_graph/generator.py

from datetime import datetime, timezone

def generate_task_graph(
    spec_bundle_content: dict[str, str],
    approved_brief: dict,
    spec_artifact_id: str,
    llm: LLMClient | None = None,
) -> dict:
    """Full pipeline: skeleton → rules → enrich → validate → envelope.

    Args:
        spec_bundle_content: {"problem.md": "...", "requirements.md": "...", ...}
        approved_brief: The brief dict (for rule conditions)
        spec_artifact_id: DB artifact ID of the spec bundle
        llm: Optional LLM client for enrichment

    Returns:
        Complete task_graph.generated.json as dict
    """
    # Stage 1: Deterministic skeleton
    graph = build_skeleton()

    # Stage 2: Deterministic rules
    graph, rules_applied = apply_rules(graph, approved_brief)

    # Stage 3: LLM enrichment (optional, non-fatal)
    graph = enrich_all(graph, spec_bundle_content, llm)

    # Stage 4: Validate
    errors = validate_graph(graph)
    if errors:
        raise GraphValidationError(errors)

    # Build envelope
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


class GraphValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Graph validation failed: {errors}")
```

---

## 9. Gate 2 Integration

Same GateIO pattern as Gate 1.

```python
# src/ai_dev_system/gate/gate2.py

class Gate2IO(Protocol):
    def present_graph(self, graph_envelope: dict) -> None:
        """Display task graph to user."""
        ...

    def collect_edits(self, graph_envelope: dict) -> tuple[Literal["approve", "reject"], dict]:
        """Let user edit. Returns (action, possibly-edited envelope)."""
        ...

class Gate2Result:
    status: Literal["approved", "rejected"]
    graph: dict  # full envelope

def run_gate_2(graph_envelope: dict, io: Gate2IO) -> Gate2Result:
    io.present_graph(graph_envelope)
    action, edited = io.collect_edits(graph_envelope)
    if action == "approve":
        return Gate2Result(status="approved", graph=edited)
    return Gate2Result(status="rejected", graph=graph_envelope)
```

User can at Gate 2:
- **Approve** as-is
- **Edit** content fields: title, description, objective, done_definition, agent_type, verification_steps
- **Add** new atomic tasks (with valid deps)
- **Remove** non-core tasks (cannot remove TASK-PARSE/DESIGN/IMPL/VALIDATE)
- **Reject** → triggers regeneration (re-run generator, possibly with updated spec)

**Restricted fields** (cannot edit at Gate 2): `id`, `phase`, `execution_type`, `deps`, `parent_id`, `group`. Deps editing is deferred to v2 with advanced mode + visual diff. Post-edit `validate_graph()` runs regardless to catch any issues.

Post-approval: save as `task_graph.approved.json` artifact (same schema, with `"source": "approved"` and `"user_edits"` diff).

---

## 10. Pipeline Integration

### Prerequisites

**New repo methods** (defined in spec-pipeline-design, reused here):
- `RunRepo.create(pipeline_type) -> str` — creates run record
- `TaskRunRepo.create_sync(run_id, task_type) -> dict` — creates pipeline-level task_run

**New repo method** (this spec):
- `TaskRunRepo.create_from_graph(run_id, task, task_graph_artifact_id) -> str` — creates execution task_run from a graph node. Sets `task_graph_artifact_id`, `resolved_dependencies` from node deps, status `PENDING`.

**DB schema note**: Pipeline-level task_runs (normalize, gate, generate) do NOT have a `task_graph_artifact_id`. This column must be **nullable** (`UUID REFERENCES artifacts(artifact_id)` — remove NOT NULL) to support both pipeline-orchestration tasks and graph-execution tasks in the same table.

**`read_spec_bundle` function**: Looks up the active `SPEC_BUNDLE` artifact from `runs.current_artifacts`, resolves its `content_ref` path, reads the 5 markdown files into `dict[str, str]`. Simple utility — implementation is straightforward file I/O.

**`approved_brief` availability**: The `approved_brief` dict must be retrievable at graph generation time. The pipeline stores it as `APPROVED_BRIEF` artifact (JSON file). The generator reads it back from the promoted artifact path.

Extends spec pipeline with steps 4-5:

```python
# In pipeline.py — after spec bundle step:

# Step 4: Generate task graph
with conn.transaction():
    task_run = task_run_repo.create_sync(run_id, task_type="generate_task_graph")
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

# Read spec files from promoted artifact
spec_content = read_spec_bundle(config, run_id)
graph_envelope = generate_task_graph(
    spec_bundle_content=spec_content,
    approved_brief=approved_brief,
    spec_artifact_id=spec_artifact_id,
    llm=llm_client,  # None if LLM not configured
)

# Write to temp and promote
temp_path = build_temp_path(config.storage_root, run_id,
                            task_run["task_id"], task_run["attempt_number"])
os.makedirs(temp_path, exist_ok=True)
with open(os.path.join(temp_path, "task_graph.generated.json"), "w") as f:
    json.dump(graph_envelope, f, indent=2)

with conn.transaction():
    promoted = PromotedOutput(
        name="task_graph_generated",
        artifact_type="TASK_GRAPH_GENERATED",
        description="Generated task execution graph",
    )
    promote_output(conn, config, task_run, promoted, temp_path)

# Step 5: Gate 2
with conn.transaction():
    task_run = task_run_repo.create_sync(run_id, task_type="gate_2")
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

result = run_gate_2(graph_envelope, gate2_io)

if result.status == "rejected":
    with conn.transaction():
        task_run_repo.mark_failed(
            task_run["task_run_id"], "EXECUTION_ERROR", "user_rejected_graph")
    # Could loop back to regeneration here
    raise PipelineAborted("User rejected task graph at Gate 2")

# Re-validate after user edits (user may have added tasks, changed deps)
errors = validate_graph(result.graph["tasks"])
if errors:
    with conn.transaction():
        task_run_repo.mark_failed(
            task_run["task_run_id"], "EXECUTION_ERROR", f"post-edit validation: {errors}")
    raise GraphValidationError(errors)

# Promote approved graph
with conn.transaction():
    temp_path = _write_json_to_temp(config, task_run, result.graph)
    promoted = PromotedOutput(
        name="task_graph_approved",
        artifact_type="TASK_GRAPH_APPROVED",
        description="Human-approved task execution graph",
    )
    promote_output(conn, config, task_run, promoted, temp_path)

# Step 6: Create task_run records for execution engine
with conn.transaction():
    for task in result.graph["tasks"]:
        if task["execution_type"] == "atomic":
            # Only atomic tasks get task_runs — composite are structural only
            task_run_repo.create_from_graph(conn, run_id, task)
```

---

## 11. Complete Example: Forum Knowledge Sharing

Given spec bundle from a "forum for internal knowledge sharing" idea:

```json
{
  "graph_version": 1,
  "generated_at": "2026-03-29T16:00:00Z",
  "spec_bundle_version": 1,
  "source_spec_artifact_id": "artifact-uuid-123",
  "generator_version": "1.0.0",
  "rules_applied": ["RULE-DATABASE", "RULE-PRODUCT-SPLIT"],
  "llm_enriched": true,

  "tasks": [
    {
      "id": "TASK-PARSE",
      "title": "Parse forum knowledge sharing spec bundle",
      "objective": "Extract requirements for internal forum: posting, search, tagging",
      "description": "Parse all 5 spec files. Verify no contradictions between PostgreSQL constraint and search requirements.",
      "phase": "parse_spec",
      "parent_id": null,
      "group": "parse_phase",
      "execution_type": "atomic",
      "type": "design",
      "tags": ["spec", "validation"],
      "deps": [],
      "agent_type": "Spec Analyst",
      "required_inputs": ["problem.md", "requirements.md", "constraints.md", "success_criteria.md", "assumptions.md"],
      "expected_outputs": ["parsed_spec_summary.json"],
      "done_definition": "All 5 spec files parsed, forum entities identified, no contradictions",
      "verification_steps": [
        "All spec files present and non-empty",
        "PostgreSQL constraint compatible with full-text search requirement",
        "Summary JSON lists: users, posts, tags entities"
      ],
      "priority": "high",
      "risk_level": "low",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": null
    },
    {
      "id": "TASK-DESIGN",
      "title": "Design forum architecture with FastAPI + PostgreSQL",
      "objective": "Create backend architecture for forum with REST API, full-text search, OAuth2 auth",
      "description": "Design API endpoints, data models, auth flow, search indexing strategy. Must respect PostgreSQL-only constraint and OAuth2+JWT decision from Gate 1.",
      "phase": "design_solution",
      "parent_id": null,
      "group": "design_phase",
      "execution_type": "atomic",
      "type": "design",
      "tags": ["architecture", "design"],
      "deps": ["TASK-PARSE"],
      "agent_type": "Solution Architect",
      "required_inputs": ["parsed_spec_summary.json"],
      "expected_outputs": ["solution_design.md"],
      "done_definition": "Architecture doc covers API, data model, auth, search. All hard constraints respected.",
      "verification_steps": [
        "API endpoints listed for: posts CRUD, search, user auth, tags",
        "Data model includes: users, posts, tags, post_tags",
        "Auth uses OAuth2+JWT per Gate 1 decision",
        "Search uses PostgreSQL full-text (not external engine)"
      ],
      "priority": "high",
      "risk_level": "medium",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": null
    },
    {
      "id": "TASK-DESIGN.SCHEMA",
      "title": "Design PostgreSQL schema for forum",
      "objective": "Create database schema with proper indexes for forum MVP",
      "description": "Design tables: users, posts, tags, post_tags. Include GIN index for full-text search, proper foreign keys.",
      "phase": "design_solution",
      "parent_id": null,
      "group": "design_phase",
      "execution_type": "atomic",
      "type": "design",
      "tags": ["database", "schema"],
      "deps": ["TASK-DESIGN"],
      "agent_type": "Database Specialist",
      "required_inputs": ["solution_design.md", "constraints.md"],
      "expected_outputs": ["schema.sql", "erd.md"],
      "done_definition": "Schema covers all entities, migration runs, GIN index for search",
      "verification_steps": [
        "SQL syntax valid",
        "Foreign key constraints present",
        "GIN index on posts content for full-text search",
        "Migration script runs without error"
      ],
      "priority": "high",
      "risk_level": "low",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": "RULE-DATABASE"
    },
    {
      "id": "TASK-IMPL",
      "title": "Implement forum solution",
      "objective": "Build the forum (composite — see children)",
      "description": "",
      "phase": "implement",
      "parent_id": null,
      "group": "implement_phase",
      "execution_type": "composite",
      "type": "coding",
      "tags": ["implementation"],
      "deps": ["TASK-DESIGN.SCHEMA"],
      "agent_type": "Developer",
      "required_inputs": ["solution_design.md"],
      "expected_outputs": ["source code"],
      "done_definition": "All children completed",
      "verification_steps": [],
      "priority": "high",
      "risk_level": "medium",
      "enriched_by": "skeleton",
      "llm_enriched": false,
      "created_by_rule": null
    },
    {
      "id": "TASK-IMPL.BACKEND",
      "title": "Implement FastAPI backend for forum",
      "objective": "Build REST API with auth, CRUD, search endpoints",
      "description": "FastAPI app with: OAuth2+JWT auth, posts CRUD, full-text search via PostgreSQL, tag management.",
      "phase": "implement",
      "parent_id": "TASK-IMPL",
      "group": "implement_phase",
      "execution_type": "atomic",
      "type": "coding",
      "tags": ["backend", "api"],
      "deps": ["TASK-DESIGN.SCHEMA"],
      "agent_type": "Backend Developer",
      "required_inputs": ["solution_design.md", "schema.sql"],
      "expected_outputs": ["backend source code", "tests/test_api.py"],
      "done_definition": "All API endpoints functional, auth works, search returns results",
      "verification_steps": [
        "POST /posts creates a post",
        "GET /posts/search returns relevant results",
        "Auth flow: login → JWT → protected endpoint",
        "Unit tests pass"
      ],
      "priority": "high",
      "risk_level": "medium",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": "RULE-PRODUCT-SPLIT"
    },
    {
      "id": "TASK-IMPL.FRONTEND",
      "title": "Implement Vue.js frontend for forum",
      "objective": "Build UI for posting, searching, and browsing articles",
      "description": "Vue.js + TypeScript SPA. Pages: login, feed, post editor, search, tag browser.",
      "phase": "implement",
      "parent_id": "TASK-IMPL",
      "group": "implement_phase",
      "execution_type": "atomic",
      "type": "coding",
      "tags": ["frontend", "ui"],
      "deps": ["TASK-DESIGN.SCHEMA"],
      "agent_type": "Frontend Developer",
      "required_inputs": ["solution_design.md"],
      "expected_outputs": ["frontend source code"],
      "done_definition": "All pages render, connect to backend API, responsive design",
      "verification_steps": [
        "Login page authenticates via backend",
        "Post editor creates posts",
        "Search returns and displays results",
        "Tag filtering works"
      ],
      "priority": "high",
      "risk_level": "medium",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": "RULE-PRODUCT-SPLIT"
    },
    {
      "id": "TASK-VALIDATE",
      "title": "Validate forum against success criteria",
      "objective": "Verify forum meets all success signals: search <5s, 30% adoption potential",
      "description": "Run validation against each success criterion. Generate pass/fail report.",
      "phase": "validate",
      "parent_id": null,
      "group": "validate_phase",
      "execution_type": "atomic",
      "type": "testing",
      "tags": ["validation", "qa"],
      "deps": ["TASK-IMPL"],
      "agent_type": "QA Engineer",
      "required_inputs": ["source code", "success_criteria.md"],
      "expected_outputs": ["validation_report.json"],
      "done_definition": "All success criteria tested, report generated with metrics",
      "verification_steps": [
        "Search latency measured and documented",
        "All CRUD operations tested end-to-end",
        "Auth flow tested",
        "Report includes pass/fail per criterion"
      ],
      "priority": "high",
      "risk_level": "low",
      "enriched_by": "llm",
      "llm_enriched": true,
      "created_by_rule": null
    }
  ]
}
```

### DAG Visualization

```
TASK-PARSE
    ↓
TASK-DESIGN
    ↓
TASK-DESIGN.SCHEMA  (added by RULE-DATABASE)
    ↓
TASK-IMPL  (composite — done when all children done)
    ├── TASK-IMPL.BACKEND   (parallel, deps: TASK-DESIGN.SCHEMA)
    └── TASK-IMPL.FRONTEND  (parallel, deps: TASK-DESIGN.SCHEMA)
    ↓
TASK-VALIDATE  (depends on TASK-IMPL composite)
```

Composite node stays in the chain. Worker resolves TASK-IMPL as complete when both children succeed.

---

## 12. File Structure

```
src/ai_dev_system/
    task_graph/
        __init__.py
        skeleton.py         # CORE_SKELETON, build_skeleton()
        rules.py            # RULES, apply_rules(), add_parallel/before/after
        enricher.py         # LLMClient protocol, enrich_task(), enrich_all()
        validator.py        # validate_graph(), CORE_IDS, REQUIRED_FIELDS
        generator.py        # generate_task_graph(), GraphValidationError
    gate/
        gate2.py            # Gate2IO, Gate2Result, run_gate_2()
        stub_gate2.py       # StubGate2IO (test double)

tests/
    unit/
        test_skeleton.py        # always 4 nodes, correct deps
        test_rules.py           # each rule independently, add_parallel/before/after
        test_validator.py       # cycles, missing deps, missing core, duplicates
        test_enricher.py        # LLM mock, guardrails, fallback on error
    integration/
        test_generator.py       # full: brief → graph with rules + mock LLM
        test_gate2.py           # approve/reject/edit with StubGate2IO
        test_pipeline_full.py   # spec pipeline + task graph end-to-end
```

---

## 13. What This Does NOT Include

- **Dynamic rule discovery** — rules are hardcoded in v1. Rule Registry (match by tags/type) is future work.
- **Multi-level task expansion** — composite nodes don't recursively expand. One level of children only.
- **LLM-based graph planning** — LLM never decides structure. This is a deliberate constraint.
- **Task estimation** — no time/effort estimates on tasks.
- **Parallel execution optimization** — graph is correct but not optimized for max parallelism.

---

## 14. Success Criteria

1. `generate_task_graph(spec_content, brief, artifact_id)` produces valid graph from spec
2. Core skeleton always present (4 nodes) regardless of input
3. Rules fire correctly: product → split, database → schema, performance → perf test
4. LLM enrichment fills content without changing structure
5. LLM failure → graceful fallback (graph still valid)
6. Graph validator catches: cycles, missing deps, duplicate IDs, missing core nodes
7. Gate 2 approve/reject/edit works with StubGate2IO
8. Full pipeline: raw idea → spec → task graph → approved graph (end-to-end test)
