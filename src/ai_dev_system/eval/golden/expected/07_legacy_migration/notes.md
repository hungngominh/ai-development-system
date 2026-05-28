# Eval Notes: 07_legacy_migration

## Expected behavior

- Migration brief — generator must ask about migration strategy, NOT new features.
- `failed_attempts` includes big bang rewrite → strangler fig pattern is strongly implied.
- `known_unknowns` has 5 items → 5+ questions required.
- Political constraint "rollback plan must be tested" → rollback question mandatory.
- Bus factor 1 as explicit pain → generator should recognize this and ask about knowledge transfer.

## Question generation signal

- "Không thể downtime quá 2 giờ" → cutover strategy question
- "Bus factor 1" in problem_statement → knowledge transfer / documentation question
- `failed_attempts` mentions big bang failure → strangler fig is preferred path
- `coexistence period` implied by scope_in (API gateway routing old→new)

## Failure modes to detect

- Generator asks about new feature development during migration → forbidden
- Generator asks about frontend rewrite → forbidden
- Generator asks about switching payment gateways → forbidden
- Generator suggests big bang rewrite despite failed_attempts evidence → quality issue
- Generator misses data sync strategy → major coverage gap
