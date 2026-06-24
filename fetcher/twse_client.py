import logging
import time
from datetime import date as _date

import requests

from config import ENDPOINTS, FETCH_TIMEOUT, FETCH_RETRIES

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# Legacy STOCK_DAY_ALL returns array-of-arrays; map positional index → English field name
_STOCK_DAY_FIELDS = [
    "Code", "Name", "TradeVolume", "TradeValue",
    "OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice",
    "Change", "Transaction",
]


class FetchError(Exception):
    pass


def _get(url: str, params: dict = None, timeout: int = FETCH_TIMEOUT) -> list | dict:
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == FETCH_RETRIES - 1:
                raise FetchError(f"GET {url} failed after {FETCH_RETRIES} attempts: {e}") from e
            time.sleep(2 ** attempt)


def _greg_to_roc(greg8: str) -> str:
    """'20260508' → '1150508'"""
    if len(greg8) == 8:
        return str(int(greg8[:4]) - 1911) + greg8[4:]
    return greg8


def fetch_taiex_index(date_str: str = None) -> list[dict]:
    """
    MI_5MINS_HIST — 近5個交易日 OHLC。取最後一筆計算當日收盤指數與漲跌，
    回傳與 OpenAPI MI_INDEX 相同欄位的 list[dict]，供 index_stats.py 直接使用。
    """
    if date_str is None:
        date_str = _date.today().strftime("%Y%m%d")
    data = _get(ENDPOINTS["twse_index"], params={"response": "json", "date": date_str})
    if not isinstance(data, dict):
        raise FetchError(f"Unexpected MI_5MINS_HIST response type: {type(data)}")
    if data.get("stat") not in ("OK", "ok"):
        raise FetchError(f"MI_5MINS_HIST stat={data.get('stat')}: {data.get('msg', '')}")

    rows = data.get("data", [])
    if not rows:
        raise FetchError("MI_5MINS_HIST: no data rows")

    # rows[i] = [日期('115/05/08'), 開盤指數, 最高指數, 最低指數, 收盤指數]
    last   = rows[-1]
    close  = float(str(last[4]).replace(",", ""))
    prev   = float(str(rows[-2][4]).replace(",", "")) if len(rows) >= 2 else close

    change_pts = round(close - prev, 2)
    change_pct = round((change_pts / prev) * 100, 2) if prev else 0.0
    sign = "+" if change_pts >= 0 else "-"

    # "115/05/08" → "1150508"
    roc_date = last[0].replace("/", "")

    return [{
        "指數":       "發行量加權股價指數",
        "收盤指數":   str(close),
        "漲跌":       sign,
        "漲跌點數":   str(abs(change_pts)),
        "漲跌百分比": str(abs(change_pct)),
        "Date":       roc_date,
    }]


# STOCK_DAY_ALL CSV column order (TWSE now ignores response=json and serves CSV):
# 日期, 證券代號, 證券名稱, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數
_STOCK_DAY_CSV = [
    "Date", "Code", "Name", "TradeVolume", "TradeValue",
    "OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice",
    "Change", "Transaction",
]


def _parse_stock_day_csv(text: str) -> list[dict]:
    """Parse STOCK_DAY_ALL CSV body into the same dict shape as the JSON path."""
    import csv as _csv
    import io as _io

    result = []
    for row in _csv.reader(_io.StringIO(text)):
        if len(row) < len(_STOCK_DAY_CSV):
            continue
        code = str(row[1]).strip()
        if not code or not code[0].isdigit():   # skip header / non-data lines
            continue
        d = {k: row[i] for i, k in enumerate(_STOCK_DAY_CSV)}
        result.append(d)
    return result


