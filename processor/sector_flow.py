"""法人資金流向 by CMoney 子類股（第 5 區塊）＋個股表（第 6 區塊）。

今日 / 5 日 / 20 日三個尺度放同一列：今日淨額（含 z 分數與翻向首日）、5 / 20 日累計、
連續流入流出天數、加速度、以及同期漲跌幅。原本的單日買賣超區塊（舊 5/6）2026-09-13 併進來。

資料來自 output/data/inst_flow/ 的每日快取（fetcher/inst_flow_cache），
所以跟 market_trend 一樣：快取缺一大塊時窗口會安靜地往更早日期湊，
數字看起來合理但是錯的 → 用 coverage 標 degraded。

近 5 / 20 日漲跌% 走還原權息（沿用 market_trend 的 cum 鏈與 ±12% 護欄），
否則除息股會被算成假跌，成分股少的子類股一檔就歪掉。

型態標籤把 (5 日流向, 20 日流向) × (5 日漲跌, 20 日漲跌) 四維壓成一個詞，
見 _pattern()。
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
_WEEK52 = 252          # 52 週位階的窗口（交易日）
_COST_MIN_NET = 0.2    # 淨額須佔總進出量的比例，低於此不給法人成本（買賣互抵）
_CONSENSUS_MIN = 0.1   # 外資/投信 5 日淨額低於此（億）不判一致性
_DISPERSE_MIN = 0.05   # 成分股 5 日淨額低於此（億）不計入擴散度家數
# 個股表每個法人別取買超前 N + 賣超前 N（全列 1162 檔 × 4 頁簽會讓 HTML 再翻倍）
_STOCK_TABLE_TOP = 50
_Z_MIN_DAYS = 10        # 今日 z 分數至少要這麼多天的歷史才算

# 型態：流向狀態（續進/續出/轉買/轉賣）× 20 日漲跌，再用 5 日漲跌拆出「拉回續買」
# 與「反彈續賣」。順序 = 多空排序用（越前面越多頭），模板的 data-order 取 index。
_PATTERNS = ("主流延續", "拉回續買", "追漲回補", "低接轉買", "買不漲",
             "漲多轉賣", "停損轉賣", "拉高調節", "反彈續賣", "棄守")
_PATTERN_ORDER = {p: len(_PATTERNS) - i for i, p in enumerate(_PATTERNS)}
_PATTERN_FLAT = 0.1     # 5 日淨額小於此（億）視為沒有轉向證據，跟著 20 日方向走
# 四維「大票」門檻（億）：個股層彙總與 AI 摘要只看這些，其餘的型態只進計數
_BIG_NET5, _BIG_NET20 = 5.0, 15.0

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


def _today_stats(series: list[float], min_abs: float) -> tuple[float | None, int, int]:
    """今日淨額的脈絡：(z 分數, 轉向, 轉向前連續天數)。

    z = 今日 ÷ 前 N 日淨額標準差（不含今日）——「聯發科 +240 億」對它是常態、
    「中型股 +8 億」可能是三個標準差，市值比看不出這件事。
    轉向 = 今日符號與前一日相反（兩天都要 ≥ _FLAT_YI）：+1 翻買、-1 翻賣、0 無。
    轉向前連續天數 = 前一日往回數的同向天數（模板寫成「翻買 ←賣 6 天」）。
    """
    z = None
    hist = series[:-1]
    # 今日太小不給 z：小型股 20 日幾乎沒動，2 億就能噴 12σ，排序會被這種噪音佔滿
    if len(hist) >= _Z_MIN_DAYS and abs(series[-1]) >= min_abs:
        mean = sum(hist) / len(hist)
        sd = (sum((v - mean) ** 2 for v in hist) / len(hist)) ** 0.5
        if sd > 0:
            z = round(series[-1] / sd, 1)
    flip, prev = 0, 0
    if len(series) >= 2 and abs(series[-1]) >= _FLAT_YI and abs(series[-2]) >= _FLAT_YI             and (series[-1] > 0) != (series[-2] > 0):
        flip = 1 if series[-1] > 0 else -1
        prev = _streak(hist)
    return z, flip, prev


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


def _price_metrics(today: date, sector_of: dict[str, str]
                  ) -> tuple[dict, dict, dict, dict, dict]:
    """一次載入 52 週價格窗口，算完所有價格衍生指標。

    回傳 ({N: 子類股近 N 日等權報酬}, {N: 個股近 N 日報酬}, 個股 52 週位階,
          {date: 當日快照}, {N: 實際報酬天數})，N ∈ {1, _SHORT, _DAYS}

    52 週高低走還原權息 cum 序列（跟 market_trend 同一套鏈與 ±12% 護欄），
    不然高股息股會因為除息缺口被算成「離高點很遠」。位階 0% = 就在 52 週高點。
    """
    window = price_cache.load_window(today, _WEEK52)
    if len(window) < 2:
        return {}, {}, {}, {}, {}

    refs = exrights.load_refs()
    chains: dict[str, _Chain] = {}
    peak: dict[str, float] = {}
    ret_start = {n: max(0, len(window) - n - 1) for n in (1, _SHORT, _DAYS)}
    cum_at_start: dict[int, dict[str, float]] = {}

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
            if ch.cum > peak.get(code, 0):
                peak[code] = ch.cum
        for n, start in ret_start.items():
            if i == start:
                cum_at_start[n] = {code: ch.cum for code, ch in chains.items()}

    sector_rets: dict[int, dict[str, float]] = {}
    code_rets: dict[int, dict[str, float]] = {}
    for n, base_map in cum_at_start.items():
        buckets: dict[str, list[float]] = defaultdict(list)
        rets: dict[str, float] = {}
        for code, ch in chains.items():
            base = base_map.get(code)
            if base:
                r = (ch.cum / base - 1) * 100
                rets[code] = round(r, 2)
                buckets[sector_of.get(code) or "其他"].append(r)
        code_rets[n] = rets
        sector_rets[n] = {s: round(sum(v) / len(v), 2) for s, v in buckets.items()}

    pos52 = {code: round((ch.cum / peak[code] - 1) * 100, 1)
             for code, ch in chains.items() if peak.get(code)}
    snap_by_date = {d: snap for d, snap in window}
    return (sector_rets, code_rets, pos52, snap_by_date,
            {n: len(window) - 1 - start for n, start in ret_start.items()})


def _inst_cost(series: list[float], dates: list, snap_by_date: dict,
               code: str, close_now: float) -> tuple[float | None, float | None]:
    """法人平均成本與現價相對它的位置。

    每日淨額是億元，先用當日收盤還原成張，再用當日 typical price (H+L+C)/3
    當成交均價近似（沒有官方 VWAP）。加權平均後：
        成本 = Σ(張 × tp) / Σ(張)
    買賣互抵到分母趨近 0 時倍數會爆掉，所以要求淨額佔總進出量 ≥ 20% 才給值。
    """
    num = den = gross = 0.0
    for i, d in enumerate(dates):
        yi = series[i]
        if yi == 0:
            continue
        px = (snap_by_date.get(d) or {}).get(code)
        if not px:
            continue
        c = px.get("c", 0)
        if c <= 0:
            continue
        zhang = yi * 1e8 / (1000 * c)
        tp = (px.get("h", c) + px.get("l", c) + c) / 3 or c
        num   += zhang * tp
        den   += zhang
        gross += abs(zhang)
    if not gross or abs(den) < gross * _COST_MIN_NET or not close_now:
        return None, None
    cost = num / den
    if cost <= 0:
        return None, None
    return round(cost, 2), round((close_now / cost - 1) * 100, 1)


def _consensus(f5: float, t5: float) -> tuple[int, str]:
    """外資 vs 投信 5 日方向。自營商多為避險部位，不納入判斷。"""
    if abs(f5) < _CONSENSUS_MIN or abs(t5) < _CONSENSUS_MIN:
        return 0, ""
    if f5 > 0 and t5 > 0:
        return 2, "同買"
    if f5 < 0 and t5 < 0:
        return -2, "同賣"
    return 0, "分歧"


def _pattern(net5: float, net20: float, ret5, ret20) -> str:
    """四維壓成一個型態詞。缺價格資料回 ''。

    流向：5 日與 20 日同號 → 續進/續出；異號 → 轉買/轉賣（5 日太小不算轉向）。
    價格主軸看 20 日，5 日只用來把「趨勢中的拉回/反彈」從續進/續出裡拆出來。
    """
    if ret5 is None or ret20 is None:
        return ""
    long_in = net20 > 0
    short_in = long_in if abs(net5) < _PATTERN_FLAT else net5 > 0
    up20, up5 = ret20 > 0, ret5 > 0
    if short_in and long_in:
        if not up20:
            return "買不漲"
        return "拉回續買" if not up5 else "主流延續"
    if not short_in and not long_in:
        if up20:
            return "拉高調節"
        return "反彈續賣" if up5 else "棄守"
    if short_in:                      # 出 → 進
        return "追漲回補" if up20 else "低接轉買"
    return "漲多轉賣" if up20 else "停損轉賣"


def _four_dim(full_rows: dict[str, list[dict]]) -> dict:
    """個股層四維彙總（未截斷的全樣本）：型態分布 + 大票列（帶外資/投信拆分）。

    給 four_dim_report.py（每日報告）與 ai_summary（盤勢總覽）共用；第 6 區塊的
    stock_tabs 只留買賣超各前 50，型態分布要用全樣本才算得對。
    """
    rows = full_rows.get("c") or []
    if not rows:
        return {}
    fmap = {r["code"]: r for r in full_rows.get("f", ())}
    tmap = {r["code"]: r for r in full_rows.get("t", ())}
    counts = {p: {"p": p, "n": 0, "f5": 0.0, "f20": 0.0} for p in _PATTERNS}
    big = []
    for r in rows:
        c = counts.get(r["pattern"])
        if c:
            c["n"] += 1; c["f5"] += r["net5"]; c["f20"] += r["net20"]
        if abs(r["net5"]) >= _BIG_NET5 or abs(r["net20"]) >= _BIG_NET20:
            f, t = fmap.get(r["code"], {}), tmap.get(r["code"], {})
            big.append(dict(r, f5=f.get("net5", 0.0), f20=f.get("net20", 0.0),
                            t5=t.get("net5", 0.0), t20=t.get("net20", 0.0),
                            net20_pct=round(r["net20"] / r["mcap"] * 100, 2) if r.get("mcap") else None))
    ret5 = sorted(r["ret5"] for r in rows if r["ret5"] is not None)
    ret20 = sorted(r["ret20"] for r in rows if r["ret20"] is not None)
    med = lambda a: round(a[len(a) // 2], 2) if a else None
    return {
        "total":   len(rows),
        "summary": [{"p": p, "n": c["n"], "f5": round(c["f5"]), "f20": round(c["f20"])}
                    for p, c in counts.items()],
        "big":     big,
        "median_ret5": med(ret5),
        "median_ret20": med(ret20),
        "total_net5":  round(sum(r["net5"] for r in rows), 1),
        "total_net20": round(sum(r["net20"] for r in rows), 1),
    }


def build(today: date) -> dict:
    window = inst_flow_cache.load_window(today, _DAYS)
    coverage = price_cache.window_coverage(window, today)
    days = len(window)

    if days < 2:
        logger.warning("sector_flow: inst_flow 快取不足（%d 天）— 先跑 backfill_inst.py", days)
        return {"days": days, "ret_days": 0, "ret_days_long": 0, "coverage": coverage, "degraded": True, "four_dim": {},
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

    sector_rets, all_code_rets, pos52, snap_by_date, ret_days = _price_metrics(today, code_to_sector)
    rets,   rets20      = sector_rets.get(_SHORT, {}),   sector_rets.get(_DAYS, {})
    code_rets, code_rets20 = all_code_rets.get(_SHORT, {}), all_code_rets.get(_DAYS, {})
    rets1, code_rets1 = sector_rets.get(1, {}), all_code_rets.get(1, {})

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
            z1, flip, prev_streak = _today_stats(series, _ACCEL_MIN_AVG)

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
                    "net1":   round(ss[-1], 2),
                    "net5":   round(s5, 2),
                    "net20":  round(sum(ss), 2),
                    "streak": _streak(ss),
                    "ret5":   code_rets.get(code),
                    "ret20":  code_rets20.get(code),
                    "pattern": _pattern(s5, sum(ss), code_rets.get(code), code_rets20.get(code)),
                })
            picks = [m for m in members if abs(m["net5"]) >= _STOCK_SHOW_YI]
            if len(picks) < _STOCK_MIN_SHOW:      # 小類股保底：至少看得到前幾名
                picks = sorted(members, key=lambda x: abs(x["net5"]),
                               reverse=True)[:_STOCK_MIN_SHOW]
            picks.sort(key=lambda x: x["net5"], reverse=True)
            # 買超側取頭、賣超側取尾（中間那些本來就不是重點）
            stocks = (picks if len(picks) <= _STOCK_CAP
                      else picks[:_STOCK_CAP // 2] + picks[-(_STOCK_CAP - _STOCK_CAP // 2):])

            # 擴散度：這波錢是全族群還是單一檔撐的
            members = [(c, sum(day[ki] for day in series_by_code[c][-_SHORT:]))
                       for c in codes_by_sector.get(sec, ())]
            moved = [(c, v) for c, v in members if abs(v) >= _DISPERSE_MIN]
            up   = sum(1 for _, v in moved if v > 0)
            down = sum(1 for _, v in moved if v < 0)
            gross = sum(abs(v) for _, v in moved)
            lead_code, lead_v = max(moved, key=lambda x: abs(x[1]), default=(None, 0.0))
            lead = round(abs(lead_v) / gross * 100) if gross else None

            smc = sector_mcap.get(sec)
            rows.append({
                "up":    up,
                "down":  down,
                "moved": len(moved),
                "lead":  lead,
                "lead_name": names.get(lead_code, "") if lead_code else "",
                "lead_code": lead_code or "",
                "sector": sec,
                "parent": sector_parent.get(sec, ""),
                "mcap":   round(smc) if smc else None,
                "net5_pct": round(net5 / smc * 100, 2) if smc else None,
                "net20_pct": round(net20 / smc * 100, 2) if smc else None,   # 輪動圖 x 軸
                "net1":   round(series[-1], 2),
                "z1":     z1,
                "flip":   flip,
                "prev_streak": prev_streak,
                "ret1":   rets1.get(sec),
                "net5":   round(net5,  2),
                "net20":  round(net20, 2),
                "streak": _streak(series),
                "accel":  ratio,
                "accel_tag": tag,
                "ret5":   rets.get(sec),
                "ret20":  rets20.get(sec),
                "pattern": _pattern(net5, net20, rets.get(sec), rets20.get(sec)),
                "n":      window[-1][1].get(sec, {}).get("n", 0),
                "stocks": stocks,
                "hidden": max(0, len(picks) - len(stocks)),
            })
        rows.sort(key=lambda r: r["net5"], reverse=True)
        tabs[key] = rows

    # 個股表：跟母表同一份 series，只是不分子類股。子類股內成分差異大時，
    # 分組本身就是雜訊 —— 這裡讓個股自己排隊。
    flow_dates = [d for d, _ in window]
    stock_tabs = {}
    full_rows: dict[str, list[dict]] = {}
    for key, _label in _TABS:
        ki = _SER_IDX[key]
        srows = []
        for code, ser in series_by_code.items():
            ss = [day[ki] for day in ser]
            s5 = sum(ss[-_SHORT:])
            if s5 == 0:
                continue
            ratio, tag = _accel(ss, _ACCEL_MIN_AVG_STOCK)
            z1, flip, prev_streak = _today_stats(ss, _ACCEL_MIN_AVG_STOCK)
            mcap = mcaps.get(code)
            close_now = last_px.get(code, 0)
            cost, vs_cost = _inst_cost(ss, flow_dates, snap_by_date, code, close_now)
            cons_order, cons_tag = _consensus(
                sum(day[_SER_IDX["f"]] for day in ser[-_SHORT:]),
                sum(day[_SER_IDX["t"]] for day in ser[-_SHORT:]))
            srows.append({
                "pos52":      pos52.get(code),
                "cost":       cost,
                "vs_cost":    vs_cost,
                "cons_order": cons_order,
                "cons_tag":   cons_tag,
                "code":   code,
                "name":   names.get(code, ""),
                "sector": code_to_sector.get(code) or "其他",
                "mcap":   round(mcap) if mcap else None,
                "net5_pct": round(s5 / mcap * 100, 2) if mcap else None,
                "net1":   round(ss[-1], 2),
                "z1":     z1,
                "flip":   flip,
                "prev_streak": prev_streak,
                "net5":   round(s5, 2),
                "net20":  round(sum(ss), 2),
                "streak": _streak(ss),
                "accel":  ratio,
                "accel_tag": tag,
                "ret1":   code_rets1.get(code),
                "ret5":   code_rets.get(code),
                "ret20":  code_rets20.get(code),
                "pattern": _pattern(s5, sum(ss), code_rets.get(code), code_rets20.get(code)),
            })
        srows.sort(key=lambda r: r["net5"], reverse=True)
        full_rows[key] = srows
        # 個股表 = 今日買賣超兩端 ∪ 5 日買賣超兩端（原單日「法人買賣超個股」區塊併進來），
        # 預設按今日淨額排。
        if len(srows) <= _STOCK_TABLE_TOP * 2:
            picked = srows
        else:
            by1 = sorted(srows, key=lambda r: r["net1"], reverse=True)
            keep = {id(r) for r in srows[:_STOCK_TABLE_TOP] + srows[-_STOCK_TABLE_TOP:]
                    + by1[:_STOCK_TABLE_TOP] + by1[-_STOCK_TABLE_TOP:]}
            picked = [r for r in srows if id(r) in keep]
        stock_tabs[key] = sorted(picked, key=lambda r: r["net1"], reverse=True)

    return {
        "four_dim": _four_dim(full_rows),
        # 給漲跌幅前 100 / 52 週新高低清單標型態與今日淨額用（三大法人）
        "by_code": {r["code"]: {"pattern": r["pattern"], "net1": r["net1"], "streak": r["streak"]}
                    for r in full_rows.get("c", ())},
        "days":     days,
        "ret_days": ret_days.get(_SHORT, 0),
        "ret_days_long": ret_days.get(_DAYS, 0),
        "pattern_order": _PATTERN_ORDER,
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
