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


def _filter_today(records: list[dict], date_field: str = "Date") -> list[dict]:
    """Keep only records matching today's ROC date (e.g. '115/05/07' or '1150507')."""
    today = date.today()
    roc_year = today.year - 1911
    # TPEX uses format '115/05/07'
    roc_slash = f"{roc_year}/{today.month:02d}/{today.day:02d}"
    # Also match compact form '1150507'
    roc_compact = f"{roc_year:03d}{today.month:02d}{today.day:02d}"

    filtered = [r for r in records if r.get(date_field, "") in (roc_slash, roc_compact, today.isoformat())]
    if not filtered:
        logger.debug(f"No records for today ({roc_slash}) in {date_field}; returning all {len(records)} records")
        return records
    return filtered


def fetch_highlight() -> dict:
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
                records = _filter_today(data)
                return records[0] if records else {}
            return {}
        except requests.RequestException as e:
            if attempt == FETCH_RETRIES - 1:
                raise FetchError(f"tpex_highlight failed: {e}") from e
            time.sleep(2 ** attempt)


def fetch_daily_quotes() -> list[dict]:
    """TPEX daily mainboard close quotes (上櫃個股)."""
    records = _get(ENDPOINTS["tpex_daily"])
    return _filter_today(records)


def fetch_3insti_summary() -> list[dict]:
    """TPEX 上櫃三大法人彙總."""
    records = _get(ENDPOINTS["tpex_3insti_sum"])
    return _filter_today(records)


def fetch_3insti_qfii() -> list[dict]:
    """TPEX 上櫃外資買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_qfii"])
    return _filter_today(records)


def fetch_3insti_trust() -> list[dict]:
    """TPEX 上櫃投信買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_trust"])
    return _filter_today(records)


def fetch_3insti_all() -> list[dict]:
    """TPEX 上櫃三大法人合計買賣超 by stock."""
    records = _get(ENDPOINTS["tpex_3insti_all"])
    return _filter_today(records)


def fetch_esb_quotes() -> list[dict]:
    """TPEX 興櫃股票統計."""
    records = _get(ENDPOINTS["tpex_esb"])
    return _filter_today(records)
