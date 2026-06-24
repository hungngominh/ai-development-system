"""Unit tests for the local web dashboard's pure decision helpers.

The dashboard's process/HTTP plumbing isn't unit-friendly, but the two bits of
logic that caused (and now guard against) the "frozen progress" bug are pure:
deciding when a RUNNING run looks orphaned, and which log lines count as
progress. Those are tested here.
"""
from __future__ import annotations

from ai_dev_system import webui


class TestLooksStale:
    """A RUNNING run whose progress log has gone silent past the threshold is
    treated as orphaned (its background process died)."""

    def test_not_running_is_never_stale(self):
        assert webui._looks_stale(False, False, 99999.0, threshold=300) is False

    def test_has_report_is_never_stale(self):
        # once the report exists we render it, never the stale card
        assert webui._looks_stale(True, True, 99999.0, threshold=300) is False

    def test_no_log_yet_is_not_stale(self):
        # idle_seconds is None when the log file doesn't exist yet
        assert webui._looks_stale(True, False, None, threshold=300) is False

    def test_recent_activity_is_not_stale(self):
        assert webui._looks_stale(True, False, 10.0, threshold=300) is False

    def test_silent_past_threshold_is_stale(self):
        assert webui._looks_stale(True, False, 600.0, threshold=300) is True

    def test_exactly_at_threshold_is_stale(self):
        assert webui._looks_stale(True, False, 300.0, threshold=300) is True


class TestIsProgressLine:
    """The progress filter must keep the child's status markers and drop noise."""

    def test_debate_round_and_phase_markers_kept(self):
        assert webui._is_progress_line("[debate] questions: 20")
        assert webui._is_progress_line("           round 1/5:  A ok  B ok")
        assert webui._is_progress_line("[Phase 1a/1b] Running debate pipeline")
        assert webui._is_progress_line("[Done]     DEBATE_REPORT promoted.")
        assert webui._is_progress_line("Pipeline error: boom")

    def test_aborted_marker_is_kept(self):
        # the new abort marker must survive the filter so the dashboard shows it
        # instead of a frozen tail
        assert webui._is_progress_line("[Aborted]  interrupted before completion")

    def test_noise_is_dropped(self):
        assert not webui._is_progress_line("DeprecationWarning: 'start' is deprecated.")
        assert not webui._is_progress_line("==== start name='x' mode=max ====")
        assert not webui._is_progress_line('{"run_id": "abc", "status": "PAUSED"}')
