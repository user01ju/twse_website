"""市場趨勢指標:
1. 收盤價 > 20MA 的股票比例
2. 創52週新高 - 創52週新低 淨值
含近 30 個交易日的歷史序列（趨勢圖用）。
"""
import logging
from collections import deque
from datetime import date

from fetcher.price_cache import load_window

logger = logging.getLogger(__name__)

_MA_DAYS = 20
_WEEK52_DAYS = 252  # approx trading days in 52 weeks
_CHART_DAYS = 30    # trend chart length


def build(today: date) -> dict:
    """
    Returns:
    {
        "above_ma20": {"count": int, "total": int, "pct": float},
        "new_high_low": {"new_high": int, "new_low": int, "net": int},
        "history_days": int,
        "ma_days_used": int,
        "history": {"dates": [...], "ma20_pct": [...], "net": [...]},
    }
    """
    # Extra lookback so the earliest chart point still has MA/52w context
    window = load_window(today, _WEEK52_DAYS + _CHART_DAYS)
    history_days = len(window)

    if history_days == 0:
        logger.warning("market_trend: no price cache available")
        return _empty(0)
    if not window[-1][1]:
        return _empty(history_days)

    chart_start = max(0, history_days - _CHART_DAYS)

    # Single pass oldest → newest, maintaining running state per code:
    #   closes: deque of last 20 closes (incl. current day when computing MA)
    #   run_high / run_low: 52w running extremes (excl. current day at compare time)
    closes: dict[str, deque] = {}
    run_high: dict[str, float] = {}
    run_low: dict[str, float] = {}

    daily = []  # (date, ma20_pct, ma20_above, ma20_total, new_high, new_low)

    for i, (d, snap) in enumerate(window):
        in_chart = i >= chart_start
        is_last = i == history_days - 1

        new_high = new_low = 0
        if in_chart or is_last:
            # compare today's extremes vs running 52w extremes (excluding today)
            for code, px in snap.items():
                c = px.get("c", 0)
                h = px.get("h", 0) or c
                lo = px.get("l", 0) or c
                ph = run_high.get(code)
                pl = run_low.get(code)
                if ph and h > ph:
                    new_high += 1
                if pl and lo < pl:
                    new_low += 1

        # update running state with current day
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            h = px.get("h", 0) or c
            lo = px.get("l", 0) or c
            dq = closes.get(code)
            if dq is None:
                dq = closes[code] = deque(maxlen=_MA_DAYS)
            dq.append(c)
            if h > 0 and (code not in run_high or h > run_high[code]):
                run_high[code] = h
            if lo > 0 and (code not in run_low or lo < run_low[code]):
                run_low[code] = lo

        if in_chart or is_last:
            above = total = 0
            for code, px in snap.items():
                dq = closes.get(code)
                if not dq or len(dq) < 2:
                    continue
                total += 1
                if px.get("c", 0) > sum(dq) / len(dq):
                    above += 1
            if total == 0:
                continue  # not enough history yet — a 0.0% point would mislead
            pct = round(above / total * 100, 1)
            daily.append((d, pct, above, total, new_high, new_low))

    if not daily:
        return _empty(history_days)

    last_d, last_pct, last_above, last_total, last_nh, last_nl = daily[-1]

    return {
        "above_ma20": {
            "count": last_above,
            "total": last_total,
            "pct":   last_pct,
        },
        "new_high_low": {
            "new_high": last_nh,
            "new_low":  last_nl,
            "net":      last_nh - last_nl,
        },
        "history_days": history_days,
        "ma_days_used": min(history_days, _MA_DAYS),
        "history": {
            "dates":    [d.strftime("%m/%d") for d, *_ in daily],
            "ma20_pct": [p for _, p, *_ in daily],
            "net":      [nh - nl for *_, nh, nl in daily],
        },
    }


def _empty(history_days: int) -> dict:
    return {
        "above_ma20":   {"count": 0, "total": 0, "pct": 0.0},
        "new_high_low": {"new_high": 0, "new_low": 0, "net": 0},
        "history_days": history_days,
        "ma_days_used": 0,
        "history": {"dates": [], "ma20_pct": [], "net": []},
    }
