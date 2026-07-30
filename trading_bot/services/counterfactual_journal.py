"""
Counterfactual journal — the feedback loop for decision gates.

Every terminal non-trade decision (Claude NO_TRADE, gate reject, judge
reject, pre-Claude skip) is persisted as a compact JSON-lines record.
Records that carried a full trade plan (direction + entry/SL/TP) are later
scored against realized price action: would the blocked trade have hit TP
first (the gate cost us R) or SL first (the gate saved us R)?

After a couple of weeks the per-gate saved-R / missed-R tally makes gate
tuning a data question instead of a debate.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PATH = "data/counterfactuals.jsonl"

# Records younger than this keep accumulating price history before scoring.
MIN_SCORE_AGE_HOURS = 4
# Records older than this with no TP/SL touch are finalized as "neither".
FINALIZE_AGE_HOURS = 24

_SCORE_TIMEFRAME = "M5"
_MAX_SCORE_BARS = 1000


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CounterfactualJournal:
    """JSON-lines journal of blocked/rejected trade decisions."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        symbol: str,
        gate_id: str,
        outcome_type: str = "",
        direction: str = "",
        confidence: float = 0.0,
        market_price: Optional[float] = None,
        entry: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reason: str = "",
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        ts = _as_utc(timestamp or datetime.now(timezone.utc))
        rec: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": ts.isoformat(),
            "symbol": symbol,
            "gate_id": gate_id or "unknown",
            "outcome_type": outcome_type,
            "direction": direction or "",
            "confidence": float(confidence or 0.0),
            "market_price": market_price,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "reason": (reason or "")[:300],
            "outcome": None,
            "outcome_r": None,
            "finalized": False,
        }
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            logger.warning(f"[COUNTERFACTUAL] Could not persist record: {exc}")
        return rec

    # ------------------------------------------------------------------
    # Loading / rewriting
    # ------------------------------------------------------------------

    def load_records(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.warning(f"[COUNTERFACTUAL] Could not read journal: {exc}")
        return records

    def _write_all(self, records: List[Dict[str, Any]]) -> None:
        tmp_path = self.path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec) + "\n")
            os.replace(tmp_path, self.path)
        except OSError as exc:
            logger.warning(f"[COUNTERFACTUAL] Could not rewrite journal: {exc}")

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _scoreable(rec: Dict[str, Any]) -> bool:
        return (
            not rec.get("finalized")
            and rec.get("direction") in ("long", "short")
            and rec.get("entry")
            and rec.get("sl")
            and rec.get("tp")
        )

    @staticmethod
    def _score_against_bars(rec: Dict[str, Any], df) -> Optional[str]:
        """Walk bars after the block; return 'tp_first', 'sl_first', or None.

        A bar touching both SL and TP counts as sl_first (conservative:
        assume the worst path for the hypothetical trade).
        """
        direction = rec["direction"]
        sl = float(rec["sl"])
        tp = float(rec["tp"])
        for _, bar in df.iterrows():
            high = float(bar["high"])
            low = float(bar["low"])
            if direction == "long":
                sl_hit = low <= sl
                tp_hit = high >= tp
            else:
                sl_hit = high >= sl
                tp_hit = low <= tp
            if sl_hit:
                return "sl_first"
            if tp_hit:
                return "tp_first"
        return None

    async def score_pending(
        self,
        fetch_ohlcv: Callable,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        """
        Score unfinalized records older than MIN_SCORE_AGE_HOURS.

        fetch_ohlcv(symbol, timeframe, count) must return an OHLCV DataFrame
        indexed by bar time (the DataFetcher signature).
        Returns the number of records whose outcome changed.
        """
        now = _as_utc(now or datetime.now(timezone.utc))
        records = self.load_records()
        changed = 0
        df_cache: Dict[str, Any] = {}

        for rec in records:
            if not self._scoreable(rec):
                continue
            try:
                blocked_at = _as_utc(datetime.fromisoformat(rec["timestamp"]))
            except (KeyError, ValueError):
                continue
            age_hours = (now - blocked_at).total_seconds() / 3600
            if age_hours < MIN_SCORE_AGE_HOURS:
                continue

            symbol = rec["symbol"]
            try:
                if symbol not in df_cache:
                    bars_needed = min(_MAX_SCORE_BARS, int(age_hours * 12) + 12)
                    df_cache[symbol] = await fetch_ohlcv(
                        symbol, _SCORE_TIMEFRAME, bars_needed
                    )
                df = df_cache[symbol]
            except Exception as exc:
                logger.debug(f"[COUNTERFACTUAL] OHLCV fetch failed for {symbol}: {exc}")
                continue
            if df is None or getattr(df, "empty", True):
                continue

            # Only bars after the block matter (index may be tz-naive UTC).
            idx = df.index
            cutoff = blocked_at.replace(tzinfo=None) if idx.tz is None else blocked_at
            after = df[idx > cutoff] if len(df) else df
            outcome = self._score_against_bars(rec, after)

            if outcome is None:
                if age_hours >= FINALIZE_AGE_HOURS:
                    rec["outcome"] = "neither"
                    rec["outcome_r"] = 0.0
                    rec["finalized"] = True
                    changed += 1
                continue

            entry = float(rec["entry"])
            sl_dist = abs(entry - float(rec["sl"]))
            tp_dist = abs(float(rec["tp"]) - entry)
            rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0
            rec["outcome"] = outcome
            rec["outcome_r"] = rr if outcome == "tp_first" else -1.0
            rec["finalized"] = True
            changed += 1

        if changed:
            self._write_all(records)
            logger.info(f"[COUNTERFACTUAL] Scored {changed} blocked-trade record(s)")
        return changed

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Per-gate tally of what the gates saved vs what they cost."""
        gates: Dict[str, Dict[str, Any]] = {}
        records = self.load_records()
        for rec in records:
            gate = rec.get("gate_id") or "unknown"
            g = gates.setdefault(gate, {
                "count": 0, "tp_first": 0, "sl_first": 0, "neither": 0,
                "pending": 0, "missed_r": 0.0, "saved_r": 0.0,
            })
            g["count"] += 1
            outcome = rec.get("outcome")
            if outcome == "tp_first":
                g["tp_first"] += 1
                g["missed_r"] += float(rec.get("outcome_r") or 0.0)
            elif outcome == "sl_first":
                g["sl_first"] += 1
                g["saved_r"] += -float(rec.get("outcome_r") or 0.0)
            elif outcome == "neither":
                g["neither"] += 1
            else:
                g["pending"] += 1
        for g in gates.values():
            g["net_saved_r"] = g["saved_r"] - g["missed_r"]
        return {
            "total_records": len(records),
            "gates": gates,
        }


_default_journal: Optional[CounterfactualJournal] = None


def get_counterfactual_journal() -> CounterfactualJournal:
    """Process-wide journal instance (bot and API share the same file)."""
    global _default_journal
    if _default_journal is None:
        _default_journal = CounterfactualJournal()
    return _default_journal
