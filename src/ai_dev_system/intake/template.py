"""Intake template loader.

Loads `generic_v1.yaml` (or any future template) into typed dataclasses so the
state machine doesn't deal with raw dicts.

Critical invariants enforced at load time:
- field ids are unique across all sections
- at least one critical field exists
- enum fields declare `options`
- `ai_can_suggest` is explicit (no default — forces a deliberate choice)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


FieldType = Literal["text_short", "text_long", "list_str", "enum", "number"]
TEMPLATES_DIR = Path(__file__).parent / "templates"


class TemplateError(ValueError):
    """Raised when a template YAML is malformed or violates invariants."""


@dataclass(frozen=True)
class TemplateField:
    id: str
    prompt: str
    type: FieldType
    critical: bool
    ai_can_suggest: bool
    section_id: str
    examples_hint: str | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    title: str
    fields: tuple[TemplateField, ...]
    schema_hash: str

    @property
    def critical_field_ids(self) -> tuple[str, ...]:
        return tuple(f.id for f in self.fields if f.critical)

    def field_by_id(self, fid: str) -> TemplateField:
        for f in self.fields:
            if f.id == fid:
                return f
        raise KeyError(f"Unknown field id: {fid!r}")

    def field_index(self, fid: str) -> int:
        for i, f in enumerate(self.fields):
            if f.id == fid:
                return i
        raise KeyError(f"Unknown field id: {fid!r}")


def _validate_field(raw: dict, section_id: str) -> TemplateField:
    required_keys = {"id", "prompt", "type", "critical", "ai_can_suggest"}
    missing = required_keys - raw.keys()
    if missing:
        raise TemplateError(
            f"Field in section {section_id!r} missing keys {sorted(missing)}: {raw}"
        )

    ftype = raw["type"]
    if ftype not in ("text_short", "text_long", "list_str", "enum", "number"):
        raise TemplateError(
            f"Field {raw['id']!r}: unknown type {ftype!r}"
        )

    options = tuple(raw.get("options", ()) or ())
    if ftype == "enum" and not options:
        raise TemplateError(f"enum field {raw['id']!r} must declare `options`")

    return TemplateField(
        id=raw["id"],
        prompt=raw["prompt"],
        type=ftype,
        critical=bool(raw["critical"]),
        ai_can_suggest=bool(raw["ai_can_suggest"]),
        section_id=section_id,
        examples_hint=raw.get("examples_hint"),
        options=options,
    )


def load_template(template_id: str = "generic_v1") -> Template:
    """Load and validate a template from the packaged templates dir.

    Raises TemplateError on any structural problem.
    """
    path = TEMPLATES_DIR / f"{template_id}.yaml"
    if not path.exists():
        raise TemplateError(f"Template {template_id!r} not found at {path}")

    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)

    if not isinstance(data, dict):
        raise TemplateError(f"Template {template_id!r}: top-level must be a mapping")
    if data.get("id") != template_id:
        raise TemplateError(
            f"Template id mismatch: file says {data.get('id')!r}, "
            f"expected {template_id!r}"
        )

    sections = data.get("sections") or []
    if not sections:
        raise TemplateError(f"Template {template_id!r} has no sections")

    fields: list[TemplateField] = []
    seen_ids: set[str] = set()
    for section in sections:
        sid = section.get("id") or "<unnamed>"
        for raw_field in section.get("fields") or []:
            tf = _validate_field(raw_field, sid)
            if tf.id in seen_ids:
                raise TemplateError(f"Duplicate field id: {tf.id!r}")
            seen_ids.add(tf.id)
            fields.append(tf)

    if not fields:
        raise TemplateError(f"Template {template_id!r} has no fields")
    if not any(f.critical for f in fields):
        raise TemplateError(f"Template {template_id!r} has no critical fields")

    return Template(
        id=template_id,
        version=int(data.get("version", 1)),
        title=str(data.get("title", template_id)),
        fields=tuple(fields),
        schema_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
    )
