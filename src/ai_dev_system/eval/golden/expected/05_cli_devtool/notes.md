# 05_cli_devtool — Authoring Notes

## Why this idea was chosen

**Opposite end of the spectrum from 01_internal_forum:**
- Low complexity, single primary domain (backend)
- Many fields legitimately `null` (no auth, no SLA, no residency, no infra budget)
- Tests **whether generator avoids over-asking** when brief is already specific
- Tests handling of `null` vs missing — `null` = "not applicable here", not "user forgot"

If the generator asks about authentication for a CLI tool, that's a strong F3 (scope drift) signal.

## Required decision rationale

Only 6 required, vs 10 for internal_forum. Reflects lower complexity:

| Source | Decisions |
|---|---|
| `known_unknowns` directly | migration_naming_convention, plugin_system |
| Implicit from scope (diff is hard problem) | column_rename_detection, schema_format_spec |
| Implicit from scope (rollback mentioned) | rollback_semantics |
| Operational concern (multi-dev usage) | concurrent_migration_safety |

## Forbidden — heavy list

8 forbidden decisions because CLI tools don't have most web-app concerns. This is the **discriminating test**:
- If generator asks about auth, SLA, scaling, UI → bad
- If generator asks about Python vs Go → bad (already constrained)
- If generator asks "how to distribute?" → bad (deployment_target = "PyPI + GitHub")

## Sensitivity

This idea is **easy to score** because the forbidden patterns are crisp. If a generator scores well on 05_cli_devtool but poorly on 01_internal_forum, that suggests the issue is multi-domain breadth, not over-asking.

## What this idea does NOT test

- Complex orchestration scenarios
- User-facing UX decisions
- Compliance/legal nuance
- Multi-tenant patterns

Those go to other golden ideas in M3.
