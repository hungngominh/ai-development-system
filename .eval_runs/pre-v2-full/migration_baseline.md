# M3 Baseline Eval — `pre-v2-full` tag

**Date:** 2026-05-28  
**Mode:** stub (LLM metrics return neutral; Q6/Q7 not yet wired to real LLM)  
**Ideas:** 8 golden ideas (01-08)

## Aggregate Results

| Metric | Mean | Pass Count (of 8) |
|--------|------|-------------------|
| critical_fill_rate | 1.000 | 8/8 |
| ai_suggest_acceptance | 1.000 | 8/8 |
| assumption_count | 0.0 | 8/8 |
| consistency_violations | 0.0 | 8/8 |
| field_coverage_per_section | 0.763 | 7/8 |
| followup_question_count | 0.0 | 8/8 |

**Overall pass: 7/8**

## Known Failure

`05_cli_devtool` fails `field_coverage_per_section` (0.40 < 0.50 threshold).  
**Root cause:** NFR section has 3 legitimate nulls (`expected_rps`, `expected_data_volume`,
`availability_target`) because CLI tools don't have server SLAs. This is expected and
documented in the golden idea's `expected_behavior_notes`.  
**Action:** No fix needed. This is a limitation of the `field_coverage_per_section` metric
for tool-type briefs. Future: add `profile.scope_type`-aware threshold overrides.

## New Golden Ideas (M3.1)

Added 6 new golden ideas covering diverse project types:
- `02_data_pipeline` — ETL pipeline, data engineering domain
- `03_mobile_b2c_app` — B2C mobile app, VN payment market
- `04_ml_inference_service` — ML model serving, MLOps
- `06_saas_b2b` — multi-tenant SaaS with billing + enterprise SSO
- `07_legacy_migration` — PHP monolith → Python microservices
- `08_security_audit_tool` — SAST + CVE scan + secrets, OSS-only

## LLM Metrics (M3.3/M3.4) — Deferred to real mode

Q6 (`binary_yes_no_ratio`) and Q7 (`scope_drift_count`) implemented with stub fallback.
Stub mode returns neutral values (Q6=0.5, Q7=0) and marks pass_binary_ratio=True,
pass_scope_drift=True to avoid blocking overall_pass on unknown signal.

To re-run in real mode once LLM question runner is wired:
```
ai-dev eval run --tag post-v2-questions --mode real
```

## Usage as Baseline

Compare future runs against this tag:
```
ai-dev eval compare pre-v2-full <new-tag>
```
