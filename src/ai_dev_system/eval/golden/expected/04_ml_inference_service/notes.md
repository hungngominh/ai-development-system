# Eval Notes: 04_ml_inference_service

## Expected behavior

- MLOps-focused brief; generator should produce MLOps/operational questions, not ML accuracy questions.
- `must_not_use` SageMaker explicitly → forbidden question.
- 5 `known_unknowns` → should map to 5+ questions.
- `compliance=PCI-DSS` + logs → PCI masking question required.
- `deployment_target=on-prem K8s` → serving framework choice constrained to K8s-compatible options.

## Question generation signal

- Model swap without downtime (`scope_in`) → deploy strategy question mandatory
- Fraud model without drift detection = silent failure → drift question critical
- PCI-DSS + request logs = masking required → security question mandatory

## Failure modes to detect

- Generator asks about model training pipeline (explicit scope_out) → forbidden
- Generator asks about SageMaker despite constraint → forbidden
- Generator focuses on model accuracy/F1 instead of operational concerns → scope drift
- Generator misses PCI-DSS log masking → coverage gap
