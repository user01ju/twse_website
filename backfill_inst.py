"""回補「法人 × 子類股」淨買賣超快取（output/data/inst_flow/）。

兩個端點都吃日期，各自一次回傳整個市場：
  上市 TWSE T86       rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALLBUT0999
  上櫃 TPEX 三大法人   www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=YYYY/MM/DD
（上櫃**不要**用 /openapi/v1/ 那套 — 它不吃日期，只回最新一天。）
實測兩邊都能回溯到 2018 年，非交易日／尚未發布一律回「0 筆 + 不報錯」。

億元換算要當日收盤 → 依賴 price_cache，所以回補範圍別超過 price cache 的天數
（先跑 backfill_prices.py）。缺價的日子會跳過。

Usage:
  python backfill_inst.py                 # 回補最近 60 個交易日
  python backfill_inst.py --days 90
  python backfill_inst.py --force         # 重抓已有快取的日子
  python backfill_inst.py --dry-run
  python backfill_inst.py --sleep 1.5     # 每個 request 間隔（預設 3 秒）

每天各自寫檔，中斷後重跑會自動接續（沒有 --force 時跳過已存在的）。
"""
import argparse
import logging
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from fetcher import inst_flow_cache, price_cache
from fetcher.market_calendar import is_trading_day
from processor.utils import parse_num

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

HEADERS   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TWSE_T86  = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"

# TPEX 明細表欄位（0-based）：代號 名稱 ＋ 7 組「買進/賣出/買賣超」＋ 三大法人合計。
# fields 裡「買賣超股數」重複 7 次，所以不能用 fields.index() 定位，只能吃位置；
# 換來的風險用「外資合計 + 投信 + 自營合計 == 三大法人合計」這條恆等式擋。
_TPEX_NCOL   = 24
_TPEX_FOREIGN = 10   # 外資及陸資合計
_TPEX_TRUST   = 13   # 投信
_TPEX_DEALER  = 22   # 自營商合計
_TPEX_TOTAL   = 23   # 三大法人合計


def _fetch_t86(d: date) -> dict | None:
    """上市法人買賣超。{} = 非交易日/未發布，None = 抓取失敗。"""
    try:
        resp = requests.get(TWSE_T86, headers=HEADERS, timeout=30,
                            params={"response": "json", "date": d.strftime("%Y%m%d"),
                                    "selectType": "ALLBUT0999"})
        if resp.status_code != 200:
            logger.warning(f"{d.isoformat()} [TWSE]: HTTP {resp.status_code}")
            return None
        j = resp.json()
    except Exception as e:
        logger.warning(f"{d.isoformat()} [TWSE]: {e}")
        return None

    if str(j.get("stat", "")).upper() != "OK" or not j.get("data"):
        return {}
    return j


def _fetch_tpex(d: date) -> list[dict] | None:
    """上櫃法人買賣超，轉成 OpenAPI 的欄位名，好讓 combined_inst 的解析器直接吃。"""
    try:
        resp = requests.get(TPEX_INST, headers=HEADERS, timeout=30,
                            params={"type": "Daily", "sect": "EW",
                                    "date": d.strftime("%Y/%m/%d"), "id": "",
                                    "response": "json"})
        if resp.status_code != 200:
            logger.warning(f"{d.isoformat()} [TPEX]: HTTP {resp.status_code}")
            return None
        tables = resp.json().get("tables") or []
    except Exception as e:
        logger.warning(f"{d.isoformat()} [TPEX]: {e}")
        return None

    if not tables:
        return []
    table = tables[0]
    rows  = table.get("data") or []
    if not rows:
        return []                      # 非交易日／尚未發布：stat 仍是 ok，只是沒列

    fields = table.get("fields") or []
    if len(fields) != _TPEX_NCOL:
        logger.error(f"{d.isoformat()} [TPEX]: 欄位數 {len(fields)} != {_TPEX_NCOL}，"
                     f"版面可能改了 → 中止這天")
        return None

    out, mismatch = [], 0
    for row in rows:
        if len(row) < _TPEX_NCOL:
            continue
        f  = parse_num(row[_TPEX_FOREIGN])
        t  = parse_num(row[_TPEX_TRUST])
        de = parse_num(row[_TPEX_DEALER])
        tot = parse_num(row[_TPEX_TOTAL])
        if abs(f + t + de - tot) > 1:      # 分組位移偵測
            mismatch += 1
            continue
        out.append({
            "SecuritiesCompanyCode": str(row[0]).strip(),
            "CompanyName":           str(row[1]).strip(),
            "ForeignInvestorsIncludeMainlandAreaInvestors-Difference": f,
            "SecuritiesInvestmentTrustCompanies-Difference":           t,
            "Dealers-Difference":                                      de,
            "TotalDifference":                                         tot,
        })

    if mismatch > len(rows) * 0.01:
        logger.error(f"{d.isoformat()} [TPEX]: {mismatch}/{len(rows)} 列不符合"
                     f"「外資+投信+自營 = 三大法人」→ 欄位對位可能錯了，中止這天")
        return None
    if mismatch:
        logger.warning(f"{d.isoformat()} [TPEX]: 跳過 {mismatch} 列加總不符的資料")
    return out


