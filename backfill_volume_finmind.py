"""用 FinMind TaiwanStockPrice 補 price cache 缺的成交量 v / 成交金額 a（逐檔）。

    python backfill_volume_finmind.py            # 補所有缺 v 的代號
    python backfill_volume_finmind.py --dry-run  # 只列要補幾檔

為什麼不用 TPEX dailyQuotes：那個 dated 端點每小時只給 ~20 次請求就把連線掐掉
（sleep 3 秒和 12 秒都在第 21 次左右被 reset），347 天要 17 小時。FinMind 一檔一次
呼叫就回整段歷史，有 token 600/hr → ~860 檔約 90 分鐘，而且 Trading_Volume /
Trading_money 跟 TPEX 官方逐股逐日對得上（實測 6488 2026-09-11 完全相同）。

FinMind 免費/贊助層不能省 data_id 一次撈全市場（400），所以只能逐檔迴圈。
token 走 FINMIND_TOKEN 環境變數（沒有就 300/hr、sleep 拉長）。
進度存 output/data/.finmind_volume_done.json，中斷重跑會接續。
"""
import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

import config

API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
SLEEP = float(os.environ.get("FINMIND_SLEEP", "6.2" if TOKEN else "12.5"))
PRICES = config.OUTPUT_DIR / "data" / "prices"
DONE = config.OUTPUT_DIR / "data" / ".finmind_volume_done.json"


def _fetch(code: str, start: str) -> list[dict]:
    params = {"dataset": "TaiwanStockPrice", "data_id": code, "start_date": start}
    if TOKEN:
        params["token"] = TOKEN
    for attempt in range(5):
        try:
            r = requests.get(API, params=params, timeout=60)
        except Exception as e:
            print(f"  ! {code}: {e}，30s 後重試", file=sys.stderr)
            time.sleep(30)
            continue
        if r.status_code == 402 or (r.status_code == 200 and r.json().get("status") == 402):
            print("  ... 額度上限，sleep 120s", file=sys.stderr)
            time.sleep(120)
            continue
        if r.status_code != 200:
            print(f"  ! {code}: HTTP {r.status_code}", file=sys.stderr)
            return []
        return r.json().get("data", [])
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--checkpoint", type=int, default=40, help="每 N 檔寫一次快取")
    args = ap.parse_args()

    files = sorted(PRICES.glob("*.json"))
    cache = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in files}
    dirty: set[str] = set()
    missing: dict[str, int] = {}
    for d, snap in cache.items():
        for code, px in snap.items():
            if "v" not in px:
                missing[code] = missing.get(code, 0) + 1
    done = set(json.loads(DONE.read_text(encoding="utf-8"))) if DONE.exists() else set()
    todo = sorted(c for c in missing if c not in done)
    print(f"快取 {len(files)} 日；缺 v 的代號 {len(missing)} 檔（{sum(missing.values()):,} 個股×日），"
          f"待補 {len(todo)} 檔，約 {len(todo) * (SLEEP + 0.8) / 60:.0f} 分鐘", file=sys.stderr)
    if args.dry_run or not todo:
        return 0

    start = files[0].stem

    def flush():
        for d in sorted(dirty):
            (PRICES / f"{d}.json").write_text(json.dumps(cache[d], ensure_ascii=False), encoding="utf-8")
        dirty.clear()
        DONE.write_text(json.dumps(sorted(done)), encoding="utf-8")

    filled = 0
    for i, code in enumerate(todo, 1):
        rows = _fetch(code, start)
        by_date = {r["date"]: r for r in rows}
        for d, snap in cache.items():
            px = snap.get(code)
            if px is None or "v" in px:
                continue
            r = by_date.get(d)
            if r is None:
                continue
            px["v"] = int(r["Trading_Volume"] // 1000)
            px["a"] = round(r["Trading_money"] / 1e8, 3)
            dirty.add(d)
            filled += 1
        done.add(code)
        if i % args.checkpoint == 0 or i == len(todo):
            flush()
            print(f"  {i}/{len(todo)} 檔，已填 {filled:,} 格", file=sys.stderr)
        time.sleep(SLEEP)

    still = sum(1 for snap in cache.values() for px in snap.values() if "v" not in px)
    print(f"Done. 填了 {filled:,} 格，仍缺 {still:,}（FinMind 也沒有的日子，通常是新掛牌前或已下市）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
