"""四維型態 → 未來 5 / 20 日報酬：離線驗證（不進報告）。

    python pattern_forward_test.py [--min-window 20] [--out output/four_dim/pattern_test.md]

每個有完整 20 日 inst_flow 窗口的交易日 D，跑 sector_flow.build(D) 拿全樣本個股型態，
配 price_cache 的還原權息 cum 鏈算 D→D+5、D→D+20 的前瞻報酬（交易日以快取索引數）。

方法對齊 llm_wiki 的事件研究慣例：
- 超額 = 個股前瞻報酬 − 當日全市場中位數（等權、市場中性）。
- 勝率的虛無假設用全體樣本的實際勝率 p0，不是 50%。
- 顯著性與幅度並列；10 型態 × 2 horizon = 20 個檢定，Bonferroni 門檻 |z| ≥ 3.02。
- 觀測值是「個股 × 日」，相鄰日的前瞻窗口高度重疊 → 另給「按日聚類」的 t：先算每日型態平均超額，
  再對日序列做 t 檢定（fwd20 的有效獨立樣本大約只有 天數/20，讀的時候要打折）。
- 位階切分配「同狀態基準」：全市場 個股×日 依距 52 週高分桶的天生前瞻報酬，相減才是型態淨貢獻。
- 交易成本 0.44%（來回）當最後篩子。
"""
import argparse
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import config
from fetcher import exrights, inst_flow_cache, price_cache
from processor import sector_flow
from processor.market_trend import _WILD, _Chain

PATTERNS = sector_flow._PATTERNS
BULL = ("主流延續", "拉回續買")
HORIZONS = (5, 20)
COST = 0.44                       # 台股來回成本 %，手續費 5 折 + 證交稅
POS_BUCKETS = (("近高<10%", -10), ("離高10~25%", -25), ("離高25~40%", -40), ("離高>40%", -1e9))


def _bucket(pos52):
    if pos52 is None:
        return None
    for name, floor in POS_BUCKETS:
        if pos52 >= floor:
            return name
    return None


def _price_panel():
    """全市場還原權息 cum 鏈：回傳 (dates, {code: [cum or None per date]}, {code: [pos52 or None]})。"""
    files = sorted(p for p in (config.OUTPUT_DIR / "data" / "prices").glob("*.json"))
    dates = [date.fromisoformat(p.stem) for p in files]
    refs = exrights.load_refs()
    chains: dict[str, _Chain] = {}
    cum: dict[str, list] = {}
    pos: dict[str, list] = {}
    peak_hist: dict[str, list] = defaultdict(list)        # 逐日 cum，算 252 日滾動高
    n = len(dates)
    for i, d in enumerate(dates):
        snap = price_cache.load(d)
        day_refs = refs.get(d.isoformat(), {})
        for code, px in snap.items():
            c = px.get("c", 0)
            if c <= 0:
                continue
            ch = chains.get(code)
            if ch is None:
                ch = chains[code] = _Chain()
                cum[code] = [None] * n
                pos[code] = [None] * n
            base = day_refs.get(code) or ch.pc
            ret = c / base if base and base > 0 else 1.0
            gap = 1 if ch.last_idx is None else min(i - ch.last_idx, 2)
            band = _WILD ** gap
            if ret > band or ret < 1 / band:
                ret = 1.0
            ch.cum *= ret
            ch.pc, ch.last_idx = c, i
            cum[code][i] = ch.cum
            h = peak_hist[code]
            h.append(ch.cum)
            if len(h) > 252:
                del h[0]
            pos[code][i] = round((ch.cum / max(h) - 1) * 100, 1)
    return dates, cum, pos


def _fwd(cum_series, i, k):
    a, b = cum_series[i], (cum_series[i + k] if i + k < len(cum_series) else None)
    return (b / a - 1) * 100 if a and b else None


def _hit_z(hits, n, p0):
    if n == 0:
        return None
    p = hits / n
    return (p - p0) / math.sqrt(p0 * (1 - p0) / n)


def _t(series):
    if len(series) < 3:
        return None
    m, sd = st.mean(series), st.pstdev(series)
    return m / (sd / math.sqrt(len(series))) if sd > 0 else None


