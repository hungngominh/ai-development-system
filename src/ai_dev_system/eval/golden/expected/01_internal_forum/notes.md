# 01_internal_forum — Authoring Notes

## Why this idea was chosen for the golden set

Tests **multi-domain breadth** without being absurdly complex:
- 4 primary domains (backend, security, product, database)
- Has hard tech constraints (PostgreSQL, React, Python) → easy to verify forbidden_decisions
- Has an explicit skip (`deployment_target = "?"`) → tests AI suggest mechanism
- Has 3 known_unknowns → tests whether generator picks them up
- Brief is mid-size (not trivial like CLI tool, not enterprise like SaaS B2B)

## Decision rationale

### Required (10 decisions)

I picked these by walking `scope_in` and asking "what design choice does this item force?":

| Scope item | Forces |
|---|---|
| posts | tags vs categories (#tags_or_categories) |
| comments threaded 2-level | enforcement layer (#comment_depth_limit) |
| voting | anti-abuse (#voting_anti_abuse) |
| leaderboard | scoring (#leaderboard_scoring_rule) + refresh (#leaderboard_refresh_strategy) |
| search full-text | engine choice (#search_engine_choice), indexing (#search_indexing_strategy), ranking (#search_result_ranking) |

Plus 2 from known_unknowns: moderation_policy, notification_channel.

### Forbidden (7 decisions)

I picked things where brief gives unambiguous answer:
- `must_use_stack` → no debate on DB/frontend/backend choice
- `must_not_use` → no Mongo/MySQL debate
- `existing_auth` → no auth-method debate
- `compliance=none` → no GDPR/HIPAA debate
- Strong implied: `data_residency=VN + budget=$200/mo` → cloud should be SUGGESTED not DEBATED

## Pattern coverage caveats

- **Search domain term:** "search" alone matches too much. I qualified with engine names or context like ".*FTS".
- **Vietnamese matching:** included `chọn`, `dùng`, `cách`, `cơ chế` for Vi alternatives.
- **2-level depth:** the `(?:.{0,5})` between "2" and "level" handles "2-level", "2 level", "2-cấp", etc.

## What this idea does NOT test

- Mobile-specific concerns (use 03_mobile_b2c_app)
- ML/AI inference cost (04_ml_inference_service)
- Multi-tenant billing (06_saas_b2b)
- Compliance heaviness (08_security_audit_tool)
- Migration from existing (07_legacy_migration)

Together with **05_cli_devtool** (low-complexity, single-domain) this pair gives the harness a working measurement before we add the other 6 in M3.
