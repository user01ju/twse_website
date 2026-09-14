"""第 7 區塊：漲幅/跌幅 前100 (上市+上櫃合併) + 52 週新高/新低清單。"""
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


def enrich(data: dict, stocks: dict | None, flow: dict | None, top: int = 50) -> dict:
    """給漲跌幅前 100 補脈絡欄（距 52 週高、近 20 日、量比、法人今日淨額、型態），
    並從全市場挑出創 52 週新高 / 新低的清單（各前 top 檔，按法人今日淨額排）。

    stocks: sector_breadth.build()["stocks"]（{code: {pos52, r20, volr, nh, nl, name, sector}}）
    flow:   sector_flow.build()["by_code"]（{code: {pattern, net1, streak}}）
    兩者任一缺就只補得到的欄，不炸。
    """
    stocks = stocks or {}
    flow = flow or {}

    def ctx(code: str) -> dict:
        m, f = stocks.get(code, {}), flow.get(code, {})
        return {"pos52": m.get("pos52"), "r20": m.get("r20"), "volr": m.get("volr"),
                "net1": f.get("net1"), "pattern": f.get("pattern"), "streak": f.get("streak")}

    for key in ("gainers", "losers"):
        for s in data.get(key, ()):
            s.update(ctx(s["code"]))

    def pick(flag: str) -> list[dict]:
        rows = [dict(code=c, name=m.get("name", c), sector=m.get("sector", ""), r1=m.get("r1"), **ctx(c))
                for c, m in stocks.items() if m.get(flag)]
        rows.sort(key=lambda r: (r["net1"] or 0), reverse=(flag == "nh"))
        return rows[:top]

    data["new_high"] = pick("nh")
    data["new_low"] = pick("nl")
    data["new_high_total"] = sum(1 for m in stocks.values() if m.get("nh"))
    data["new_low_total"] = sum(1 for m in stocks.values() if m.get("nl"))
    return data