def _fmt(v, d=2, pct=True):
    if v is None:
        return "–"
    return f"{v:+.{d}f}{'%' if pct else ''}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-window", type=int, default=sector_flow._DAYS)
    ap.add_argument("--out", default=str(config.OUTPUT_DIR / "four_dim" / "pattern_test.md"))
    args = ap.parse_args()

    dates, cum, pos52_all = _price_panel()
    didx = {d: i for i, d in enumerate(dates)}
    flow_dates = sorted(date.fromisoformat(p.stem)
                        for p in (config.OUTPUT_DIR / "data" / "inst_flow").glob("*.json"))
    # 型態日：inst_flow 窗口要湊滿 min_window 天，且價格快取要有 D 與 D+5（fwd20 缺的就只算 fwd5）
    cand = [d for d in flow_dates[args.min_window - 1:] if d in didx and didx[d] + 5 < len(dates)]
    print(f"價格快取 {len(dates)} 日（{dates[0]}~{dates[-1]}），inst_flow {len(flow_dates)} 日，"
          f"型態日 {len(cand)}（{cand[0]}~{cand[-1]}）", file=sys.stderr)

    # obs: (date, code, pattern, big, pos_bucket, {k: excess}, {k: raw})
    obs = []
    market_med = {}                 # (date, k) → 全市場中位數
    panel_bucket = defaultdict(list)  # (bucket, k) → 全市場 個股×日 超額（同狀態基準）
    sector_flow._STOCK_TABLE_TOP = 10 ** 6
    for d in cand:
        i = didx[d]
        for k in HORIZONS:
            vals = [v for v in (_fwd(cum[c], i, k) for c in cum) if v is not None]
            market_med[(d, k)] = st.median(vals) if vals else None
        for code in cum:
            b = _bucket(pos52_all[code][i])
            if b is None:
                continue
            for k in HORIZONS:
                m = market_med[(d, k)]
                v = _fwd(cum[code], i, k)
                if m is not None and v is not None:
                    panel_bucket[(b, k)].append(v - m)
        res = sector_flow.build(d)
        if res["degraded"] or not res["stock_tabs"]:
            print(f"  skip {d}: degraded", file=sys.stderr)
            continue
        big = {r["code"] for r in res["four_dim"]["big"]}
        for r in res["stock_tabs"]["c"]:
            p = r["pattern"]
            if not p or r["code"] not in cum:
                continue
            ex, raw = {}, {}
            for k in HORIZONS:
                m = market_med[(d, k)]
                v = _fwd(cum[r["code"]], i, k)
                if m is not None and v is not None:
                    raw[k], ex[k] = v, v - m
            if ex:
                obs.append((d, r["code"], p, r["code"] in big, _bucket(r.get("pos52")), ex, raw))
        print(f"  {d}: {len(res['stock_tabs']['c'])} 檔", file=sys.stderr)

    out = []
    w = out.append
    n_days = len({o[0] for o in obs})
    w(f"# 四維型態 → 前瞻報酬驗證\n")
    w(f"型態日 {n_days} 天（{cand[0]} ~ {max(o[0] for o in obs)}），個股×日觀測 {len(obs):,}。"
      f"超額 = 個股前瞻報酬 − 當日全市場中位數。fwd20 只有 D+20 在快取內的日子才有。\n")

    for label, flt in (("全樣本", lambda o: True), ("大票（|5日| ≥ 5 億或 |20日| ≥ 15 億）", lambda o: o[3])):
        w(f"\n## {label}\n")
        for k in HORIZONS:
            sub = [o for o in obs if flt(o) and k in o[5]]
            if not sub:
                continue
            p0 = sum(1 for o in sub if o[5][k] > 0) / len(sub)
            w(f"\n### 未來 {k} 日（n={len(sub):,}，p0={p0*100:.1f}%，Bonferroni |z|≥3.02）\n")
            w("| 型態 | n | 天數 | 超額均值 | 超額中位 | 原始中位 | 勝率 | z(勝率) | t(按日) | 日勝率 | 扣成本後中位 |")
            w("|---|---|---|---|---|---|---|---|---|---|---|")
            for pat in PATTERNS:
                s = [o for o in sub if o[2] == pat]
                if len(s) < 30:
                    continue
                ex = [o[5][k] for o in s]
                by_day = defaultdict(list)
                for o in s:
                    by_day[o[0]].append(o[5][k])
                daily = [st.mean(v) for v in by_day.values()]
                hits = sum(1 for v in ex if v > 0)
                z = _hit_z(hits, len(ex), p0)
                t = _t(daily)
                med_raw = st.median(o[6][k] for o in s)
                star = " **" if z is not None and abs(z) >= 3.02 else ""
                w(f"| {pat}{star} | {len(s):,} | {len(daily)} | {_fmt(st.mean(ex))} | {_fmt(st.median(ex))} | "
                  f"{_fmt(med_raw)} | {hits/len(ex)*100:.1f}% | {_fmt(z, 2, False)} | {_fmt(t, 2, False)} | "
                  f"{sum(1 for v in daily if v > 0)/len(daily)*100:.0f}% | "
                  f"{_fmt(st.median(ex) - COST if st.median(ex) > 0 else st.median(ex) + COST)} |")

    # 2×2：把型態折回 (20 日流向, 20 日漲跌)，看主效應各是誰的
    FLOW_IN = {"主流延續", "拉回續買", "買不漲", "漲多轉賣", "停損轉賣"}
    RET_UP = {"主流延續", "拉回續買", "追漲回補", "漲多轉賣", "拉高調節"}
    w("\n## 主效應拆解：20 日流向 × 20 日漲跌（未來 20 日超額）\n")
    w("| 20日流向 | 20日漲跌 | n | 超額均值 | 超額中位 | 勝率 | t(按日) |")
    w("|---|---|---|---|---|---|---|")
    for fin in (True, False):
        for up in (True, False):
            s = [o for o in obs if 20 in o[5] and (o[2] in FLOW_IN) == fin and (o[2] in RET_UP) == up]
            if not s:
                continue
            ex = [o[5][20] for o in s]
            by_day = defaultdict(list)
            for o in s:
                by_day[o[0]].append(o[5][20])
            daily = [st.mean(v) for v in by_day.values()]
            w(f"| {'進' if fin else '出'} | {'漲' if up else '跌'} | {len(s):,} | {_fmt(st.mean(ex))} | {_fmt(st.median(ex))} | "
              f"{sum(1 for v in ex if v > 0)/len(ex)*100:.1f}% | {_fmt(_t(daily), 2, False)} |")

    # 分期穩定性：型態日按月切
    w("\n## 分期（型態日所在月份，未來 20 日超額中位）\n")
    months = sorted({o[0].strftime('%Y-%m') for o in obs if 20 in o[5]})
    w("| 型態 | " + " | ".join(months) + " |")
    w("|---|" + "---|" * len(months))
    for pat in PATTERNS:
        cells = []
        for m in months:
            ex = [o[5][20] for o in obs if o[2] == pat and 20 in o[5] and o[0].strftime('%Y-%m') == m]
            cells.append(f"{_fmt(st.median(ex))} (n={len(ex)})" if len(ex) >= 30 else "–")
        w(f"| {pat} | " + " | ".join(cells) + " |")

    # 多空價差：多頭型態 − 棄守，逐日
    w("\n## 多空價差（主流延續＋拉回續買 − 棄守，逐日等權）\n")
    w("| horizon | 天數 | 價差均值 | 價差中位 | t | 日勝率 | 最差一天 |")
    w("|---|---|---|---|---|---|---|")
    for k in HORIZONS:
        by_day = defaultdict(lambda: {"l": [], "s": []})
        for o in obs:
            if k not in o[5]:
                continue
            if o[2] in BULL:
                by_day[o[0]]["l"].append(o[5][k])
            elif o[2] == "棄守":
                by_day[o[0]]["s"].append(o[5][k])
        spread = [st.mean(v["l"]) - st.mean(v["s"]) for v in by_day.values() if v["l"] and v["s"]]
        if spread:
            w(f"| {k} 日 | {len(spread)} | {_fmt(st.mean(spread))} | {_fmt(st.median(spread))} | "
              f"{_fmt(_t(spread), 2, False)} | {sum(1 for v in spread if v > 0)/len(spread)*100:.0f}% | {_fmt(min(spread))} |")

    # 位階 × 型態，配同狀態基準
    w("\n## 位階 × 型態（未來 20 日；「基準」= 全市場同位階桶的 個股×日 超額中位）\n")
    w("| 型態 | 位階 | n | 超額中位 | 同狀態基準 | 淨 | 勝率 |")
    w("|---|---|---|---|---|---|---|")
    k = 20
    for pat in ("主流延續", "拉回續買", "低接轉買", "買不漲", "拉高調節", "棄守"):
        for bname, _ in POS_BUCKETS:
            s = [o for o in obs if o[2] == pat and o[4] == bname and k in o[5]]
            if len(s) < 30:
                continue
            ex = [o[5][k] for o in s]
            base = panel_bucket.get((bname, k)) or []
            bm = st.median(base) if base else None
            med = st.median(ex)
            w(f"| {pat} | {bname} | {len(s):,} | {_fmt(med)} | {_fmt(bm)} | "
              f"{_fmt(med - bm) if bm is not None else '–'} | {sum(1 for v in ex if v > 0)/len(ex)*100:.1f}% |")

    w("\n## 讀法提醒\n")
    w(f"- 相鄰型態日的 fwd{HORIZONS[-1]} 窗口重疊 {HORIZONS[-1]-1}/{HORIZONS[-1]}，「n」不是獨立樣本；"
      f"按日 t 的有效樣本約 天數/{HORIZONS[-1]}。目前只有 {n_days} 個型態日，結論一律標「初步」。")
    w(f"- 扣成本欄用來回 {COST}% 估：中位超額扣完還是同號才算「可交易」，否則是統計現象。")
    w("- 型態是收盤後才知道，前瞻報酬從 D 收盤起算 = 假設隔日開盤以 D 收盤價成交，實務上有跳空滑價。")
    text = "\n".join(out)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
