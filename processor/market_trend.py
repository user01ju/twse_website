"""市場趨勢指標:
1. 收盤價 > 20MA 的股票比例
2. 創52週新高 - 創52週新低 淨值
含近 30 個交易日的歷史序列（趨勢圖用）。

以「還原權息指數」計算（對齊 sector_gainer 的 cum 鏈）:
  ret[t] = close[t] / (除權息/減資/面額變更參考價 ?? 前一保留日收盤)
  cum[t] = cum[t-1] × ret[t]
20MA / 52週新高低全部在 cum 序列上判斷，除息跳空不再造成假跌破/假新低。
缺 ref 時 fallback 前收（該筆退化為未還原），孤立的限制外跳動（漏抓的
分割/減資）視為價值中性（ret=1），與 sector_gainer 同一護欄。
"""
import logging
from collections import deque
from datetime import date

from fetcher.price_cache import load_window, window_coverage
from fetcher import exrights

logger = logging.getLogger(__name__)

_MA_DAYS = 20
_WEEK52_DAYS = 252  # approx trading days in 52 weeks
_HALF_YEAR = 126    # 上市/資料滿半年才計新高低(對齊 sector_gainer)
_CHART_DAYS = 30    # trend chart length
_WILD = 1.12        # 單日 ±12% 限制外跳動 = 未知資本事件，視為價值中性
_MIN_COVERAGE = 90.0  # 窗口完整度低於此 → 標 degraded(完整 cache 約 93~97%)


class _Chain:
    """單一個股的還原指數狀態。"""
    __slots__ = ("pc", "cum", "last_idx", "cum20", "cum252")

    def __init__(self):
        self.pc = None        # 前一保留日原始收盤
        self.cum = 1.0        # 還原指數
        self.last_idx = None  # 前一保留日的 day index（gap 護欄用）
        self.cum20 = deque(maxlen=_MA_DAYS)
        self.cum252 = deque(maxlen=_WEEK52_DAYS)


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

    # 窗口完整度: 缺一大塊時 load_window 會往更早日期湊滿天數,指標看起來
    # 合理但錯(常見於未從 gh-pages 還原 cache 的本機重建) → 標 degraded。
    # 全窗口比率對「洞在遠端」比較鈍(缺 18 天也只掉到 88%),所以另外看近
    # 20 日 — MA20 就靠這段,洞在這裡的話比率會直接崩,是更利的偵測器。
    coverage = window_coverage(window, today)
    coverage["recent_pct"] = window_coverage(window[-_MA_DAYS:], today)["pct"]
    degraded = min(coverage["pct"], coverage["recent_pct"]) < _MIN_COVERAGE
    if degraded:
        logger.warning(
            f"market_trend: price cache 窗口只有 {coverage['got']}/{coverage['expected']} "
            f"個交易日 (全窗 {coverage['pct']}% / 近20日 {coverage['recent_pct']}%) → "
            f"指標標為 degraded。本機重建請先還原: "
            f"git archive origin/gh-pages | tar -x -C output/"
        )

    if history_days == 0:
        logger.warning("market_trend: no price cache available")
        return _empty(0, coverage)
    if not window[-1][1]:
        return _empty(history_days, coverage)

    refs = exrights.load_refs()
    if not refs:
        logger.warning("market_trend: no exrights data — falling back to raw closes (除息旺季指標會偏低)")

    chart_start = max(0, history_days - _CHART_DAYS)

    # Single pass oldest → newest,每股維護還原指數序列:
    #   cum20:  近 20 日 cum(20MA,需滿 20 日才納入)
    #   cum252: 近 252 日 cum(嚴格滾動 52 週窗口;需滿半年才計新高低)
    #   新高低一律以 cum 判斷,且含當日窗口(cum >= max → 新高 / cum <= min → 新低)
    chains: dict[str, _Chain] = {}

    daily = []  # (date, ma20_pct, ma20_above, ma20_total, new_high, new_low)

    for i, (d, snap) in enumerate(window):
        in_chart = i >= chart_start
        is_last = i == history_days - 1
        day_refs = refs.get(d.isoformat(), {})

        # 先把當日還原報酬併入每股鏈
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            ch = chains.get(code)
            if ch is None:
                ch = chains[code] = _Chain()
            base = day_refs.get(code) or ch.pc
            ret = c / base if base and base > 0 else 1.0
            # 跨越缺資料日最多允許 ±12% 複利兩次;再超出 = 未知資本事件,視為價值中性
            gap = 1 if ch.last_idx is None else min(i - ch.last_idx, 2)
            band = _WILD ** gap
            if ret > band or ret < 1 / band:
                ret = 1.0
            ch.cum *= ret
            ch.pc = c
            ch.last_idx = i
            ch.cum20.append(ch.cum)
            ch.cum252.append(ch.cum)

        if not (in_chart or is_last):
            continue

        above = total = new_high = new_low = 0
        for code, px in snap.items():
            if px.get("c", 0) <= 0:
                continue
            ch = chains.get(code)
            if ch is None:
                continue
            if len(ch.cum20) >= _MA_DAYS:
                total += 1
                if ch.cum > sum(ch.cum20) / _MA_DAYS:
                    above += 1
            if len(ch.cum252) >= _HALF_YEAR:
                if ch.cum >= max(ch.cum252):
                    new_high += 1
                elif ch.cum <= min(ch.cum252):
                    new_low += 1
        if total == 0:
            continue  # not enough history yet — a 0.0% point would mislead
        pct = round(above / total * 100, 1)
        daily.append((d, pct, above, total, new_high, new_low))

    if not daily:
        return _empty(history_days, coverage)

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
        "coverage":     coverage,
        "degraded":     degraded,
        "history": {
            "dates":    [d.strftime("%m/%d") for d, *_ in daily],
            "ma20_pct": [p for _, p, *_ in daily],
            "net":      [nh - nl for *_, nh, nl in daily],
        },
    }


def _empty(history_days: int, coverage: dict | None = None) -> dict:
    return {
        "above_ma20":   {"count": 0, "total": 0, "pct": 0.0},
        "new_high_low": {"new_high": 0, "new_low": 0, "net": 0},
        "history_days": history_days,
        "ma_days_used": 0,
        "coverage":     coverage or {"got": 0, "expected": 0, "pct": 0.0, "recent_pct": 0.0},
        "degraded":     True,
        "history": {"dates": [], "ma20_pct": [], "net": []},
    }
