"""
Prompt baseline report.

Compares the distribution of `signal_decisions` telemetry BEFORE vs AFTER a cutover
timestamp so we can measure the behavioural impact of the Claude Opus 4.8 migration +
prompt hardening. Pass the moment the new prompts went live as ``--cutover`` and the
script prints a side-by-side comparison of decision mix, direction bias, judge verdicts,
confidence, and (where the outcome worker has evaluated them) hypothetical performance.

Usage:
    python -m scripts.prompt_baseline_report                     # uses PROMPT_V2_CUTOVER
    python -m scripts.prompt_baseline_report --cutover 2026-07-13T00:00:00
    python -m scripts.prompt_baseline_report --cutover 2026-07-13 --window-days 14
    python -m scripts.prompt_baseline_report --cutover 2026-07-13 --db /path/to/trading_bot.db

The computation helpers (``summarize`` / ``split_by_cutover`` / ``build_report``) are pure
functions over lists of plain dict rows so they can be unit-tested without a database.
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Dict, List, Optional

# When the Opus 4.8 migration + prompt hardening phase 2 went live (2026-07-13,
# ~6:40 PM ET). The same evening, all light-task calls were also moved to Opus 4.8
# and strict tool use was enabled, so this single cutover covers the whole change set.
PROMPT_V2_CUTOVER = "2026-07-13T22:40:00+00:00"


# Columns we read from signal_decisions. Kept explicit so a schema change surfaces loudly.
_COLUMNS = [
    "decision_id",
    "outcome_type",
    "gate_id",
    "symbol",
    "direction",
    "confidence",
    "session",
    "mode",
    "judge_verdict",
    "timestamp",
    "hypothetical_result",
    "hypothetical_r",
    "mfe_r",
    "mae_r",
    "outcome_worker_status",
]


def parse_dt(value: Any) -> Optional[datetime]:
    """Parse a timestamp from the DB (ISO string or datetime) into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        # SQLite stores datetimes as e.g. "2026-07-13 12:34:56.789000".
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            # Fall back to a couple of common formats.
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    dt = None  # type: ignore[assignment]
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def split_by_cutover(
    rows: List[Dict[str, Any]],
    cutover: datetime,
    window_days: Optional[int] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Partition rows into 'before' (< cutover) and 'after' (>= cutover).

    When ``window_days`` is set, each side is limited to that many days from the cutover,
    giving a like-for-like comparison window on both sides.
    """
    lo = cutover - timedelta(days=window_days) if window_days else None
    hi = cutover + timedelta(days=window_days) if window_days else None

    before: List[Dict[str, Any]] = []
    after: List[Dict[str, Any]] = []
    for row in rows:
        ts = parse_dt(row.get("timestamp"))
        if ts is None:
            continue
        if ts < cutover:
            if lo is None or ts >= lo:
                before.append(row)
        else:
            if hi is None or ts <= hi:
                after.append(row)
    return {"before": before, "after": after}


def _dist(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    """Count occurrences of a field's values (missing/empty bucketed as '(none)')."""
    counter: Counter = Counter()
    for row in rows:
        value = row.get(field)
        key = str(value) if value not in (None, "") else "(none)"
        counter[key] += 1
    return dict(counter.most_common())


def _numeric_stats(rows: List[Dict[str, Any]], field: str) -> Dict[str, Optional[float]]:
    """Mean/median/min/max for a numeric field, ignoring None values."""
    values = [float(r[field]) for r in rows if r.get(field) is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the full distribution summary for one bucket of decision rows."""
    evaluated = [r for r in rows if r.get("outcome_worker_status") == "evaluated"]
    return {
        "count": len(rows),
        "direction": _dist(rows, "direction"),
        "outcome_type": _dist(rows, "outcome_type"),
        "judge_verdict": _dist(rows, "judge_verdict"),
        "mode": _dist(rows, "mode"),
        "session": _dist(rows, "session"),
        "top_gates": dict(list(_dist(rows, "gate_id").items())[:10]),
        "confidence": _numeric_stats(rows, "confidence"),
        "evaluated_count": len(evaluated),
        "hypothetical_result": _dist(evaluated, "hypothetical_result"),
        "hypothetical_r": _numeric_stats(evaluated, "hypothetical_r"),
        "mfe_r": _numeric_stats(evaluated, "mfe_r"),
        "mae_r": _numeric_stats(evaluated, "mae_r"),
    }


def build_report(
    rows: List[Dict[str, Any]],
    cutover: datetime,
    window_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Split rows at the cutover and summarize each side."""
    buckets = split_by_cutover(rows, cutover, window_days)
    return {
        "cutover": cutover.isoformat(),
        "window_days": window_days,
        "before": summarize(buckets["before"]),
        "after": summarize(buckets["after"]),
    }


def _pct(counts: Dict[str, int]) -> Dict[str, str]:
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: f"{v} ({100 * v / total:.0f}%)" for k, v in counts.items()}


def _format_dist_block(title: str, before: Dict[str, int], after: Dict[str, int]) -> List[str]:
    lines = [f"  {title}:"]
    keys = list(dict.fromkeys(list(before.keys()) + list(after.keys())))
    b_pct = _pct(before)
    a_pct = _pct(after)
    for key in keys:
        lines.append(f"    {key:<20} before={b_pct.get(key, '0'):<14} after={a_pct.get(key, '0')}")
    if not keys:
        lines.append("    (no data)")
    return lines


def _format_numeric(title: str, before: Dict[str, Any], after: Dict[str, Any]) -> str:
    def fmt(stats: Dict[str, Any]) -> str:
        if not stats or stats.get("mean") is None:
            return "n=0"
        return f"n={stats['n']} mean={stats['mean']} median={stats['median']} min={stats['min']} max={stats['max']}"
    return f"  {title}:\n    before: {fmt(before)}\n    after:  {fmt(after)}"


def format_report(report: Dict[str, Any]) -> str:
    """Render the report dict into a human-readable side-by-side comparison."""
    before = report["before"]
    after = report["after"]
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("PROMPT BASELINE REPORT — signal_decisions before vs after cutover")
    lines.append("=" * 72)
    lines.append(f"Cutover:      {report['cutover']}")
    if report.get("window_days"):
        lines.append(f"Window:       +/- {report['window_days']} days around cutover")
    lines.append(f"Decisions:    before={before['count']}    after={after['count']}")
    lines.append("")

    lines += _format_dist_block("Direction", before["direction"], after["direction"])
    lines.append("")
    lines += _format_dist_block("Outcome type", before["outcome_type"], after["outcome_type"])
    lines.append("")
    lines += _format_dist_block("Judge verdict", before["judge_verdict"], after["judge_verdict"])
    lines.append("")
    lines += _format_dist_block("Mode", before["mode"], after["mode"])
    lines.append("")
    lines += _format_dist_block("Top blocking gates", before["top_gates"], after["top_gates"])
    lines.append("")
    lines.append(_format_numeric("Confidence", before["confidence"], after["confidence"]))
    lines.append("")
    lines.append(
        f"  Evaluated outcomes: before={before['evaluated_count']}    after={after['evaluated_count']}"
    )
    lines += _format_dist_block(
        "Hypothetical result", before["hypothetical_result"], after["hypothetical_result"]
    )
    lines.append(_format_numeric("Hypothetical R", before["hypothetical_r"], after["hypothetical_r"]))
    lines.append(_format_numeric("MFE (R)", before["mfe_r"], after["mfe_r"]))
    lines.append(_format_numeric("MAE (R)", before["mae_r"], after["mae_r"]))
    lines.append("=" * 72)
    return "\n".join(lines)


def load_rows(db_path: str) -> List[Dict[str, Any]]:
    """Read all signal_decisions rows from the sqlite DB as plain dicts."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cols = ", ".join(_COLUMNS)
        cursor = conn.execute(f"SELECT {cols} FROM signal_decisions")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _default_db_path() -> str:
    """Resolve the canonical trading_bot.db path, falling back to project root."""
    try:
        from trading_bot.api.database import get_database_path

        return str(get_database_path())
    except Exception:
        from pathlib import Path

        return str(Path(__file__).resolve().parent.parent / "trading_bot.db")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cutover",
        default=PROMPT_V2_CUTOVER,
        help=(
            "ISO8601 timestamp when the new prompts went live "
            f"(default: recorded prompt-v2 cutover {PROMPT_V2_CUTOVER})."
        ),
    )
    parser.add_argument("--db", default=None, help="Path to trading_bot.db (defaults to project DB).")
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="Limit each side to N days around the cutover for a like-for-like window.",
    )
    args = parser.parse_args(argv)

    cutover = parse_dt(args.cutover)
    if cutover is None:
        parser.error(f"Could not parse --cutover value: {args.cutover!r}")

    db_path = args.db or _default_db_path()
    rows = load_rows(db_path)
    report = build_report(rows, cutover, args.window_days)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
