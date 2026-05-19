"""Section 2: 市場統計 (上漲/下跌/持平/漲停/跌停)."""
from .utils import parse_num, price_status, is_warrant, change_pct as _change_pct

# 漲跌幅分布區間 (label, lo_inclusive, hi_exclusive); None = unbounded
_DIST_BINS = [
    ("<-10",   None, -10),
    ("-10~-8",  -10,  -8),
    ("-8~-6",    -8,  -6),
    ("-6~-4",    -6,  -4),
    ("-4~-2",    -4,  -2),
    ("-2~0",     -2,   0),
    ("0~2",       0,   2),
    ("2~4",       2,   4),
    ("4~6",       4,   6),
    ("6~8",       6,   8),
    ("8~10",      8,  10),
    (">10",      10, None),
]


def _count(stocks: list[dict], close_field: str, change_field: str,
           code_field: str = "", name_field: str = "",
           limit_prices: dict = None) -> dict:
    """
    limit_prices: {code: (limit_up_price, limit_down_price)} from TWT84U (fetched with trading date).
    When provided, use official limit prices only — no formula fallback.
    Stocks not in limit_prices (e.g. TPEX) fall back to price_status() heuristic.
    """
    counts = {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0, "total": 0}
    for s in stocks:
        try:
            code = str(s.get(code_field, "")).strip() if code_field else ""
            name = str(s.get(name_field, "")).strip() if name_field else ""
            if code_field and is_warrant(code, name):
                continue
            close  = parse_num(s.get(close_field, 0))
            change = parse_num(str(s.get(change_field, "0")).replace(",", "").strip())
            if close <= 0:
                continue
            counts["total"] += 1

            # Use official limit prices when available (TWSE TWT84U).
            # Note: TWT84U may return *next* trading day reference prices after
            # market close / on weekends, in which case the exact comparison
            # won't match today's closing prices.  Fall back to the heuristic
            # whenever the TWT84U price doesn't produce a hit so we still
            # detect genuine limit-up/down stocks via the ±10 % rule.
            if limit_prices and code in limit_prices:
                lu, ld = limit_prices[code]
                if abs(close - lu) < 0.005:
                    status = "limit_up"
                elif abs(close - ld) < 0.005:
                    status = "limit_down"
                elif change > 0:
                    status = "up"
                elif change < 0:
                    status = "down"
                else:
                    status = "flat"
            else:
                status = price_status(close, change)

            if status == "limit_up":
                counts["limit_up"] += 1
                counts["up"] += 1
            elif status == "limit_down":
                counts["limit_down"] += 1
                counts["down"] += 1
            elif status == "up":
                counts["up"] += 1
            elif status == "down":
                counts["down"] += 1
            else:
                counts["flat"] += 1
        except Exception:
            continue

    total = counts["total"]
    for k in ("up", "down", "flat", "limit_up", "limit_down"):
        counts[f"{k}_pct"] = round(counts[k] / total * 100, 1) if total else 0.0

    return counts


def _count_esb(stocks: list[dict]) -> dict:
    """興櫃 uses LatestPrice vs PreviousAveragePrice."""
    counts = {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0, "total": 0}
    for s in stocks:
        try:
            latest = parse_num(s.get("LatestPrice", 0))
            prev   = parse_num(s.get("PreviousAveragePrice", 0))
            if latest <= 0 or prev <= 0:
                continue
            counts["total"] += 1
            change = latest - prev
            if change > 0:
                counts["up"] += 1
            elif change < 0:
                counts["down"] += 1
            else:
                counts["flat"] += 1
        except Exception:
            continue

    total = counts["total"]
    for k in ("up", "down", "flat", "limit_up", "limit_down"):
        counts[f"{k}_pct"] = round(counts[k] / total * 100, 1) if total else 0.0

    return counts


def _from_tpex_highlight(h: dict) -> dict:
    """
    Build TPEX breadth counts directly from tpex_mainborad_highlight official data.
    PriceRise/PriceDecline already include limit_up/limit_down respectively.
    """
    def pi(key): return int(parse_num(h.get(key, 0)))

    up       = pi("PriceRiseCompanyNumbers")
    down     = pi("PriceDeclineCompanyNumbers")
    flat     = pi("PriceFlatCompanyNumbers")
    limit_up = pi("LimitUpCompanyNumbers")
    limit_dn = pi("LimitDownCompanyNumbers")
    total    = pi("ListedCompanyNumbers")

    counts = {
        "up": up, "down": down, "flat": flat,
        "limit_up": limit_up, "limit_down": limit_dn,
        "total": total,
    }
    for k in ("up", "down", "flat", "limit_up", "limit_down"):
        counts[f"{k}_pct"] = round(counts[k] / total * 100, 1) if total else 0.0
    return counts


def _distribution(twse_stocks: list[dict], tpex_stocks: list[dict]) -> dict:
    """Combined TWSE+TPEX change_pct distribution across 12 bins."""
    pcts = []
    for s in twse_stocks:
        try:
            code = str(s.get("Code", "")).strip()
            if is_warrant(code, ""):
                continue
            close  = parse_num(s.get("ClosingPrice", 0))
            change = parse_num(str(s.get("Change", "0")).replace(",", ""))
            if close > 0:
                pcts.append(_change_pct(close, change))
        except Exception:
            pass
    for s in tpex_stocks:
        try:
            code = str(s.get("SecuritiesCompanyCode", "")).strip()
            name = str(s.get("CompanyName", "")).strip()
            if is_warrant(code, name):
                continue
            close  = parse_num(s.get("Close", 0))
            change = parse_num(str(s.get("Change", "0")))
            if close > 0:
                pcts.append(_change_pct(close, change))
        except Exception:
            pass

    bins = {label: 0 for label, *_ in _DIST_BINS}
    for p in pcts:
        for label, lo, hi in _DIST_BINS:
            if (lo is None or p >= lo) and (hi is None or p < hi):
                bins[label] += 1
                break

    return {
        "labels": [b[0] for b in _DIST_BINS],
        "counts": [bins[b[0]] for b in _DIST_BINS],
    }


def build(twse_stocks: list[dict], tpex_stocks: list[dict], esb_stocks: list[dict],
          twt84u_limits: dict = None, tpex_highlight: dict = None) -> dict:
    # TPEX: use official highlight data when available, fall back to per-stock computation
    if tpex_highlight:
        tpex_counts = _from_tpex_highlight(tpex_highlight)
    else:
        tpex_counts = _count(tpex_stocks, "Close", "Change",
                             code_field="SecuritiesCompanyCode", name_field="CompanyName")

    return {
        "twse": _count(twse_stocks, "ClosingPrice", "Change",
                       code_field="Code", limit_prices=twt84u_limits),
        "tpex": tpex_counts,
        "esb":  _count_esb(esb_stocks),
        "distribution": _distribution(twse_stocks, tpex_stocks),
    }
