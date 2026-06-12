import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from config import ENDPOINTS, CACHE_DIR

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def roc_to_date(roc_str: str) -> date:
    """Convert ROC date string '1150506' to date(2026, 5, 6)."""
    s = roc_str.strip()
    year  = int(s[:3]) + 1911
    month = int(s[3:5])
    day   = int(s[5:7])
    return date(year, month, day)


def date_to_roc(d: date) -> str:
    """Convert date(2026, 5, 6) to ROC string '1150506'."""
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def _load_holidays(year: int) -> set[str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"holidays_{year}.json"

    if cache_file.exists():
        try:
            return set(json.loads(cache_file.read_text(encoding="utf-8")))
        except Exception:
            pass

    try:
        resp = requests.get(
            ENDPOINTS["twse_holidays"],
            params={"response": "json", "year": str(year)},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        holidays = set()
        for row in data.get("data", []):
            date_str = row[0].strip()
            if len(date_str) == 7:
                try:
                    d = roc_to_date(date_str)
                    holidays.add(d.isoformat())
                except Exception:
                    pass
        cache_file.write_text(json.dumps(sorted(holidays)), encoding="utf-8")
        return holidays
    except Exception as e:
        logger.warning(f"Failed to fetch holidays for {year}: {e}")
        return set()


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    holidays = _load_holidays(d.year)
    return d.isoformat() not in holidays


def get_latest_trading_day() -> date:
    """Return most recent trading day, anchored to Taiwan time (CI runs in UTC)."""
    today_tw = datetime.now(ZoneInfo("Asia/Taipei")).date()
    d = today_tw
    for _ in range(10):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return today_tw
