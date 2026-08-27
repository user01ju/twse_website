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

from fetcher import exrights, inst_flow_cache, price_cache, shares
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
# 個股層的加速度門檻要低一階：1 億/日 對個股等於只有前 12% 判得到
# （20 日日均絕對值 p90 = 1.26 億、p75 = 0.19 億）。
_ACCEL_MIN_AVG_STOCK = 0.3
# 個股表每個法人別取買超前 N + 賣超前 N（全列 1162 檔 × 4 頁簽會讓 HTML 再翻倍）
_STOCK_TABLE_TOP = 50

_TABS = (("c", "三大法人"), ("f", "外資"), ("t", "投信"), ("d", "自營商"))
# 個股層快取一列是 [名稱, c, f, t, d]；載入時名稱另外收，序列只留四個數字，
# 所以這裡的位置是 0-3（不是快取的 1-4）。
_SER_IDX = {"c": 0, "f": 1, "t": 2, "d": 3}

# 展開列：只列近 5 日淨額夠大的，且每個子類股最多這麼多檔。純粹是版面與 HTML
# 體積的取捨 —— 門檻 0.1 億會排出 1634 列、0.5 億剩 1044 列而金額只掉 1.7%。
# 但小型子類股整段都在門檻以下時會變成「不能展開」，所以保底給前 _STOCK_MIN_SHOW 名。
_STOCK_SHOW_YI  = 0.5
_STOCK_MIN_SHOW = 3
_STOCK_CAP      = 15


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


def _accel(series: list[float], min_avg: float = _ACCEL_MIN_AVG) -> tuple[float | None, str]:
    """短窗日均 / 長窗日均 → (倍數, 標籤)。分母太小或樣本不足回 (None, '')。"""
    if len(series) < _MIN_DAYS:
        return None, ""
    short = series[-_SHORT:]
    avg_s = sum(short) / len(short)
    avg_l = sum(series) / len(series)
    if abs(avg_l) < min_avg:
        return None, ""
    if avg_s * avg_l < 0:
        return None, "翻轉"
    ratio = avg_s / avg_l
    if ratio >= _ACCEL_HOT:
        return round(ratio, 2), "加速"
    if ratio <= _ACCEL_COLD:
        return round(ratio, 2), "減速"
    return round(ratio, 2), "持平"


def _sector_returns(today: date, sector_of: dict[str, str]
                   ) -> tuple[dict[str, float], dict[str, float], int]:
    """近 N 日還原報酬（%）。回傳 (子類股等權平均, 個股, 實際天數)。"""
    window = price_cache.load_window(today, _SHORT + 1)
    if len(window) < 2:
        return {}, {}, 0

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
    per_code: dict[str, float] = {}
    for code, ch in chains.items():
        if code not in first_snap:
            continue               # 期初沒報價 → 算不出區間報酬（新股/停牌）
        ret = (ch.cum - 1) * 100
        per_code[code] = round(ret, 2)
        buckets[sector_of.get(code) or "其他"].append(ret)

    return ({s: round(sum(v) / len(v), 2) for s, v in buckets.items()},
            per_code, len(window) - 1)


