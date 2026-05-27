# src/ai_dev_system/gate/gate1_review/editor.py
"""Gate 1 review — brief field editor (G7+G8).

Allows limited brief field edits at Gate 1. Scope-affecting edits
(scope_in / scope_out) set scope_affected=True in GateSessionState (G8),
which cmd_finalize surfaces so the skill can warn the user and offer to
re-trigger the debate pipeline.

Editable fields (whitelist from spec gate1-skill-redesign §Brief Edit at Gate):
    problem_statement, who_feels_pain, current_workaround,
    cost_of_doing_nothing, success_metric, done_definition, deadline,
    nfr_priority, known_unknowns,
    scope_in, scope_out (may affect questions — flagged but not re-triggered here)

Non-editable at gate (would invalidate entire debate):
    compliance, data_residency, deployment_target,
    must_use_stack, must_not_use

For list fields (scope_in, scope_out, nfr_priority, known_unknowns), three
operations are supported:
    set   → replace entire list with new_value (list)
    append → add a single string item to the list
    remove → remove a single string item from the list (if present)

For scalar fields, only `set` is supported.

Edits are logged to an in-memory BriefEdit list; the caller is responsible
for persisting them (e.g. writing BRIEF_EDIT_LOG artifact or stamping onto
BRIEF_FINAL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Operation = Literal["set", "append", "remove"]

# Fields that can be edited at Gate 1
EDITABLE_FIELDS: frozenset[str] = frozenset({
    "problem_statement",
    "who_feels_pain",
    "current_workaround",
    "cost_of_doing_nothing",
    "success_metric",
    "done_definition",
    "deadline",
    "nfr_priority",
    "known_unknowns",
    "scope_in",
    "scope_out",
})

# List-typed fields (support append/remove operations)
LIST_FIELDS: frozenset[str] = frozenset({
    "scope_in",
    "scope_out",
    "nfr_priority",
    "known_unknowns",
})

# Fields whose change may affect debate questions (flagged but not re-triggered in G7)
SCOPE_AFFECTING_FIELDS: frozenset[str] = frozenset({
    "scope_in",
    "scope_out",
})

# Fields that cannot be changed at gate
NON_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "compliance",
    "data_residency",
    "deployment_target",
    "must_use_stack",
    "must_not_use",
})


@dataclass
class BriefEdit:
    field: str
    operation: Operation
    old_value: object
    new_value: object


@dataclass
class EditResult:
    accepted: bool
    message: str
    brief: dict                 # updated brief (same object if rejected)
    edit: BriefEdit | None = None
    scope_affected: bool = False  # True if scope_in/scope_out changed


def apply_edit(
    brief: dict,
    field: str,
    operation: Operation,
    value: object,
) -> EditResult:
    """Apply a single brief field edit.

    Args:
        brief: the current brief dict (will be mutated on success).
        field: the field name to edit.
        operation: "set", "append", or "remove".
        value: new value (scalar for set; string item for append/remove).

    Returns:
        EditResult with `accepted=True` if the edit was applied,
        `accepted=False` with a user-facing `message` if rejected.
    """
    # Non-editable check
    if field in NON_EDITABLE_FIELDS:
        return EditResult(
            accepted=False,
            brief=brief,
            message=(
                f"Field `{field}` không được sửa tại Gate 1 — "
                f"thay đổi trường này sẽ làm mất hiệu lực toàn bộ kết quả debate. "
                f"Hãy abort và làm lại từ intake nếu cần."
            ),
        )

    # Unknown field check
    if field not in EDITABLE_FIELDS:
        known = ", ".join(sorted(EDITABLE_FIELDS))
        return EditResult(
            accepted=False,
            brief=brief,
            message=(
                f"Field `{field}` không hỗ trợ sửa tại Gate 1. "
                f"Các field có thể sửa: {known}."
            ),
        )

    # List field operations
    if field in LIST_FIELDS:
        return _apply_list_edit(brief, field, operation, value)

    # Scalar field — only "set" makes sense
    if operation != "set":
        return EditResult(
            accepted=False,
            brief=brief,
            message=f"Field `{field}` là scalar — chỉ hỗ trợ operation `set`, không phải `{operation}`.",
        )
    return _apply_scalar_edit(brief, field, value)


def _apply_scalar_edit(brief: dict, field: str, value: object) -> EditResult:
    old = brief.get(field)
    brief[field] = value
    edit = BriefEdit(field=field, operation="set", old_value=old, new_value=value)
    return EditResult(
        accepted=True,
        brief=brief,
        edit=edit,
        message=f"Brief updated: `{field}` = {value!r}",
    )


def _apply_list_edit(brief: dict, field: str, operation: Operation, value: object) -> EditResult:
    current: list = list(brief.get(field) or [])

    if operation == "set":
        if not isinstance(value, list):
            return EditResult(
                accepted=False, brief=brief,
                message=f"Operation `set` trên list field `{field}` cần value là list, không phải {type(value).__name__}.",
            )
        old = current
        brief[field] = list(value)
        edit = BriefEdit(field=field, operation="set", old_value=old, new_value=list(value))
        msg = f"Brief updated: `{field}` = {value!r}"

    elif operation == "append":
        if not isinstance(value, str):
            return EditResult(
                accepted=False, brief=brief,
                message=f"Operation `append` cần value là str.",
            )
        if value in current:
            return EditResult(
                accepted=False, brief=brief,
                message=f"Item {value!r} đã có trong `{field}`, không cần append.",
            )
        old = list(current)
        current.append(value)
        brief[field] = current
        edit = BriefEdit(field=field, operation="append", old_value=old, new_value=current)
        msg = f"Brief updated: `{field}` += {value!r} → {current!r}"

    elif operation == "remove":
        if not isinstance(value, str):
            return EditResult(
                accepted=False, brief=brief,
                message=f"Operation `remove` cần value là str.",
            )
        if value not in current:
            return EditResult(
                accepted=False, brief=brief,
                message=f"Item {value!r} không có trong `{field}`, không thể remove.",
            )
        old = list(current)
        current.remove(value)
        brief[field] = current
        edit = BriefEdit(field=field, operation="remove", old_value=old, new_value=current)
        msg = f"Brief updated: `{field}` -= {value!r} → {current!r}"

    else:
        return EditResult(
            accepted=False, brief=brief,
            message=f"Unsupported operation `{operation}`. Dùng: set | append | remove.",
        )

    scope_affected = field in SCOPE_AFFECTING_FIELDS
    if scope_affected:
        msg += (
            "\n\n⚠️ Scope thay đổi — câu hỏi debate có thể không còn phản ánh đúng scope mới. "
            "Khi finalize, hệ thống sẽ cảnh báo để bạn xem xét re-trigger debate pipeline."
        )

    return EditResult(
        accepted=True, brief=brief, edit=edit,
        scope_affected=scope_affected, message=msg,
    )


def check_editable(field: str) -> tuple[bool, str]:
    """Return (is_editable, reason) for a field name.

    Convenience for pre-flight checks in parser before full apply.
    """
    if field in NON_EDITABLE_FIELDS:
        return False, (
            f"`{field}` không thể sửa tại Gate 1 (thay đổi sẽ mất hiệu lực debate)."
        )
    if field not in EDITABLE_FIELDS:
        return False, f"`{field}` không hỗ trợ sửa tại Gate 1."
    return True, ""
