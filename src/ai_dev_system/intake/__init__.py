"""Intake Wizard — Phase 1a-0 brief collection (M2 slice 1+2).

State machine + template + DB checkpoint for the 3-stage intake wizard
described in `docs/superpowers/specs/2026-05-23-intake-wizard-design.md`.

S1+S2 scope (this session):
- `template.py`     — load + validate generic_v1.yaml
- `engine.py`       — pure ASKING-state machine
- `repo.py`         — DB checkpoint to runs.intake_state
- `brief.py`        — promote INTAKE_BRIEF artifact on confirm

Deferred to S3+:
- `suggest.py`      — LLM-driven '?' proposals
- `followup.py`     — 4 gap detection logics
- `consistency_rules.py`
- `digest.py`       — brief → 500-token digest
- resume command
"""