def main():
    parser = argparse.ArgumentParser(description="回補法人子類股資金流向快取")
    parser.add_argument("--days", type=int, default=60, help="回補幾個交易日（預設 60）")
    parser.add_argument("--force", action="store_true", help="重抓已有快取的日子")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=3.0, help="每個 request 間隔秒數")
    args = parser.parse_args()

    today = date.today()
    trading_days, d = [], today
    while len(trading_days) < args.days and (today - d).days < args.days * 3:
        if is_trading_day(d):
            trading_days.append(d)
        d -= timedelta(days=1)
    trading_days.reverse()

    todo = [d for d in trading_days if args.force or not inst_flow_cache.load(d)]
    logger.info(f"範圍 {trading_days[0]} → {trading_days[-1]}，交易日 {len(trading_days)}，"
                f"待抓 {len(todo)}，預估 ~{len(todo) * (args.sleep * 2 + 2) / 60:.0f} 分鐘")

    if args.dry_run:
        for d in todo[:10]:
            logger.info(f"  would fetch {d.isoformat()}")
        if len(todo) > 10:
            logger.info(f"  ... and {len(todo) - 10} more")
        return
    if not todo:
        logger.info("沒有要抓的。")
        return

    ok = empty = skipped = failed = 0
    consecutive_fail = 0
    for i, day in enumerate(todo, 1):
        prices = {c: px.get("c", 0) for c, px in price_cache.load(day).items()}
        if not prices:
            logger.warning(f"{day.isoformat()}: price cache 沒有這天 → 跳過"
                           f"（先跑 backfill_prices.py）")
            skipped += 1
            continue

        t86 = _fetch_t86(day)
        time.sleep(args.sleep + random.uniform(0, 1))
        tpex = _fetch_tpex(day) if t86 is not None else None
        time.sleep(args.sleep + random.uniform(0, 1))

        if t86 is None or tpex is None:
            failed += 1
            consecutive_fail += 1
            if consecutive_fail >= 5:
                logger.error("連續 5 天失敗 — 可能被限流，中止；稍後重跑會接續。")
                break
            time.sleep(30)
            continue
        consecutive_fail = 0

        if not t86 or not tpex:
            logger.info(f"{day.isoformat()}: 無資料（非交易日或當時未發布）")
            empty += 1
            continue

        if inst_flow_cache.save(day, t86, tpex, prices, prices):
            ok += 1
        else:
            failed += 1

        if i % 10 == 0 or i == len(todo):
            logger.info(f"進度 {i}/{len(todo)}（存 {ok}、空 {empty}、"
                        f"缺價跳過 {skipped}、失敗 {failed}）")

    logger.info(f"完成：存 {ok}、空 {empty}、缺價跳過 {skipped}、失敗 {failed}")


if __name__ == "__main__":
    main()
