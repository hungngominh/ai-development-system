"""TerminalGate2IO — an interactive, terminal-driven Gate 2 (task-graph review).

Implements the ``Gate2IO`` protocol (gate/gate2.py): render the generated task
graph, let a human edit it (set fields, remove tasks, add/remove deps), then
approve or reject. ``approve`` re-runs ``validate_graph`` so a human edit can't
ship a broken graph.

The input source is injected as ``prompt_fn`` (default ``input``) so the same
class is testable with a scripted driver and a future front-end (web/skill) can
supply edits without stdin. Rendering goes to ``out`` (default stderr) to keep
stdout free for the pipeline's final JSON.
"""
from __future__ import annotations

import copy
import shlex
import sys
from typing import Callable, Literal

from ai_dev_system.task_graph.facets import FACET_KEYS
from ai_dev_system.task_graph.validator import validate_graph

_EDITABLE_FIELDS = {
    "title", "objective", "description", "phase", "type",
    "agent_type", "priority", "risk_level", "group", "done_definition",
}

_HELP = (
    "Commands:\n"
    "  list                      re-render the graph\n"
    "  show <ID>                 show one task's full detail\n"
    "  set <ID> <field> <value>  edit a scalar field (objective, agent_type, …)\n"
    "  remove <ID>               delete a task (and scrub it from other deps)\n"
    "  dep add <ID> <DEP>        add a dependency\n"
    "  dep remove <ID> <DEP>     remove a dependency\n"
    "  facet show <ID>           show a task's 8 facets\n"
    "  facet set <ID> <key> <v>  fill/replace a facet (status→filled)\n"
    "  facet na <ID> <key> <why> mark a facet not-applicable\n"
    "  approve                   validate + accept the graph\n"
    "  reject                    abort Phase B\n"
    "  help"
)


