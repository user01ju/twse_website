"""Sections 5+6: 漲幅/跌幅 前100 (上市+上櫃合併)."""
from .utils import parse_num, change_pct, is_warrant, is_stock_code


def _vs_ref(code: str, close: float, change: float, ex_refs: dict) -> tuple[float, float]:
    """除息/減資/面額變更當日交易所的 Change 不可用（TWSE 給 'X'），
    改以除權息參考價為基準算漲跌與漲跌幅。回傳 (change, pct)。
    """
    ref = ex_refs.get(code)
    if ref and ref > 0:
        return close - ref, (close - ref) / ref * 100
    return change, change_pct(close, change)


def _normalize_twse(stocks: list[dict], ex_refs: dict | None = None) -> list[dict]:
    ex_refs = ex_refs or {}
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
            change, pct = _vs_ref(code, close, change, ex_refs)
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


def _normalize_tpex(stocks: list[dict], ex_refs: dict | None = None) -> list[dict]:
    ex_refs = ex_refs or {}
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
            change, pct = _vs_ref(code, close, change, ex_refs)
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


def build(twse_stocks: list[dict], tpex_stocks: list[dict], ex_refs: dict | None = None) -> dict:
    combined = _normalize_twse(twse_stocks, ex_refs) + _normalize_tpex(tpex_stocks, ex_refs)

    # Filter out unreasonably extreme values (suspended/auction stocks)
    combined = [s for s in combined if abs(s["change_pct"]) < 50]

    gainers = sorted(combined, key=lambda s: s["change_pct"], reverse=True)[:100]
    losers  = sorted(combined, key=lambda s: s["change_pct"])[:100]

    return {"gainers": gainers, "losers": losers}
