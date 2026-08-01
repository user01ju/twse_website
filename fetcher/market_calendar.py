import json
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
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


@lru_cache(maxsize=None)
def _load_holidays(year: int) -> set[str]:
    """休市日集合 (ISO date strings)。

    holidaySchedule 的雷：
      - 日期欄是西元 ISO `YYYY-MM-DD`，不是 ROC。
      - `year` 參數收得下但**完全被忽略**，永遠回當年度表 → 必須自行按年份
        過濾，否則會把今年的假日寫進去年的 cache（跨年度只能改用外部來源）。
      - 表中混有「交易日」列（國曆新年開始交易日/農曆春節前最後交易日…），
        那些是有交易的，不能算休市。
      - 解出 0 筆視為失敗，不寫 cache，免得空集合永久化。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"holidays_{year}.json"

    if cache_file.exists():
        try:
            cached = set(json.loads(cache_file.read_text(encoding="utf-8")))
            if cached:
                return cached
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
            try:
                d = date.fromisoformat(str(row[0]).strip())
            except Exception:
                continue
            if d.year != year:
                continue
            name = str(row[1]).strip() if len(row) > 1 else ""
            if "交易日" in name:      # 開始/最後交易日 — 有交易，不是休市
                continue
            holidays.add(d.isoformat())

        if not holidays:
            logger.warning(f"holidaySchedule returned no holidays for {year} — not caching")
            return set()

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
