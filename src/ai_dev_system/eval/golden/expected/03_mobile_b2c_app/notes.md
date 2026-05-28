# Eval Notes: 03_mobile_b2c_app

## Expected behavior

- `existing_auth=null` (greenfield) → generator must ask about auth approach (OTP is VN norm).
- `must_integrate_with` includes VNPay + MoMo → payment webhook reliability question required.
- 4 `known_unknowns` should map to 4+ questions.
- PDPD VN compliance + data_residency VN → data protection question.

## Question generation signal

Strong signals:
- VN market context → OTP phone auth expected over social login
- `known_unknowns` has 4 explicit items
- Payment webhooks (VNPay/MoMo) are notoriously flaky in VN market → idempotency question
- React Native cross-platform → no need to ask about iOS vs Android separately

## Failure modes to detect

- Generator asks about shipper app (explicit scope_out) → forbidden
- Generator asks generic "should you use auth?" without VN context → low quality
- Generator asks about web desktop app → forbidden
- Generator misses payment webhook reliability → coverage gap (critical for VN market)
