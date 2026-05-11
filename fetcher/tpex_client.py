import logging
import time
from datetime import date

import requests

from config import ENDPOINTS, FETCH_TIMEOUT, FETCH_RETRIES

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# tpex_mainborad_highlight requires these headers to bypass cache
HIGHLIGHT_HEADERS = {
    **HEADERS,
    "If-Modified-Since": "Mon, 26 Jul 1997 05:00:00 GMT",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class FetchError(Exception):
    pass


def _get(url: str, timeout: int = FETCH_TIMEOUT) -> list[dict]:
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            raise FetchError(f"Unexpected response type from {url}: {type(data)}")
        except requests.RequestException as e:
            if attempt == FETCH_RETRIES - 1:
                raise FetchError(f"GET {url} failed after {FETCH_RETRIES} attempts: {e}") from e
            time.sleep(2 ** attempt)


def _filter_date(records: list[dict], target: date, date_field: str = "Date") -> list[dict]:
    """Keep only records matching target date (ROC compact '1150507', slash '115/05/07', or ISO)."""
    roc_year = target.year - 1911
    roc_slash   = f"{roc_year}/{target.month:02d}/{target.day:02d}"
    roc_compact = f"{roc_year:03d}{target.month:02d}{target.day:02d}"
    iso         = target.isoformat()

    filtered = [r for r in records if r.get(date_field, "") in (roc_slash, roc_compact, iso)]
    if not filtered:
        logger.debug(f"No TPEX records for {roc_slash} in '{date_field}'; returning all {len(records)} records")
        return records
    return filtered


def get_tpex_data_date(records: list[dict], date_field: str = "Date") -> date | None:
    """Extract the actual data date from the first TPEX record."""
    from fetcher.market_calendar import roc_to_date
    if not records:
        return None
    raw = str(records[0].get(date_field, "")).strip()
    # compact: '1150507'
    if len(raw) == 7 and raw.isdigit():
        try:
            return roc_to_date(raw)
        except Exception:
            pass
    # slash: '115/05/07'
    if "/" in raw:
        try:
            return roc_to_date(raw.replace("/", ""))
        except Exception:
            pass
    # ISO: '2026-05-07'
    try:
        from datetime import date as _date
        return _date.fromisoformat(raw)
    except Exception:
        pass
    return None


def fetch_highlight(target: date = None) -> dict:
    """
    TPEX tpex_mainborad_highlight — 上櫃大盤漲跌統計 (official breadth numbers).
    Returns the single today record as a dict, or {} on failure.
    Fields include: LimitUpCompanyNumbers, LimitDownCompanyNumbers,
                    PriceRiseCompanyNumbers, PriceDeclineCompanyNumbers,
                    PriceFlatCompanyNumbers, ListedCompanyNumbers, CloseIndex, IndexChange.
    """
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(ENDPOINTS["tpex_highlight"], headers=HIGHLIGHT_HEADERS,
                                timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                records = _filter_date(data, target or date.today())
                return records[0] if records else {}
            return {}
        except requests.RequestException as e:
            if attempt == FETCH_RETRIES - 1:
                raise FetchError(f"tpex_highlight failed: {e}") from e
            time.sleep(2 ** attempt)


def fetch_daily_quotes(target: date = None) -> list[dict]:
    """TPEX daily mainboard close quotes (上櫃個股)."""
    records = _get(ENDPOINTS["tpex_daily"])
    return _filter_date(records, target or date.today())


def fetch_3insti_summary(target: date = None) -> list[dict]:
    """TPEX 上櫃三大法人彙總."""
    records = _get(ENDPOINTS["tpex_3insti_sum"])
    return _filter_date(records, target or date.today())


def fetch_3insti_qfii(target: date = None) -> list[dict]:
    """TPEX 上櫃外資買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_qfii"])
    return _filter_date(records, target or date.today())


def fetch_3insti_trust(target: date = None) -> list[dict]:
    """TPEX 上櫃投信買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_trust"])
    return _filter_date(records, target or date.today())


def fetch_3insti_all(target: date = None) -> list[dict]:
    """TPEX 上櫃三大法人合計買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_all"])
    return _filter_date(records, target or date.today())


def fetch_esb_quotes(target: date = None) -> list[dict]:
    """TPEX 興櫃股票統計."""
    records = _get(ENDPOINTS["tpex_esb"])
    return _filter_date(records, target or date.today())
