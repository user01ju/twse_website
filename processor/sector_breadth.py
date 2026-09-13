"""族群廣度 by CMoney 子類股（第 8 區塊）。

取代舊的「漲跌幅前 100 子類股分析」：那張表只平均「進了 top100 的成分股」，
一個族群 30 檔只有 2 檔進榜就平均那 2 檔，是選樣偏差。這裡看**全部成分股**：
今日漲/跌/平家數、漲跌中位數、站上 20MA 比例、52 週新高/新低家數、近 5 / 20 日中位數。

價格一律走還原權息 cum 鏈（跟 market_trend / sector_flow 同一套 ±12% 護欄），
20MA 與 52 週高低都在 cum 上判斷，除息跳空不會變成假跌破。
"""
import logging
from collections import defaultdict, deque
from datetime import date
from statistics import median

from fetcher import exrights, price_cache
from processor.market_trend import _WILD, _HALF_YEAR, _MA_DAYS, _WEEK52_DAYS, _MIN_COVERAGE
from processor.sector_inst import _load_sector_map

logger = logging.getLogger(__name__)

_SHORT, _LONG = 5, 20
_LEADERS = 2            # 每個族群列幾檔領漲 / 領跌
_MIN_MEMBERS = 3        # 成分股少於這麼多檔不列（1~2 檔的「族群廣度」沒有意義）


class _Ch:
    __slots__ = ("pc", "cum", "last_idx", "cum20", "cum252")

    def __init__(self):
        self.pc = None
        self.cum = 1.0
        self.last_idx = None
        self.cum20 = deque(maxlen=_MA_DAYS)
        self.cum252 = deque(maxlen=_WEEK52_DAYS)


def _load_names() -> dict[str, str]:
    """price cache 沒有名稱，從 cmoney_raw.json 撿（跟子類股同一份快照）。"""
    import json
    from pathlib import Path
    p = Path(__file__).parent.parent / "cmoney_raw.json"
    if not p.exists():
        return {}
    return {str(st["id"]).strip(): st["name"]
            for cat in json.loads(p.read_text(encoding="utf-8"))
            for st in cat.get("stocks", [])}


def build(today: date) -> dict:
    window = price_cache.load_window(today, _WEEK52_DAYS)
    n = len(window)
    if n < 2:
        return {"rows": [], "degraded": True, "coverage": {}, "date": today.isoformat()}
    coverage = price_cache.window_coverage(window, today)
    coverage["recent_pct"] = price_cache.window_coverage(window[-_MA_DAYS:], today)["pct"]
    degraded = min(coverage["pct"], coverage["recent_pct"]) < _MIN_COVERAGE

    refs = exrights.load_refs()
    chains: dict[str, _Ch] = {}
    starts = {k: max(0, n - k - 1) for k in (1, _SHORT, _LONG)}
    cum_at: dict[int, dict[str, float]] = {}

    for i, (d, snap) in enumerate(window):
        day_refs = refs.get(d.isoformat(), {})
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            ch = chains.get(code)
            if ch is None:
                ch = chains[code] = _Ch()
            base = day_refs.get(code) or ch.pc
            ret = c / base if base and base > 0 else 1.0
            gap = 1 if ch.last_idx is None else min(i - ch.last_idx, 2)
            band = _WILD ** gap
            if ret > band or ret < 1 / band:
                ret = 1.0
            ch.cum *= ret
            ch.pc, ch.last_idx = c, i
            ch.cum20.append(ch.cum)
            ch.cum252.append(ch.cum)
        for k, s in starts.items():
            if i == s:
                cum_at[k] = {code: ch.cum for code, ch in chains.items()}

    last_date, last_snap = window[-1]
    code_to_sector, code_to_parent = _load_sector_map()
    names = _load_names()

    members: dict[str, list[dict]] = defaultdict(list)
    for code, px in last_snap.items():
        if px.get("c", 0) <= 0 or code not in chains:
            continue
        ch = chains[code]
        m = {"code": code, "name": names.get(code, code)}
        for k in (1, _SHORT, _LONG):
            base = cum_at.get(k, {}).get(code)
            m[f"r{k}"] = (ch.cum / base - 1) * 100 if base else None
        m["above"] = (ch.cum > sum(ch.cum20) / _MA_DAYS) if len(ch.cum20) >= _MA_DAYS else None
        if len(ch.cum252) >= _HALF_YEAR:
            m["nh"] = ch.cum >= max(ch.cum252)
            m["nl"] = ch.cum <= min(ch.cum252)
        else:
            m["nh"] = m["nl"] = False
        members[code_to_sector.get(code) or "其他"].append(m)

    rows = []
    for sec, ms in members.items():
        r1 = [m["r1"] for m in ms if m["r1"] is not None]
        if len(r1) < _MIN_MEMBERS:
            continue
        up = sum(1 for v in r1 if v > 0)
        down = sum(1 for v in r1 if v < 0)
        ma = [m["above"] for m in ms if m["above"] is not None]
        r5 = [m["r5"] for m in ms if m["r5"] is not None]
        r20 = [m["r20"] for m in ms if m["r20"] is not None]
        ranked = sorted((m for m in ms if m["r1"] is not None), key=lambda m: m["r1"], reverse=True)
        lead = lambda seq: [{"code": m["code"], "name": m["name"], "pct": round(m["r1"], 2)} for m in seq]
        parent = next((code_to_parent.get(m["code"]) for m in ms if code_to_parent.get(m["code"])), "")
        rows.append({
            "sector":  sec,
            "parent":  parent,
            "n":       len(r1),
            "up":      up,
            "down":    down,
            "flat":    len(r1) - up - down,
            "breadth": round((up - down) / len(r1) * 100),          # 漲跌家數差佔比，-100~+100
            "med1":    round(median(r1), 2),
            "med5":    round(median(r5), 2) if r5 else None,
            "med20":   round(median(r20), 2) if r20 else None,
            "ma20_pct": round(sum(ma) / len(ma) * 100) if ma else None,
            "ma20_n":  len(ma),
            "nh":      sum(1 for m in ms if m["nh"]),
            "nl":      sum(1 for m in ms if m["nl"]),
            "leaders": lead(ranked[:_LEADERS]),
            "laggards": lead(ranked[_LEADERS:][-_LEADERS:][::-1]),   # 跟領漲不重疊
        })
    rows.sort(key=lambda r: r["med1"], reverse=True)

    total_n = sum(r["n"] for r in rows)
    return {
        "date":     last_date.isoformat(),
        "rows":     rows,
        "coverage": coverage,
        "degraded": degraded,
        "ret_days": {k: n - 1 - s for k, s in starts.items()},
        "market": {                     # 對照用：全市場（列入表的成分股）
            "n": total_n,
            "up": sum(r["up"] for r in rows),
            "down": sum(r["down"] for r in rows),
            "med1": round(median(m["r1"] for ms in members.values() for m in ms if m["r1"] is not None), 2) if total_n else None,
            "ma20_pct": round(sum(1 for ms in members.values() for m in ms if m["above"]) /
                              max(1, sum(1 for ms in members.values() for m in ms if m["above"] is not None)) * 100),
        },
    }
