"""近期法人資金流向 by CMoney 子類股（第 7 區塊）。

與第 6 區塊的差別：6 是「今日快照 + 個股明細」，這裡是**時間序列** —
5 日 / 20 日累計、連續流入流出天數、加速度、以及同期子類股漲跌幅。

資料來自 output/data/inst_flow/ 的每日快取（fetcher/inst_flow_cache），
所以跟 market_trend 一樣：快取缺一大塊時窗口會安靜地往更早日期湊，
數字看起來合理但是錯的 → 用 coverage 標 degraded。

近 5 日漲跌% 走還原權息（沿用 market_trend 的 cum 鏈與 ±12% 護欄），
否則除息股會被算成假跌，成分股少的子類股一檔就歪掉。
"""
import logging
from collections import defaultdict
from datetime import date

from fetcher import exrights, inst_flow_cache, price_cache
from processor.market_trend import _WILD, _Chain
from processor.sector_inst import _load_sector_map

logger = logging.getLogger(__name__)

_DAYS      = 20     # 長窗
_SHORT     = 5      # 短窗
_MIN_DAYS  = 10     # 少於這麼多天不判加速（樣本太少）
_MIN_COVERAGE = 90.0
_FLAT_YI   = 0.01   # 小於 100 萬視為沒動作（連續天數判斷用）
_ACCEL_MIN_AVG = 1.0    # 20 日日均低於 1 億不判加速：淨額是正負相抵後的殘值，
                        # 分母一小，倍數就會噴出 20x 這種純噪音（實測 電機 -10 億/20 日 → 22.21x）
_ACCEL_HOT     = 1.5
_ACCEL_COLD    = 0.67

_TABS = (("c", "三大法人"), ("f", "外資"), ("t", "投信"), ("d", "自營商"))


def _streak(series: list[float]) -> int:
    """從最近一日往回數同向天數。回傳帶正負號（+3 = 連 3 天買超），0 = 當日沒動作。"""
    if not series or abs(series[-1]) < _FLAT_YI:
        return 0
    sign = 1 if series[-1] > 0 else -1
    n = 0
    for v in reversed(series):
        if abs(v) < _FLAT_YI or (1 if v > 0 else -1) != sign:
            break
        n += 1
    return n * sign


def _accel(series: list[float]) -> tuple[float | None, str]:
    """短窗日均 / 長窗日均 → (倍數, 標籤)。分母太小或樣本不足回 (None, '')。"""
    if len(series) < _MIN_DAYS:
        return None, ""
    short = series[-_SHORT:]
    avg_s = sum(short) / len(short)
    avg_l = sum(series) / len(series)
    if abs(avg_l) < _ACCEL_MIN_AVG:
        return None, ""
    if avg_s * avg_l < 0:
        return None, "翻轉"
    ratio = avg_s / avg_l
    if ratio >= _ACCEL_HOT:
        return round(ratio, 2), "加速"
    if ratio <= _ACCEL_COLD:
        return round(ratio, 2), "減速"
    return round(ratio, 2), "持平"


def _sector_returns(today: date, sector_of: dict[str, str]) -> tuple[dict[str, float], int]:
    """子類股近 N 日還原報酬（成分股等權平均，%）。回傳 (map, 實際天數)。"""
    window = price_cache.load_window(today, _SHORT + 1)
    if len(window) < 2:
        return {}, 0

    refs = exrights.load_refs()
    chains: dict[str, _Chain] = {}
    for i, (d, snap) in enumerate(window):
        day_refs = refs.get(d.isoformat(), {})
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            ch = chains.get(code)
            if ch is None:
                ch = chains[code] = _Chain()
            base = day_refs.get(code) or ch.pc
            ret  = c / base if base and base > 0 else 1.0
            gap  = 1 if ch.last_idx is None else min(i - ch.last_idx, 2)
            band = _WILD ** gap
            if ret > band or ret < 1 / band:
                ret = 1.0          # 未知資本事件 → 視為價值中性
            ch.cum *= ret
            ch.pc, ch.last_idx = c, i

    first_snap = window[0][1]
    buckets: dict[str, list[float]] = defaultdict(list)
    for code, ch in chains.items():
        if code not in first_snap:
            continue               # 期初沒報價 → 算不出區間報酬（新股/停牌）
        buckets[sector_of.get(code) or "其他"].append((ch.cum - 1) * 100)

    return ({s: round(sum(v) / len(v), 2) for s, v in buckets.items()},
            len(window) - 1)


def build(today: date) -> dict:
    window = inst_flow_cache.load_window(today, _DAYS)
    coverage = price_cache.window_coverage(window, today)
    days = len(window)

    if days < 2:
        logger.warning("sector_flow: inst_flow 快取不足（%d 天）— 先跑 backfill_inst.py", days)
        return {"days": days, "ret_days": 0, "coverage": coverage,
                "degraded": True, "tabs": {}, "start": None, "end": None}

    degraded = coverage["pct"] < _MIN_COVERAGE or days < _MIN_DAYS
    if degraded:
        logger.warning(
            f"sector_flow: 窗口 {coverage['got']}/{coverage['expected']} 個交易日 "
            f"({coverage['pct']}%) → 標為 degraded。本機重建請先還原： "
            f"git archive origin/gh-pages | tar -x -C output/"
        )

    code_to_sector, code_to_parent = _load_sector_map()
    sector_parent = {}
    for code, sec in code_to_sector.items():
        sector_parent.setdefault(sec, code_to_parent.get(code, ""))

    rets, ret_days = _sector_returns(today, code_to_sector)

    sectors = sorted({s for _, snap in window for s in snap})
    tabs = {}
    for key, label in _TABS:
        rows = []
        for sec in sectors:
            series = [snap.get(sec, {}).get(key, 0.0) for _, snap in window]
            net5   = sum(series[-_SHORT:])
            net20  = sum(series)
            if abs(net5) < 0.1 and abs(net20) < 0.1:
                continue                       # 整段幾乎沒進出，不佔版面
            ratio, tag = _accel(series)
            rows.append({
                "sector": sec,
                "parent": sector_parent.get(sec, ""),
                "net5":   round(net5,  2),
                "net20":  round(net20, 2),
                "streak": _streak(series),
                "accel":  ratio,
                "accel_tag": tag,
                "ret5":   rets.get(sec),
                "n":      window[-1][1].get(sec, {}).get("n", 0),
            })
        rows.sort(key=lambda r: r["net5"], reverse=True)
        tabs[key] = rows

    return {
        "days":     days,
        "ret_days": ret_days,
        "coverage": coverage,
        "degraded": degraded,
        "short":    _SHORT,
        "long":     _DAYS,
        "tabs":     tabs,
        "start":    window[0][0].isoformat(),
        "end":      window[-1][0].isoformat(),
    }
