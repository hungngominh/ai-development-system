# Eval Notes: 06_saas_b2b

## Expected behavior

- `data_residency=?` (skipped) → AI suggest should propose AWS ap-southeast-1 given: SEA market +
  SOC 2 Type 1 requirement + GDPR (some EU customers possible).
- 5 `known_unknowns` → 5+ questions required.
- Multi-tenancy and data isolation are core architecture — must have questions about both.
- SOC 2 Type 1 compliance → security control questions required.

## Question generation signal

- `must_integrate_with` has Stripe → billing question still needed (confirm Stripe only vs others)
- Enterprise SSO requirement in `scope_in` → SAML/OIDC/Auth0 question
- Multi-tenancy model is the single most important architecture decision for SaaS B2B

## Failure modes to detect

- Generator asks about MongoDB despite explicit constraint → forbidden
- Generator asks about mobile native app → forbidden
- Generator misses multi-tenancy model question → major coverage gap
- Generator misses SOC 2 compliance question → compliance gap
- Generator generates shallow binary questions like "Should you use Stripe?" → binary_yes_no_ratio issue
