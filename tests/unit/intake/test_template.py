"""Template loader tests."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.template import (
    Template,
    TemplateError,
    load_template,
)


def test_generic_v1_loads():
    tpl = load_template("generic_v1")
    assert tpl.id == "generic_v1"
    assert tpl.version == 1
    assert len(tpl.fields) >= 30
    assert tpl.schema_hash  # non-empty hex


def test_generic_v1_has_eight_critical_fields():
    tpl = load_template("generic_v1")
    crit = tpl.critical_field_ids
    expected = {
        "problem_statement", "scope_in", "scope_out", "success_metric",
        "primary_user", "deployment_target", "compliance", "current_workaround",
    }
    assert set(crit) == expected, f"Critical set mismatch: {set(crit)} vs {expected}"


def test_generic_v1_field_ids_unique():
    tpl = load_template("generic_v1")
    ids = [f.id for f in tpl.fields]
    assert len(ids) == len(set(ids))


def test_field_by_id_roundtrip():
    tpl = load_template("generic_v1")
    f = tpl.field_by_id("scope_in")
    assert f.type == "list_str"
    assert f.critical is True
    assert tpl.field_index("scope_in") == [i for i, x in enumerate(tpl.fields) if x.id == "scope_in"][0]


def test_field_by_id_missing_raises():
    tpl = load_template("generic_v1")
    with pytest.raises(KeyError):
        tpl.field_by_id("does_not_exist")


def test_load_unknown_template_raises():
    with pytest.raises(TemplateError, match="not found"):
        load_template("doesnt_exist")


def test_enum_field_has_options():
    tpl = load_template("generic_v1")
    fld = tpl.field_by_id("greenfield_or_brownfield")
    assert fld.type == "enum"
    assert set(fld.options) == {"greenfield", "brownfield", "hybrid"}
