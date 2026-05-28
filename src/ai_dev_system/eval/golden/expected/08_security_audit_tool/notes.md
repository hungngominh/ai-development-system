# Eval Notes: 08_security_audit_tool

## Expected behavior

- Security tooling brief — all 3 scan types (SAST + dependency + secret) must have coverage.
- `must_not_use` includes Snyk SaaS + commercial SAST → only OSS tools acceptable.
- `data_residency=on-prem` + "code không được gửi ra ngoài" → all tools must be self-hostable.
- 5 `known_unknowns` → 5+ questions required.

## Question generation signal

- Hard constraint "no code leaves" → should drive constraint-aware questions (cloud-only tools rejected)
- `scope_in` mentions allow-list for false positives → suppression mechanism question required
- `scope_in` mentions Jira auto-ticket → integration approach + deduplication question
- `budget_infra=$0` + `must_not_use commercial` → all suggested tools must be OSS

## Failure modes to detect

- Generator asks about Snyk SaaS despite explicit constraint → forbidden
- Generator asks about DAST despite scope_out → forbidden
- Generator asks about IDE plugin → forbidden
- Generator misses false positive suppression → coverage gap (developer UX critical)
- Generator misses Jira deduplication concern → coverage gap (ticket spam is common failure)