class TerminalGate2IO:
    def __init__(
        self,
        prompt_fn: Callable[..., str] = input,
        out=None,
        auto_approve: bool = False,
    ) -> None:
        self._prompt = prompt_fn
        self._out = out if out is not None else sys.stderr
        self._auto_approve = auto_approve

    # --- Gate2IO protocol ---

    def present_graph(self, graph_envelope: dict) -> None:
        self._render(graph_envelope.get("tasks", []))

    def collect_edits(self, graph_envelope: dict) -> tuple[Literal["approve", "reject"], dict]:
        if self._auto_approve:
            self._emit("[gate2] auto-approve → accepting task graph unedited.")
            return "approve", graph_envelope

        edited = copy.deepcopy(graph_envelope)
        self._emit(_HELP)
        while True:
            try:
                raw = self._prompt("gate2> ")
            except EOFError:
                self._emit("[gate2] end of input → approving.")
                return "approve", edited
            cmd = (raw or "").strip()
            if not cmd:
                continue
            outcome = self._dispatch(cmd, edited)
            if outcome is not None:
                return outcome

    # --- command dispatch ---

    def _dispatch(self, cmd: str, edited: dict):
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        if not parts:
            return None
        verb = parts[0].lower()
        tasks = edited["tasks"]

        if verb in ("approve", "confirm", "a"):
            errors = validate_graph(tasks)
            if errors:
                self._emit("Cannot approve — graph is invalid:")
                for e in errors:
                    self._emit(f"  - {e}")
                return None
            pending = self._needs_human_facets(tasks)
            if pending:
                self._emit(f"[gate2] WARNING: {len(pending)} facet(s) still needs_human:")
                for tid, key in pending:
                    self._emit(f"  - {tid}.{key}")
                self._emit("Approving anyway (facets are advisory).")
            return ("approve", edited)
        if verb in ("reject", "abort", "quit", "q"):
            return ("reject", edited)
        if verb in ("help", "h", "?"):
            self._emit(_HELP)
            return None
        if verb in ("list", "ls"):
            self._render(tasks)
            return None
        if verb == "show" and len(parts) >= 2:
            self._show(tasks, parts[1])
            return None
        if verb == "set" and len(parts) >= 4:
            self._set(tasks, parts[1], parts[2], " ".join(parts[3:]))
            return None
        if verb == "remove" and len(parts) >= 2:
            self._remove(tasks, parts[1])
            return None
        if verb == "dep" and len(parts) >= 4:
            self._dep(tasks, parts[1].lower(), parts[2], parts[3])
            return None
        if verb == "facet" and len(parts) >= 3:
            self._facet(tasks, parts[1].lower(), parts[2],
                        parts[3] if len(parts) >= 4 else "",
                        " ".join(parts[4:]) if len(parts) >= 5 else "")
            return None

        self._emit(f"Unknown/invalid command: {cmd!r}. Type 'help'.")
        return None

    # --- edit operations ---

    @staticmethod
    def _find(tasks: list[dict], tid: str) -> dict | None:
        return next((t for t in tasks if t["id"] == tid), None)

    def _set(self, tasks, tid, field, value):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        if field not in _EDITABLE_FIELDS:
            self._emit(
                f"Field {field!r} is not editable. Editable: {sorted(_EDITABLE_FIELDS)}. "
                f"(Use 'dep add|remove' for dependencies.)"
            )
            return
        task[field] = value
        self._emit(f"{tid}.{field} = {value!r}")

    def _remove(self, tasks, tid):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        tasks.remove(task)
        for other in tasks:
            if tid in other.get("deps", []):
                other["deps"] = [d for d in other["deps"] if d != tid]
        self._emit(f"Removed {tid} (scrubbed from dependencies).")

    def _dep(self, tasks, op, tid, dep):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        deps = task.setdefault("deps", [])
        if op == "add":
            if dep not in deps:
                deps.append(dep)
            self._emit(f"{tid}.deps = {deps}")
        elif op == "remove":
            task["deps"] = [d for d in deps if d != dep]
            self._emit(f"{tid}.deps = {task['deps']}")
        else:
            self._emit("Usage: dep add|remove <ID> <DEP>")

    def _needs_human_facets(self, tasks):
        out = []
        for t in tasks:
            for key, f in (t.get("facets") or {}).items():
                if isinstance(f, dict) and f.get("status") == "needs_human":
                    out.append((t["id"], key))
        return out

    def _facet(self, tasks, op, tid, key, value):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        facets = task.setdefault("facets", {})
        if op == "show":
            self._emit(f"--- {tid} facets ---")
            for k in FACET_KEYS:
                f = facets.get(k) or {"status": "needs_human", "content": "", "reason": ""}
                self._emit(f"  {k}: [{f.get('status')}] {f.get('content') or f.get('reason')}")
            return
        if key not in FACET_KEYS:
            self._emit(f"Unknown facet {key!r}. Valid: {', '.join(FACET_KEYS)}")
            return
        if op == "set":
            facets[key] = {"status": "filled", "content": value, "reason": ""}
            self._emit(f"{tid}.{key} = filled: {value!r}")
        elif op == "na":
            facets[key] = {"status": "na", "content": "", "reason": value}
            self._emit(f"{tid}.{key} = na ({value!r})")
        else:
            self._emit("Usage: facet show|set|na <ID> [<key> <value>]")

    # --- rendering ---

    def _render(self, tasks: list[dict]) -> None:
        self._emit("")
        self._emit(f"=== Gate 2: task graph ({len(tasks)} tasks) ===")
        for t in tasks:
            deps = ", ".join(t.get("deps", [])) or "-"
            self._emit(
                f"  {t['id']}  [{t.get('phase', '?')}/{t.get('type', '?')}]  "
                f"agent={t.get('agent_type', '?')}  deps=[{deps}]"
            )
            objective = t.get("objective") or t.get("title") or ""
            if objective:
                self._emit(f"      {objective}")
            facets = t.get("facets")
            if facets:
                filled = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "filled")
                nh = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "needs_human")
                na = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "na")
                self._emit(f"      facets: {filled} filled / {nh} needs-human / {na} N/A")
        self._emit("Type 'help' for commands; 'approve' or 'reject' to finish.")

    def _show(self, tasks, tid):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        self._emit(f"--- {tid} ---")
        for k, v in task.items():
            self._emit(f"  {k}: {v}")

    def _emit(self, line: str = "") -> None:
        print(line, file=self._out, flush=True)
