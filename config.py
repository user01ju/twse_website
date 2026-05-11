import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env", override=True)

TPEX_OPENAPI_BASE = "https://www.tpex.org.tw/openapi/v1"
TWSE_LEGACY_BASE  = "https://www.twse.com.tw"

ENDPOINTS = {
    # MI_5MINS_HIST: 近5個交易日 OHLC，用最後一筆取當日收盤指數與漲跌
    "twse_index":       f"{TWSE_LEGACY_BASE}/indicesReport/MI_5MINS_HIST",
    "twse_stock_all":   f"{TWSE_LEGACY_BASE}/exchangeReport/STOCK_DAY_ALL",
    "twse_bfi82u":      f"{TWSE_LEGACY_BASE}/fund/BFI82U",   # 上市三大法人彙總
    "twse_t86":         f"{TWSE_LEGACY_BASE}/fund/T86",       # 上市三大法人 by stock
    "twse_twt84u":      f"{TWSE_LEGACY_BASE}/exchangeReport/TWT84U",  # 漲跌停參考價
    "twse_holidays":    f"{TWSE_LEGACY_BASE}/holidaySchedule/holidaySchedule",
    "tpex_daily":       f"{TPEX_OPENAPI_BASE}/tpex_mainboard_daily_close_quotes",
    "tpex_highlight":   f"{TPEX_OPENAPI_BASE}/tpex_mainborad_highlight",  # note: typo in TPEX API name
    "tpex_3insti_sum":  f"{TPEX_OPENAPI_BASE}/tpex_3insti_summary",
    "tpex_3insti_qfii": f"{TPEX_OPENAPI_BASE}/tpex_3insti_qfii_trading",
    "tpex_3insti_trust":f"{TPEX_OPENAPI_BASE}/tpex_3insti_trading",
    "tpex_3insti_all":  f"{TPEX_OPENAPI_BASE}/tpex_3insti_daily_trading",
    "tpex_esb":         f"{TPEX_OPENAPI_BASE}/tpex_esb_latest_statistics",
}

OUTPUT_DIR    = BASE_DIR / "output"
REPORTS_DIR   = OUTPUT_DIR / "reports"
STATIC_SRC    = BASE_DIR / "static"
STATIC_OUT    = OUTPUT_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
CACHE_DIR     = BASE_DIR / ".cache"

FETCH_TIMEOUT    = 30
FETCH_RETRIES    = 3
FETCH_HOUR       = 15
FETCH_MINUTE     = 5
FORCE_REBUILD    = os.environ.get("FORCE_REBUILD", "").lower() in ("1", "true", "yes")
