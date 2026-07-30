"""Daily price cache: persist {code: {close, high, low}} per trading date.

Files: output/data/prices/YYYY-MM-DD.json — lives inside output/ so it deploys
to gh-pages and gets restored on the next CI run (runners are ephemeral).
Format: {"2330": {"c": 925.0, "h": 930.0, "l": 920.0}, ...}
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from config import OUTPUT_DIR
from processor.utils import parse_num, is_warrant, is_stock_code

logger = logging.getLogger(__name__)

_PRICES_DIR = OUTPUT_DIR / "data" / "prices"


def _path(d: date) -> Path:
    return _PRICES_DIR / f"{d.isoformat()}.json"


def save(d: date, twse_stocks: list[dict], tpex_stocks: list[dict]) -> None:
    """Extract close/high/low from raw API rows and write to cache."""
    _PRICES_DIR.mkdir(parents=True, exist_ok=True)
    prices = {}

    for s in twse_stocks:
        try:
            code = str(s.get("Code", "")).strip()
            if not is_stock_code(code):
                continue
            c = parse_num(s.get("ClosingPrice", 0))
            h = parse_num(s.get("HighestPrice", 0))
            lo = parse_num(s.get("LowestPrice", 0))
            if c > 0:
                prices[code] = {"c": c, "h": h, "l": lo}
        except Exception:
            continue

    for s in tpex_stocks:
        try:
            code = str(s.get("SecuritiesCompanyCode", "")).strip()
            name = str(s.get("CompanyName", "")).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            c = parse_num(s.get("Close", 0))
            h = parse_num(s.get("High", 0))
            lo = parse_num(s.get("Low", 0))
            if c > 0:
                prices[code] = {"c": c, "h": h, "l": lo}
        except Exception:
            continue

    p = _path(d)
    p.write_text(json.dumps(prices, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Price cache saved: {p} ({len(prices)} stocks)")


def load(d: date) -> dict[str, dict]:
    """Load price dict for a single date. Returns {} if not found."""
    p = _path(d)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Price cache load failed {p}: {e}")
        return {}


def window_coverage(window: list[tuple[date, dict]], end: date) -> dict:
    """窗口完整度：實得快照數 / 期望交易日數 → {got, expected, pct}。

    期望值用「工作日數」近似（不扣國定假日），所以**完整**的 cache 大約落在
    93~97% 而不是 100%。存在的意義：cache 缺一大塊時 load_window 會安靜地
    往更早的日期湊滿天數，算出來的 20MA / 52 週新高低看起來合理但是錯的
    （拿今天的價比兩個月前的均價）。用這個比率把它變成可見的訊號。
    """
    if not window:
        return {"got": 0, "expected": 0, "pct": 0.0}
    start = window[0][0]
    expected = sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    )
    got = len(window)
    return {
        "got":      got,
        "expected": expected,
        "pct":      round(got / expected * 100, 1) if expected else 0.0,
    }


def load_window(end: date, days: int) -> list[tuple[date, dict]]:
    """
    Return up to `days` cached snapshots ending at `end` (inclusive), oldest first.
    Only dates that have a cache file are included.
    """
    result = []
    d = end
    for _ in range(days * 2):  # scan up to 2x calendar days to cover weekends/holidays
        if len(result) >= days:
            break
        data = load(d)
        if data:
            result.append((d, data))
        d -= timedelta(days=1)
    result.reverse()
    return result
