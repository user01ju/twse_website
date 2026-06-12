"""Backfill historical OHLC price cache for market trend indicators.

Uses TWSE MI_INDEX (type=ALLBUT0999) — ONE request returns the whole
market's OHLC for a given date, so a 13-month backfill is only ~270
requests (vs 14k+ with the per-stock STOCK_DAY endpoint, which gets
rate-banned after ~50 stocks).

TPEX: no public historical endpoint found; TPEX data accumulates going
forward from the daily report runs.

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
    parser = argparse.ArgumentParser(description="Backfill OHLC price cache via MI_INDEX")
    parser.add_argument("--months", type=int, default=13)
    parser.add_argument("--force", action="store_true", help="Re-fetch days that already have cache")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds between requests")
    args = parser.parse_args()

    today = date.today()
    start = today - timedelta(days=args.months * 31)

    trading_days = []
    d = start
    while d <= today:
        if is_trading_day(d):
            trading_days.append(d)
        d += timedelta(days=1)

    # A day needs fetching when its cache is missing or suspiciously small
    # (earlier broken backfill left ~49-stock files).
    def needs_fetch(day: date) -> bool:
        if args.force:
            return True
        existing = load(day)
        return len(existing) < 500

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
        prices = _fetch_day(day)

        if prices is None:
            failed += 1
            consecutive_fail += 1
            if consecutive_fail >= 5:
                logger.error("5 consecutive failures — likely rate-banned. "
                             "Stopping; rerun later to resume.")
                break
            time.sleep(30)  # back off hard on failure
            continue
        consecutive_fail = 0

        if not prices:
            empty += 1
        else:
            # Merge into any existing snapshot (preserves TPEX entries from report runs)
            merged = load(day)
            merged.update(prices)
            _cache_path(day).write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            ok += 1

        if i % 10 == 0 or i == len(todo):
            logger.info(f"Progress: {i}/{len(todo)} (saved {ok}, empty {empty}, failed {failed})")

        time.sleep(args.sleep + random.uniform(0, 1))

    logger.info(f"Done. Saved {ok}, empty {empty}, failed {failed}.")
    logger.info("Note: TPEX historical data not available — TPEX stocks accumulate going forward.")


if __name__ == "__main__":
    main()
