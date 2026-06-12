"""Backfill historical OHLC price cache for market trend indicators.

TWSE: Uses STOCK_DAY (per-stock monthly endpoint) — supports historical dates.
TPEX: No public historical endpoint found; TPEX data accumulates going forward.

Usage:
  python backfill_prices.py              # backfill last 252 trading days
  python backfill_prices.py --months 6  # only 6 months
  python backfill_prices.py --force      # overwrite existing cache files
  python backfill_prices.py --dry-run    # show what would be fetched

Rate limit: 3 concurrent workers, 0.4s sleep between requests per worker.
Estimated time: ~1000 stocks × N months / 3 workers ≈ 10-20 min for 12 months.
"""
import argparse
import logging
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from fetcher.market_calendar import is_trading_day, roc_to_date
from fetcher.price_cache import load, _path as _cache_path
from fetcher.twse_client import fetch_stock_day_all
from processor.utils import is_stock_code, parse_num

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TWSE_STOCK_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"


# ── Fetch helpers ──────────────────────────────────────────────────────────────

def _fetch_stock_day(code: str, date_str: str) -> list[tuple[date, float, float, float]]:
    """
    Fetch STOCK_DAY for one stock for the month containing date_str (YYYYMMDD).
    Returns list of (date, close, high, low) for each trading day in that month.
    """
    time.sleep(0.4)
    try:
        resp = requests.get(
            TWSE_STOCK_DAY,
            params={"stockNo": code, "date": date_str, "response": "json"},
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        d = resp.json()
        if d.get("stat") not in ("OK", "ok") or not d.get("data"):
            return []
        rows = d["data"]
        # row: [roc_date, volume, value, open, high, low, close, change, transactions, ...]
        # roc_date format: '115/05/04'
        result = []
        for row in rows:
            if len(row) < 7:
                continue
            try:
                roc_str = row[0].replace("/", "")  # '1150504'
                dt = roc_to_date(roc_str)
                h   = parse_num(row[4])
                lo  = parse_num(row[5])
                c   = parse_num(row[6])
                if c > 0:
                    result.append((dt, c, h or c, lo or c))
            except Exception:
                continue
        return result
    except Exception as e:
        logger.debug(f"STOCK_DAY {code} {date_str}: {e}")
        return []


def _get_trading_days_in_range(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _months_in_range(start: date, end: date) -> list[str]:
    """Return list of YYYYMMDD strings (first of each month) between start and end."""
    months = []
    y, m = start.year, start.month
    while date(y, m, 1) <= date(end.year, end.month, 1):
        months.append(f"{y}{m:02d}01")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return months


# ── Main backfill ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill OHLC price cache")
    parser.add_argument("--months", type=int, default=13,
                        help="Number of months to look back (default 13, covers 252+ trading days)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing cache files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without fetching")
    parser.add_argument("--workers", type=int, default=3,
                        help="Concurrent fetch workers (default 3)")
    args = parser.parse_args()

    today = date.today()
    start_approx = date(today.year, today.month, 1)
    for _ in range(args.months - 1):
        if start_approx.month == 1:
            start_approx = date(start_approx.year - 1, 12, 1)
        else:
            start_approx = date(start_approx.year, start_approx.month - 1, 1)

    logger.info(f"Backfill range: {start_approx.isoformat()} → {today.isoformat()}")
    logger.info(f"Workers: {args.workers}, Force: {args.force}")

    # Get list of trading days in range
    trading_days = _get_trading_days_in_range(start_approx, today)
    logger.info(f"Trading days in range: {len(trading_days)}")

    # Check which dates already have cache
    missing_dates = [d for d in trading_days if args.force or not _cache_path(d).exists()]
    existing = len(trading_days) - len(missing_dates)
    logger.info(f"Already cached: {existing}, Need to fetch: {len(missing_dates)}")

    if not missing_dates:
        logger.info("All dates already cached. Use --force to re-fetch.")
        return

    if args.dry_run:
        logger.info("Dry run — would fetch data for these dates:")
        for d in missing_dates[:10]:
            logger.info(f"  {d.isoformat()}")
        if len(missing_dates) > 10:
            logger.info(f"  ... and {len(missing_dates) - 10} more")
        return

    # ── Step 1: Get stock codes from today's TWSE data ──────────────────────
    logger.info("Fetching today's TWSE stock list...")
    try:
        twse_stocks = fetch_stock_day_all()
        codes = sorted({
            s.get("Code", "").strip()
            for s in twse_stocks
            if is_stock_code(s.get("Code", "").strip())
        })
        logger.info(f"TWSE stock codes: {len(codes)}")
    except Exception as e:
        logger.error(f"Failed to fetch TWSE stock list: {e}")
        sys.exit(1)

    # ── Step 2: Determine months to fetch ───────────────────────────────────
    months_needed = _months_in_range(start_approx, today)
    logger.info(f"Months to fetch: {len(months_needed)} ({months_needed[0]} → {months_needed[-1]})")

    # Build: month_str → [codes that need data for dates in that month]
    # (all codes, since we don't know which codes were listed which months)
    total_requests = len(codes) * len(months_needed)
    logger.info(f"Total STOCK_DAY requests: {total_requests}")
    est_minutes = total_requests / (args.workers * 2.5) / 60
    logger.info(f"Estimated time: ~{est_minutes:.0f} minutes at {args.workers} workers")

    # ── Step 3: Fetch all monthly data ──────────────────────────────────────
    # Accumulate: date → {code: (close, high, low)}
    daily_data: dict[date, dict[str, tuple]] = defaultdict(dict)

    # Populate from existing cache first (so --force still keeps other codes)
    for d in trading_days:
        existing_snap = load(d)
        if existing_snap:
            daily_data[d].update({k: (v["c"], v["h"], v["l"]) for k, v in existing_snap.items()})

    def fetch_one(code: str, month_str: str):
        rows = _fetch_stock_day(code, month_str)
        return code, rows

    done = 0
    tasks = [(code, month_str) for month_str in months_needed for code in codes]
    total = len(tasks)

    logger.info(f"Starting fetch ({total} requests)...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, code, month_str): (code, month_str)
                   for code, month_str in tasks}
        for future in as_completed(futures):
            code, rows = future.result()
            for dt, c, h, lo in rows:
                if dt in {d for d in trading_days}:
                    daily_data[dt][code] = (c, h, lo)
            done += 1
            if done % 100 == 0 or done == total:
                pct = done / total * 100
                logger.info(f"Progress: {done}/{total} ({pct:.0f}%) requests done")

    # ── Step 4: Save cache files ─────────────────────────────────────────────
    _cache_path(trading_days[0]).parent.mkdir(parents=True, exist_ok=True)

    saved = 0
    for d in trading_days:
        if not args.force and _cache_path(d).exists():
            continue
        snap = daily_data.get(d, {})
        if not snap:
            logger.debug(f"No data for {d.isoformat()}, skipping")
            continue

        import json
        payload = {code: {"c": c, "h": h, "l": lo} for code, (c, h, lo) in snap.items()}
        _cache_path(d).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        saved += 1

    logger.info(f"Done. Saved {saved} cache files.")
    logger.info("Note: TPEX historical data not available — TPEX stocks will accumulate going forward.")


if __name__ == "__main__":
    main()
