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
_HALF_YEAR = 126    # 上市/資料滿半年才計新高低(對齊 sector_gainer)
_CHART_DAYS = 30    # trend chart length

# NOTE: 仍使用「未還原」收盤價。除權息旺季(7–9 月)會因除息跳空造成假跌破
# 20MA / 假新低,使 ma20_pct 與 net 系統性偏低。完整對齊 sector_gainer 需引入
# 除權息參考價做還原鏈(見 sector_gainer 的 fetch_exrights + cum 邏輯)。


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

    # Single pass oldest → newest,每股維護收盤序列(對齊 sector_gainer):
    #   d20:  近 20 日收盤(20MA,需滿 20 日才納入)
    #   d252: 近 252 日收盤(嚴格滾動 52 週窗口;需滿半年才計新高低)
    #   新高低一律以「收盤」判斷,且含當日窗口(c >= max → 新高 / c <= min → 新低)
    closes20: dict[str, deque] = {}
    closes252: dict[str, deque] = {}

    daily = []  # (date, ma20_pct, ma20_above, ma20_total, new_high, new_low)

    for i, (d, snap) in enumerate(window):
        in_chart = i >= chart_start
        is_last = i == history_days - 1

        # 先把當日收盤併入每股序列
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            d20 = closes20.get(code)
            if d20 is None:
                d20 = closes20[code] = deque(maxlen=_MA_DAYS)
            d20.append(c)
            d252 = closes252.get(code)
            if d252 is None:
                d252 = closes252[code] = deque(maxlen=_WEEK52_DAYS)
            d252.append(c)

        if not (in_chart or is_last):
            continue

        above = total = new_high = new_low = 0
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            d20 = closes20.get(code)
            if d20 and len(d20) >= _MA_DAYS:
                total += 1
                if c > sum(d20) / _MA_DAYS:
                    above += 1
            d252 = closes252.get(code)
            if d252 and len(d252) >= _HALF_YEAR:
                if c >= max(d252):
                    new_high += 1
                elif c <= min(d252):
                    new_low += 1
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
