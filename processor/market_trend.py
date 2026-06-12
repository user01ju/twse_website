"""市場趨勢指標:
1. 收盤價 > 20MA 的股票比例
2. 創52週新高 - 創52週新低 淨值
"""
import logging
from datetime import date

from fetcher.price_cache import load_window

logger = logging.getLogger(__name__)

_MA_DAYS = 20
_WEEK52_DAYS = 252  # approx trading days in 52 weeks; we load up to 2x calendar days


def build(today: date) -> dict:
    """
    Returns:
    {
        "above_ma20": {"count": int, "total": int, "pct": float},
        "new_high_low": {"new_high": int, "new_low": int, "net": int},
        "history_days": int,  # actual number of cached days used
    }
    """
    # Load 252-day window (enough for both MA20 and 52-week)
    window = load_window(today, _WEEK52_DAYS)
    history_days = len(window)

    if history_days == 0:
        logger.warning("market_trend: no price cache available")
        return _empty(0)

    # Build per-code close/high/low arrays (oldest → newest)
    # We only track codes present in today's snapshot
    today_data = window[-1][1] if window else {}
    if not today_data:
        return _empty(history_days)

    # --- 20MA ---
    ma20_window = window[-_MA_DAYS:] if history_days >= _MA_DAYS else window
    # {code: [close, close, ...]} for codes in today
    closes_by_code: dict[str, list[float]] = {code: [] for code in today_data}
    for _, snap in ma20_window:
        for code in today_data:
            if code in snap:
                closes_by_code[code].append(snap[code]["c"])

    above_ma20 = 0
    total_ma20 = 0
    for code, closes in closes_by_code.items():
        if len(closes) < 2:
            continue  # not enough data to compute MA
        today_close = today_data[code]["c"]
        ma20 = sum(closes) / len(closes)
        total_ma20 += 1
        if today_close > ma20:
            above_ma20 += 1

    pct = round(above_ma20 / total_ma20 * 100, 1) if total_ma20 else 0.0

    # --- 52-week high / low ---
    # High = max(high) over window; Low = min(low) over window (excluding today for comparison)
    week52_window = window[:-1] if len(window) > 1 else []

    highs_by_code: dict[str, float] = {}
    lows_by_code: dict[str, float] = {}
    for _, snap in week52_window:
        for code, px in snap.items():
            h = px.get("h", 0) or px.get("c", 0)
            lo = px.get("l", 0) or px.get("c", 0)
            if h > 0:
                if code not in highs_by_code or h > highs_by_code[code]:
                    highs_by_code[code] = h
            if lo > 0:
                if code not in lows_by_code or lo < lows_by_code[code]:
                    lows_by_code[code] = lo

    new_high = 0
    new_low = 0
    for code, px in today_data.items():
        c = px["c"]
        h_today = px.get("h", c) or c
        lo_today = px.get("l", c) or c
        prev_high = highs_by_code.get(code)
        prev_low = lows_by_code.get(code)
        if prev_high and h_today > prev_high:
            new_high += 1
        if prev_low and lo_today < prev_low:
            new_low += 1

    return {
        "above_ma20": {
            "count": above_ma20,
            "total": total_ma20,
            "pct":   pct,
        },
        "new_high_low": {
            "new_high": new_high,
            "new_low":  new_low,
            "net":      new_high - new_low,
        },
        "history_days": history_days,
        "ma_days_used": len(ma20_window),
    }


def _empty(history_days: int) -> dict:
    return {
        "above_ma20":   {"count": 0, "total": 0, "pct": 0.0},
        "new_high_low": {"new_high": 0, "new_low": 0, "net": 0},
        "history_days": history_days,
        "ma_days_used": 0,
    }
