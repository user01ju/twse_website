"""法人四維透視（個股層）— 排程用的三段式工具。

    python four_dim_report.py check  [--sync]            # 今天資料到齊了嗎？exit 0 齊 / 3 未齊 / 4 非交易日 / 5 今天已產出
    python four_dim_report.py data   [--date D]          # 算數據 → output/four_dim/D.json，並印一份文字摘要給 Claude 讀
    python four_dim_report.py render --date D --narrative N.json   # 把敘事 JSON 塞進模板 → output/four_dim/D.html

四維 = (5 日流向, 20 日流向) × (5 日漲跌, 20 日漲跌)，型態詞與資料都直接吃
processor.sector_flow（跟第 5/6 區塊同一套口徑），這裡只做彙總與排版。

資料來源是 gh-pages 上的每日快取；`--sync` 會 `git archive origin/gh-pages data` 把
data/ 整包蓋進 output/（只蓋 data/，不碰本機報告）。排程時 CI 可能還沒推，所以 check 會
拿「最新交易日」對 inst_flow / inst_stock / prices 三份快取逐一比對，缺一就是未齊。
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import config
from fetcher import inst_flow_cache, market_calendar, price_cache

OUT_DIR = config.OUTPUT_DIR / "four_dim"
TEMPLATE = config.BASE_DIR / "templates" / "four_dim_report.html"

PATTERNS = ("主流延續", "拉回續買", "追漲回補", "低接轉買", "買不漲",
            "漲多轉賣", "停損轉賣", "拉高調節", "反彈續賣", "棄守")
GROUPS = (("主流延續",), ("拉回續買",), ("追漲回補", "低接轉買"), ("買不漲",),
          ("漲多轉賣", "停損轉賣"), ("拉高調節", "反彈續賣"), ("棄守",))


def _today_tw() -> date:
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def _sync() -> None:
    subprocess.run(["git", "fetch", "-q", "origin", "gh-pages"], cwd=config.BASE_DIR, check=True)
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    # git archive 只吐 data/ 子樹，tar 解到 output/ → output/data/...
    arc = subprocess.run(["git", "archive", "origin/gh-pages", "data"], cwd=config.BASE_DIR,
                         check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(config.OUTPUT_DIR)], input=arc, check=True)


def cmd_check(args) -> int:
    today = _today_tw()
    if not market_calendar.is_trading_day(today):
        print(f"SKIP 非交易日 {today}")
        return 4
    if (OUT_DIR / f"{today.isoformat()}.done").exists():
        print(f"DONE 今天已產出 {today}")
        return 5
    if args.sync:
        _sync()
    missing = [name for name, p in (
        ("inst_flow",  inst_flow_cache._path(today)),
        ("inst_stock", inst_flow_cache._stock_path(today)),
        ("prices",     price_cache._path(today)),
    ) if not p.exists()]
    if missing:
        print(f"STALE {today} 缺 {', '.join(missing)}")
        return 3
    print(f"FRESH {today}")
    return 0


def cmd_data(args) -> int:
    from processor import sector_flow
    d = date.fromisoformat(args.date) if args.date else market_calendar.get_latest_trading_day()
    res = sector_flow.build(d)
    fd = res.get("four_dim") or {}
    if res["degraded"] or not fd:
        print(f"DEGRADED coverage={res['coverage']} days={res['days']}", file=sys.stderr)
        return 3
    summary, big = fd["summary"], fd["big"]
    sectors = [{k: r.get(k) for k in ("sector", "parent", "mcap", "net5", "net20", "net5_pct",
                                      "ret5", "ret20", "pattern", "streak", "accel_tag",
                                      "up", "down", "lead", "lead_name")}
               for r in res["tabs"]["c"]]
    med5, med20 = fd["median_ret5"], fd["median_ret20"]
    payload = {"date": d.isoformat(), "start": res["start"], "end": res["end"], "days": res["days"],
               "total": fd["total"], "summary": summary, "stocks": big, "sectors": sectors,
               "median_ret5": med5, "median_ret20": med20}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{d.isoformat()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ---- 文字摘要（給 Claude 寫敘事用；stdout）----
    def line(r):
        return (f"{r['code']} {r['name']:<7}{r['sector'][:8]:<9} 5d{r['net5']:>7.1f} 20d{r['net20']:>7.1f} "
                f"mc{r['mcap'] or 0:>6} 5d%{r['net5_pct'] or 0:>5.2f} 連{r['streak']:>3} {r['accel_tag'] or '':<2} "
                f"r5{r['ret5'] or 0:>7.2f} r20{r['ret20'] or 0:>7.2f} 52w{r['pos52'] if r['pos52'] is not None else '':>6} "
                f"cost{r['vs_cost'] if r['vs_cost'] is not None else '':>6} {r['cons_tag'] or '':<2} "
                f"外{r['f5']:>5.0f}/{r['f20']:<5.0f} 投{r['t5']:>4.0f}/{r['t20']:<4.0f}")
    print(f"# 法人四維 {d} 窗口 {res['start']}~{res['end']} ({res['days']} 日) 個股 {fd['total']} 檔 / 大票 {len(big)} 檔 "
          f"/ 個股漲跌中位 5 日 {med5:+.2f}% · 20 日 {med20:+.2f}%")
    print("wrote", out)
    print("\n## 型態分布（全樣本 n / 5日 / 20日 / 大票 52w 中位）")
    for s in summary:
        m = median([r["pos52"] for r in big if r["pattern"] == s["p"] and r["pos52"] is not None] or [float("nan")])
        print(f"{s['p']:<6} n={s['n']:>4} 5d{s['f5']:>7} 20d{s['f20']:>7}  52w_med {m:>6.1f}")
    for pats in GROUPS:
        sel = sorted([r for r in big if r["pattern"] in pats], key=lambda r: -abs(r["net5"]))
        print(f"\n## {' / '.join(pats)}（大票 {len(sel)} 檔）")
        for r in sel[:16]:
            print(line(r))
    print("\n## 連買 ≥ 10 天")
    for r in sorted([r for r in big if r["streak"] >= 10], key=lambda r: -r["net20"])[:10]:
        print(line(r), r["pattern"])
    print("\n## 連賣 ≥ 8 天")
    for r in sorted([r for r in big if r["streak"] <= -8], key=lambda r: r["net20"])[:10]:
        print(line(r), r["pattern"])
    print("\n## 外資/投信 5 日分歧（|外資| 與 |投信| 都 ≥ 5 億且異號）")
    for r in sorted([r for r in big if abs(r["f5"]) >= 5 and abs(r["t5"]) >= 5 and r["f5"] * r["t5"] < 0],
                    key=lambda r: -abs(r["f5"] - r["t5"]))[:12]:
        print(line(r))
    print("\n## 子類股（三大法人，依 20 日/市值排序，前 25）")
    for s in sorted(sectors, key=lambda s: -((s["net20"] / s["mcap"]) if s["mcap"] else 0))[:25]:
        print(f"{s['sector']:<12} 5d{s['net5']:>7.0f} 20d{s['net20']:>7.0f} r5{s['ret5'] or 0:>7.2f} r20{s['ret20'] or 0:>7.2f} "
              f"{s['pattern'] or '':<5} 連{s['streak']:>3} {s['accel_tag'] or '':<2} {s['up']}買/{s['down']}賣 龍頭{s['lead'] or 0}% {s['lead_name']}")
    return 0


def cmd_render(args) -> int:
    d = args.date
    data = (OUT_DIR / f"{d}.json").read_text(encoding="utf-8")
    narr = Path(args.narrative).read_text(encoding="utf-8")
    json.loads(narr)                                 # 早點炸，別讓壞 JSON 進頁面
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__DATA__", data.replace("</", "<\\/"))
            .replace("__NARR__", narr.replace("</", "<\\/")))
    out = OUT_DIR / f"{d}.html"
    out.write_text(html, encoding="utf-8")
    print("wrote", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("--sync", action="store_true")
    p = sub.add_parser("data");  p.add_argument("--date")
    p = sub.add_parser("render"); p.add_argument("--date", required=True); p.add_argument("--narrative", required=True)
    args = ap.parse_args()
    return {"check": cmd_check, "data": cmd_data, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