def fetch_stock_day_all(date_str: str = None) -> list[dict]:
    """
    STOCK_DAY_ALL — 全部上市個股當日收盤資料。
    TWSE 已改為一律回傳 CSV（response=json 失效），本函式同時支援 JSON 與 CSV，
    回傳 list[dict]（Code, Name, ClosingPrice, Change ...）並附加 Date（ROC '1150508'）。
    """
    if date_str is None:
        date_str = _date.today().strftime("%Y%m%d")

    last_err = None
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(
                ENDPOINTS["twse_stock_all"],
                params={"response": "json", "date": date_str},
                headers=HEADERS, timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            body = resp.text.lstrip()

            # JSON path (in case TWSE reverts) ──────────────────────────────
            if body[:1] in "{[":
                data = resp.json()
                if not isinstance(data, dict) or data.get("stat") not in ("OK", "ok"):
                    raise FetchError(f"STOCK_DAY_ALL stat={data.get('stat') if isinstance(data, dict) else '?'}")
                roc_date = _greg_to_roc(data.get("date", ""))
                rows = data.get("data", [])
                result = []
                for row in rows:
                    if len(row) < len(_STOCK_DAY_FIELDS):
                        continue
                    d = {k: row[i] for i, k in enumerate(_STOCK_DAY_FIELDS)}
                    d["Date"] = roc_date
                    result.append(d)
                if not result:
                    raise FetchError("STOCK_DAY_ALL: no data rows (JSON)")
                return result

            # CSV path (current TWSE behaviour) ─────────────────────────────
            result = _parse_stock_day_csv(resp.text)
            if not result:
                raise FetchError("STOCK_DAY_ALL: no data rows (CSV)")
            return result

        except (requests.RequestException, FetchError, ValueError) as e:
            last_err = e
            if attempt < FETCH_RETRIES - 1:
                time.sleep(2 ** attempt)

    raise FetchError(f"STOCK_DAY_ALL failed after {FETCH_RETRIES} attempts: {last_err}")


def _fetch_legacy(endpoint_key: str, date_str: str = None) -> dict:
    """Fetch TWSE legacy JSON endpoint. Omitting date returns current-day data."""
    params = {"response": "json"}
    if date_str:
        params["date"] = date_str
    data = _get(ENDPOINTS[endpoint_key], params=params)
    if not isinstance(data, dict):
        raise FetchError(f"Unexpected {endpoint_key} response type: {type(data)}")
    if data.get("stat") not in ("OK", "ok"):
        raise FetchError(f"{endpoint_key} stat={data.get('stat')}: {data.get('msg', '')}")
    return data


def fetch_twt84u(date_str: str = None) -> dict[str, tuple[float, float]]:
    """
    TWT84U — 個股漲跌停參考價。
    date_str: YYYYMMDD of the trading day whose reference prices are needed.
    Returns {code: (limit_up_price, limit_down_price)}.
    Fields: [代號, 名稱, 漲停價, 開盤競價基準, 跌停價, ...]
    """
    time.sleep(0.3)   # slight delay to avoid rate-limit when fetched in parallel
    params = {"response": "json"}
    if date_str:
        params["date"] = date_str
    data = _get(ENDPOINTS["twse_twt84u"], params=params)
    if not isinstance(data, dict) or data.get("stat") not in ("OK", "ok"):
        raise FetchError(f"TWT84U stat={data.get('stat') if isinstance(data, dict) else '?'}")

    # Validate response date — after market close TWSE may flip to next-day reference prices.
    resp_date = str(data.get("date", "")).strip()
    if date_str and resp_date and resp_date != date_str:
        logger.warning(f"TWT84U date mismatch: requested {date_str}, got {resp_date} — skipping limit prices")
        return {}

    def pf(s):
        try:
            return float(str(s).replace(",", ""))
        except (ValueError, TypeError):
            return None

    result = {}
    for row in data.get("data", []):
        if len(row) < 5:
            continue
        code = str(row[0]).strip()
        lu = pf(row[2])
        ld = pf(row[4])
        if code and lu is not None and ld is not None:
            result[code] = (lu, ld)
    return result


def fetch_bfi82u() -> dict:
    """Listed 三大法人彙總 (aggregate by institution type) — BFI82U, current-day."""
    return _fetch_legacy("twse_bfi82u")


def fetch_t86() -> dict:
    """Listed 三大法人 by stock (外資/投信/自營商/合計) — T86, current-day.
    selectType=ALLBUT0999: 全部上市股票（不含權證、牛熊證、可展延牛熊證）
    """
    params = {"response": "json", "selectType": "ALLBUT0999"}
    data = _get(ENDPOINTS["twse_t86"], params=params)
    if not isinstance(data, dict):
        raise FetchError(f"Unexpected twse_t86 response type: {type(data)}")
    if data.get("stat") not in ("OK", "ok"):
        raise FetchError(f"twse_t86 stat={data.get('stat')}: {data.get('msg', '')}")
    return data


def fetch_fmtqik(date_str: str = None) -> dict:
    """
    FMTQIK — 大盤成交統計 (官方每日總計)。
    date_str: YYYYMMDD of any day in the target month.
    Returns dict with keys: date, trade_volume, trade_value, transaction, close, change_pts.
    成交金額/筆數 values are the OFFICIAL market-wide totals (all security types).
    """
    params = {"response": "json"}
    if date_str:
        params["date"] = date_str
    data = _get("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK", params=params)
    if not isinstance(data, dict) or data.get("stat") not in ("OK", "ok"):
        raise FetchError(f"FMTQIK stat={data.get('stat') if isinstance(data, dict) else '?'}")

    rows = data.get("data", [])
    if not rows:
        raise FetchError("FMTQIK: no data rows")

    # Target date in ROC slash format e.g. "115/05/13"
    if date_str and len(date_str) == 8:
        roc_year = int(date_str[:4]) - 1911
        target_roc = f"{roc_year}/{date_str[4:6]}/{date_str[6:8]}"
        row = next((r for r in rows if r[0] == target_roc), None)
        if row is None:
            raise FetchError(f"FMTQIK: date {target_roc} not found in response")
    else:
        row = rows[-1]  # latest available

    def pn(s):
        try:
            return float(str(s).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    return {
        "date":          row[0],
        "trade_volume":  pn(row[1]),
        "trade_value":   pn(row[2]),
        "transaction":   int(pn(row[3])),
        "close":         pn(row[4]),
        "change_pts":    pn(row[5]),
    }
