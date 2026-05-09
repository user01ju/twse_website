"""Fetch TWSE sector index performance (MI_INDEX?type=IND)."""
import re
import logging

from fetcher.twse_client import _get
from config import TWSE_LEGACY_BASE

logger = logging.getLogger(__name__)

_URL = f"{TWSE_LEGACY_BASE}/exchangeReport/MI_INDEX"

# Keywords that mark non-pure-sector rows (broad market / ETF strategy indices)
_EXCLUDE_KEYWORDS = (
    "兩倍", "反向", "槓桿", "IPO", "高薪", "未含", "臺灣50", "臺灣中型",
    "高殖利", "發行量加權", "寶島", "臺灣就業", "臺灣發達", "高股息",
    "電子類兩倍", "電子類反向",
)


def _short_name(name: str) -> str:
    """Strip verbose suffixes to get a compact sector label."""
    return (name
            .replace("類指數", "")
            .replace("指數", "")
            .replace("上市", "")
            .replace("臺灣", "")
            .strip())


def fetch_sector_performance() -> list[dict]:
    """
    Returns list of dicts sorted by change_pct descending:
      {name, name_short, close, change_pct, change_pts}
    Only pure sector indices (類指數) are included.
    """
    data = _get(_URL, params={"response": "json", "type": "IND"})
    if not isinstance(data, dict) or data.get("stat") not in ("OK", "ok"):
        raise RuntimeError(f"MI_INDEX IND stat={data.get('stat') if isinstance(data, dict) else '?'}")

    tables = data.get("tables", [])
    # Table 0 = 上市類股指數
    if not tables:
        raise RuntimeError("MI_INDEX IND: no tables returned")

    rows = []
    for row in tables[0].get("data", []):
        name = str(row[0]).strip()
        pct_str = str(row[4]).strip()

        # Only keep rows ending with 類指數 (pure sector), skip others
        if "類指數" not in name:
            continue
        if any(kw in name for kw in _EXCLUDE_KEYWORDS):
            continue
        if pct_str in ("--", "", "N/A"):
            continue

        try:
            pct = float(pct_str)
            pts = float(str(row[3]).replace(",", ""))
            close = float(str(row[1]).replace(",", ""))
        except (ValueError, TypeError):
            continue

        rows.append({
            "name":       name,
            "name_short": _short_name(name),
            "close":      close,
            "change_pct": pct,
            "change_pts": pts if pct >= 0 else -abs(pts),
        })

    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    logger.info(f"Fetched {len(rows)} sector indices")
    return rows
