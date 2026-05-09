"""Sections 5+6: 漲幅/跌幅 前100 (上市+上櫃合併)."""
from .utils import parse_num, change_pct, is_warrant, is_stock_code


def _normalize_twse(stocks: list[dict]) -> list[dict]:
    result = []
    for s in stocks:
        try:
            code = s.get("Code", "").strip()
            if not is_stock_code(code):
                continue
            close  = parse_num(s.get("ClosingPrice", 0))
            change = parse_num(s.get("Change", 0))
            volume = parse_num(s.get("TradeVolume", 0))
            if close <= 0 or volume <= 0:
                continue
            pct = change_pct(close, change)
            result.append({
                "code":       code,
                "name":       s.get("Name", "").strip(),
                "close":      close,
                "change":     change,
                "change_pct": pct,
                "volume_zhang": round(volume / 1000),
                "market":     "上市",
            })
        except Exception:
            continue
    return result


def _normalize_tpex(stocks: list[dict]) -> list[dict]:
    result = []
    for s in stocks:
        try:
            code = str(s.get("SecuritiesCompanyCode", "")).strip()
            name = str(s.get("CompanyName", "")).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            close  = parse_num(s.get("Close", 0))
            # TPEX Change has sign prefix like "+0.85" or "-0.50" or "0.00"
            change = parse_num(s.get("Change", 0))
            volume = parse_num(s.get("TradingShares", 0))
            if close <= 0 or volume <= 0:
                continue
            pct = change_pct(close, change)
            result.append({
                "code":         s.get("SecuritiesCompanyCode", "").strip(),
                "name":         s.get("CompanyName", "").strip(),
                "close":        close,
                "change":       change,
                "change_pct":   pct,
                "volume_zhang": round(volume / 1000),
                "market":       "上櫃",
            })
        except Exception:
            continue
    return result


def build(twse_stocks: list[dict], tpex_stocks: list[dict]) -> dict:
    combined = _normalize_twse(twse_stocks) + _normalize_tpex(tpex_stocks)

    # Filter out unreasonably extreme values (suspended/auction stocks)
    combined = [s for s in combined if abs(s["change_pct"]) < 50]

    gainers = sorted(combined, key=lambda s: s["change_pct"], reverse=True)[:100]
    losers  = sorted(combined, key=lambda s: s["change_pct"])[:100]

    return {"gainers": gainers, "losers": losers}
