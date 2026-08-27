"""Backfill historical OHLC price cache for market trend indicators.

Uses TWSE MI_INDEX (type=ALLBUT0999) — ONE request returns the whole
market's OHLC for a given date, so a 13-month backfill is only ~270
requests (vs 14k+ with the per-stock STOCK_DAY endpoint, which gets
rate-banned after ~50 stocks).

TPEX: 用 www/zh-tw/afterTrading/dailyQuotes（吃 date 參數，回溯得到多年）。
不是 /openapi/v1/ 那套 —— OpenAPI 一律只回最新一天，不吃日期。

Usage:
  python backfill_prices.py               # backfill last 13 months
  python backfill_prices.py --months 6    # only 6 months
  python backfill_prices.py --force       # re-fetch days that already have cache
  python backfill_prices.py --dry-run     # show what would be fetched
  python backfill_prices.py --sleep 5     # seconds between requests (default 3)

Each day is saved immediately, so an interrupted run resumes where it
left off (existing files are skipped without --force).
"""
import argparse
import json
import logging
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from fetcher.market_calendar import is_trading_day
from fetcher.price_cache import load, _path as _cache_path
from processor.utils import is_stock_code, parse_num

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TWSE_MI_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY    = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


def _fetch_tpex_day(d: date) -> dict[str, dict] | None:
    """
    Fetch all-market 上櫃 OHLC for one trading day via TPEX dailyQuotes.
    Fields: 0=代號, 2=收盤, 5=最高, 6=最低. Returns {code:{c,h,l}} or None on failure.
    """
    try:
        resp = requests.get(
            TPEX_DAILY,
            params={"date": d.strftime("%Y/%m/%d"), "type": "EW", "response": "json"},
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"{d.isoformat()} [TPEX]: HTTP {resp.status_code}")
            return None
        data = resp.json()
    except Exception as e:
        logger.warning(f"{d.isoformat()} [TPEX]: {e}")
        return None

    for table in data.get("tables", []):
        fields = table.get("fields", [])
        if "代號" not in fields or "收盤" not in fields:
            continue
        i_code  = fields.index("代號")
        i_close = fields.index("收盤")
        i_high  = fields.index("最高")
        i_low   = fields.index("最低")

        prices = {}
        for row in table.get("data", []):
            try:
                code = str(row[i_code]).strip()
                if not is_stock_code(code):
                    continue
                c  = parse_num(row[i_close])
                h  = parse_num(row[i_high])
                lo = parse_num(row[i_low])
                if c > 0:
                    prices[code] = {"c": c, "h": h or c, "l": lo or c}
            except Exception:
                continue
        return prices

    logger.info(f"{d.isoformat()} [TPEX]: no stock table (non-trading day?)")
    return {}


def _fetch_day(d: date) -> dict[str, dict] | None:
    """
    Fetch all-market OHLC for one trading day via MI_INDEX.
    Returns {code: {"c":, "h":, "l":}} or None on failure (incl. rate-limit).
    """
    try:
        resp = requests.get(
            TWSE_MI_INDEX,
            params={"response": "json", "date": d.strftime("%Y%m%d"), "type": "ALLBUT0999"},
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"{d.isoformat()}: HTTP {resp.status_code} (rate-limited?)")
            return None
        data = resp.json()
    except Exception as e:
        logger.warning(f"{d.isoformat()}: {e}")
        return None

    if data.get("stat") not in ("OK", "ok"):
        logger.info(f"{d.isoformat()}: stat={data.get('stat')} — skipping")
        return {}

    # Find the per-stock table: fields contain 證券代號 and 收盤價
    for table in data.get("tables", []):
        fields = table.get("fields", [])
        if "證券代號" not in fields or "收盤價" not in fields:
            continue
        i_code  = fields.index("證券代號")
        i_high  = fields.index("最高價")
        i_low   = fields.index("最低價")
        i_close = fields.index("收盤價")

        prices = {}
        for row in table.get("data", []):
            try:
                code = str(row[i_code]).strip()
                if not is_stock_code(code):
                    continue
                c  = parse_num(row[i_close])
                h  = parse_num(row[i_high])
                lo = parse_num(row[i_low])
                if c > 0:
                    prices[code] = {"c": c, "h": h or c, "l": lo or c}
            except Exception:
                continue
        return prices

    logger.warning(f"{d.isoformat()}: stock table not found in MI_INDEX response")
    return {}


def main():
    parser = argparse.ArgumentParser(description="Backfill OHLC price cache (TWSE MI_INDEX + TPEX dailyQuotes)")
    parser.add_argument("--months", type=int, default=13)
    parser.add_argument("--source", choices=["twse", "tpex", "both"], default="both",
                        help="Which market(s) to backfill")
    parser.add_argument("--force", action="store_true", help="Re-fetch days that already have cache")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds between requests")
    args = parser.parse_args()

    fetchers = []
    if args.source in ("twse", "both"):
        fetchers.append(("TWSE", _fetch_day))
    if args.source in ("tpex", "both"):
        fetchers.append(("TPEX", _fetch_tpex_day))

    today = date.today()
    start = today - timedelta(days=args.months * 31)

    trading_days = []
    d = start
    while d <= today:
        if is_trading_day(d):
            trading_days.append(d)
        d += timedelta(days=1)

    # A day needs fetching when its cache is missing or — for combined runs —
    # smaller than a full TWSE+TPEX day (TWSE-only ≈ 1088, combined ≈ 1900).
    threshold = 1500 if args.source in ("tpex", "both") else 500

    def needs_fetch(day: date) -> bool:
        if args.force:
            return True
        return len(load(day)) < threshold

    todo = [d for d in trading_days if needs_fetch(d)]
    logger.info(f"Range: {start.isoformat()} → {today.isoformat()}, "
                f"trading days: {len(trading_days)}, to fetch: {len(todo)}")
    logger.info(f"Estimated time: ~{len(todo) * (args.sleep + 1) / 60:.0f} minutes")

    if args.dry_run:
        for d in todo[:10]:
            logger.info(f"  would fetch {d.isoformat()}")
        if len(todo) > 10:
            logger.info(f"  ... and {len(todo) - 10} more")
        return

    if not todo:
        logger.info("Nothing to fetch.")
        return

    _cache_path(todo[0]).parent.mkdir(parents=True, exist_ok=True)

    ok = empty = failed = 0
    consecutive_fail = 0
    for i, day in enumerate(todo, 1):
        # Fetch each requested market, merging all into one day snapshot.
        merged = load(day)
        added = 0
        hard_fail = False
        for mkt, fn in fetchers:
            prices = fn(day)
            if prices is None:
                hard_fail = True
                break
            if prices:
                merged.update(prices)
                added += len(prices)
            time.sleep(args.sleep + random.uniform(0, 1))

        if hard_fail:
            failed += 1
            consecutive_fail += 1
            if consecutive_fail >= 5:
                logger.error("5 consecutive failures — likely rate-banned. "
                             "Stopping; rerun later to resume.")
                break
            time.sleep(30)
            continue
        consecutive_fail = 0

        if not added:
            empty += 1
        else:
            _cache_path(day).write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            ok += 1

        if i % 10 == 0 or i == len(todo):
            logger.info(f"Progress: {i}/{len(todo)} (saved {ok}, empty {empty}, failed {failed})")

    logger.info(f"Done. Saved {ok}, empty {empty}, failed {failed}.")


if __name__ == "__main__":
    main()