def build(today: date) -> dict:
    window = inst_flow_cache.load_window(today, _DAYS)
    coverage = price_cache.window_coverage(window, today)
    days = len(window)

    if days < 2:
        logger.warning("sector_flow: inst_flow 快取不足（%d 天）— 先跑 backfill_inst.py", days)
        return {"days": days, "ret_days": 0, "coverage": coverage, "degraded": True,
                "tabs": {}, "stock_tabs": {}, "start": None, "end": None}

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

    rets, code_rets, ret_days = _sector_returns(today, code_to_sector)

    # 個股層（展開列用）。日期序列跟母表同一組，缺檔的日子給 {}。
    stock_window = inst_flow_cache.load_window(today, _DAYS, stocks=True)
    names: dict[str, str] = {}
    series_by_code: dict[str, list[list[float]]] = {}
    n_days = len(stock_window)
    for i, (_d, snap) in enumerate(stock_window):
        for code, row in snap.items():
            names.setdefault(code, row[0])
            ser = series_by_code.get(code)
            if ser is None:
                ser = series_by_code[code] = [[0.0] * 4 for _ in range(n_days)]
            ser[i] = [row[1], row[2], row[3], row[4]]

    codes_by_sector: dict[str, list[str]] = defaultdict(list)
    for code in series_by_code:
        codes_by_sector[code_to_sector.get(code) or "其他"].append(code)

    # 市值 = 報告日收盤 × 發行股數。股數只有最新快照，整個窗口共用今天的值
    # （股數以月為尺度變動、窗口只有 20 天），所以市值比是近似值。
    last_px = {c: v.get("c", 0) for c, v in price_cache.load(window[-1][0]).items()}
    share_map = shares.load()
    mcaps = {c: last_px[c] * share_map[c] / 1e8
             for c in series_by_code
             if share_map.get(c) and last_px.get(c)}

    # 子類股市值用**全部成分股**加總，不是只算窗口內有流向的那幾檔 —— 分母要是
    # 整個族群的規模，否則冷門類股會因為分母縮水而比率虛高。
    sector_mcap: dict[str, float] = defaultdict(float)
    for code, sh in share_map.items():
        px = last_px.get(code)
        if px:
            sector_mcap[code_to_sector.get(code) or "其他"] += px * sh / 1e8

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

            ki = _SER_IDX[key]
            members = []
            for code in codes_by_sector.get(sec, ()):
                ss = [day[ki] for day in series_by_code[code]]
                s5 = sum(ss[-_SHORT:])
                if s5 == 0:
                    continue
                members.append({
                    "code":   code,
                    "name":   names.get(code, ""),
                    "net5":   round(s5, 2),
                    "net20":  round(sum(ss), 2),
                    "streak": _streak(ss),
                    "ret5":   code_rets.get(code),
                })
            picks = [m for m in members if abs(m["net5"]) >= _STOCK_SHOW_YI]
            if len(picks) < _STOCK_MIN_SHOW:      # 小類股保底：至少看得到前幾名
                picks = sorted(members, key=lambda x: abs(x["net5"]),
                               reverse=True)[:_STOCK_MIN_SHOW]
            picks.sort(key=lambda x: x["net5"], reverse=True)
            # 買超側取頭、賣超側取尾（中間那些本來就不是重點）
            stocks = (picks if len(picks) <= _STOCK_CAP
                      else picks[:_STOCK_CAP // 2] + picks[-(_STOCK_CAP - _STOCK_CAP // 2):])

            smc = sector_mcap.get(sec)
            rows.append({
                "sector": sec,
                "parent": sector_parent.get(sec, ""),
                "mcap":   round(smc) if smc else None,
                "net5_pct": round(net5 / smc * 100, 2) if smc else None,
                "net5":   round(net5,  2),
                "net20":  round(net20, 2),
                "streak": _streak(series),
                "accel":  ratio,
                "accel_tag": tag,
                "ret5":   rets.get(sec),
                "n":      window[-1][1].get(sec, {}).get("n", 0),
                "stocks": stocks,
                "hidden": max(0, len(picks) - len(stocks)),
            })
        rows.sort(key=lambda r: r["net5"], reverse=True)
        tabs[key] = rows

    # 個股表：跟母表同一份 series，只是不分子類股。子類股內成分差異大時，
    # 分組本身就是雜訊 —— 這裡讓個股自己排隊。
    stock_tabs = {}
    for key, _label in _TABS:
        ki = _SER_IDX[key]
        srows = []
        for code, ser in series_by_code.items():
            ss = [day[ki] for day in ser]
            s5 = sum(ss[-_SHORT:])
            if s5 == 0:
                continue
            ratio, tag = _accel(ss, _ACCEL_MIN_AVG_STOCK)
            mcap = mcaps.get(code)
            srows.append({
                "code":   code,
                "name":   names.get(code, ""),
                "sector": code_to_sector.get(code) or "其他",
                "mcap":   round(mcap) if mcap else None,
                "net5_pct": round(s5 / mcap * 100, 2) if mcap else None,
                "net5":   round(s5, 2),
                "net20":  round(sum(ss), 2),
                "streak": _streak(ss),
                "accel":  ratio,
                "accel_tag": tag,
                "ret5":   code_rets.get(code),
            })
        srows.sort(key=lambda r: r["net5"], reverse=True)
        stock_tabs[key] = (srows if len(srows) <= _STOCK_TABLE_TOP * 2
                           else srows[:_STOCK_TABLE_TOP] + srows[-_STOCK_TABLE_TOP:])

    return {
        "days":     days,
        "ret_days": ret_days,
        "stock_tabs": stock_tabs,
        "stock_top":  _STOCK_TABLE_TOP,
        "stock_universe": len(series_by_code),
        "coverage": coverage,
        "degraded": degraded,
        "short":    _SHORT,
        "long":     _DAYS,
        "stock_min": _STOCK_SHOW_YI,
        "stock_cap": _STOCK_CAP,
        "tabs":     tabs,
        "start":    window[0][0].isoformat(),
        "end":      window[-1][0].isoformat(),
    }
