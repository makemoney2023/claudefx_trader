"""Tests for the prompt baseline report computation helpers."""
from datetime import datetime, timezone

from scripts.prompt_baseline_report import (
    build_report,
    parse_dt,
    split_by_cutover,
    summarize,
)


def _row(**kw):
    base = {
        "decision_id": "d",
        "outcome_type": "executed",
        "gate_id": None,
        "symbol": "EURUSD",
        "direction": "long",
        "confidence": 0.8,
        "session": "london",
        "mode": "normal",
        "judge_verdict": "APPROVE",
        "timestamp": "2026-07-10 12:00:00",
        "hypothetical_result": None,
        "hypothetical_r": None,
        "mfe_r": None,
        "mae_r": None,
        "outcome_worker_status": "pending",
    }
    base.update(kw)
    return base


CUTOVER = datetime(2026, 7, 13, tzinfo=timezone.utc)


class TestParseDt:
    def test_parses_sqlite_datetime_as_utc(self):
        dt = parse_dt("2026-07-10 12:00:00.500000")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.month == 7 and dt.day == 10

    def test_returns_none_for_empty(self):
        assert parse_dt(None) is None
        assert parse_dt("") is None


class TestSplitByCutover:
    def test_splits_before_and_after(self):
        rows = [
            _row(timestamp="2026-07-10 09:00:00"),
            _row(timestamp="2026-07-12 23:59:59"),
            _row(timestamp="2026-07-13 00:00:00"),  # on cutover -> after
            _row(timestamp="2026-07-15 10:00:00"),
        ]
        buckets = split_by_cutover(rows, CUTOVER)
        assert len(buckets["before"]) == 2
        assert len(buckets["after"]) == 2

    def test_window_days_limits_each_side(self):
        rows = [
            _row(timestamp="2026-06-01 00:00:00"),  # >5 days before -> excluded
            _row(timestamp="2026-07-11 00:00:00"),  # within 5 days before
            _row(timestamp="2026-07-14 00:00:00"),  # within 5 days after
            _row(timestamp="2026-08-01 00:00:00"),  # >5 days after -> excluded
        ]
        buckets = split_by_cutover(rows, CUTOVER, window_days=5)
        assert len(buckets["before"]) == 1
        assert len(buckets["after"]) == 1

    def test_skips_rows_without_timestamp(self):
        rows = [_row(timestamp=None), _row(timestamp="2026-07-10 00:00:00")]
        buckets = split_by_cutover(rows, CUTOVER)
        assert len(buckets["before"]) == 1
        assert len(buckets["after"]) == 0


class TestSummarize:
    def test_counts_and_direction_distribution(self):
        rows = [
            _row(direction="long"),
            _row(direction="short"),
            _row(direction="short"),
        ]
        summary = summarize(rows)
        assert summary["count"] == 3
        assert summary["direction"] == {"short": 2, "long": 1}

    def test_confidence_stats(self):
        rows = [_row(confidence=0.6), _row(confidence=0.8), _row(confidence=1.0)]
        stats = summarize(rows)["confidence"]
        assert stats["n"] == 3
        assert stats["min"] == 0.6 and stats["max"] == 1.0

    def test_evaluated_outcomes_only_count_evaluated_rows(self):
        rows = [
            _row(outcome_worker_status="evaluated", hypothetical_result="win", hypothetical_r=2.0),
            _row(outcome_worker_status="pending", hypothetical_result="loss", hypothetical_r=-1.0),
        ]
        summary = summarize(rows)
        assert summary["evaluated_count"] == 1
        assert summary["hypothetical_result"] == {"win": 1}
        assert summary["hypothetical_r"]["mean"] == 2.0


class TestBuildReport:
    def test_report_has_before_and_after(self):
        rows = [
            _row(timestamp="2026-07-10 00:00:00", direction="short"),
            _row(timestamp="2026-07-14 00:00:00", direction="long"),
        ]
        report = build_report(rows, CUTOVER)
        assert report["before"]["count"] == 1
        assert report["after"]["count"] == 1
        assert report["before"]["direction"] == {"short": 1}
        assert report["after"]["direction"] == {"long": 1}
