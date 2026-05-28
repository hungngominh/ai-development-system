# Eval Notes: 02_data_pipeline

## Expected behavior

- Brief should fill 8 critical fields. `deployment_target` is `?` (user skipped), AI suggest should
  propose EU cloud (AWS eu-west or Hetzner EU) because: GDPR data residency + budget $500/month.
- `data_residency=EU` + `compliance=GDPR` should drive both the deployment suggestion and
  generate a mandatory question about PII/GDPR handling in the pipeline.

## Question generation signal

Strong signals the generator should pick up:
- `known_unknowns` has 3 explicit decisions → should generate at least 3 questions mapping to these
- `must_not_use` includes Fivetran/SaaS + constraint against BigQuery → generator must not propose BigQuery
- `compliance=GDPR` → PII event handling question is mandatory

## Failure modes to detect

- Generator asks about BigQuery despite explicit constraint → `scope_drift` (forbidden)
- Generator asks about BI dashboard despite scope_out → `scope_drift`
- Generator generates only yes/no questions like "Should you use Kafka?" without context → `binary_yes_no_ratio` high
- Generator misses schema evolution despite explicit known_unknown → coverage gap
