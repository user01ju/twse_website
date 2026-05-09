"""自營商買賣超 by stock (T86 row[11] + TPEX Dealers-Difference)."""
from .utils import parse_num, shares_to_zhang, is_warrant, is_stock_code


def _from_twse_t86(t86_data: dict, twse_prices: dict) -> list[dict]:
    """T86 row[11] = 自營商合計買賣超股數 (自行+避險)."""
    rows = t86_data.get("data", [])
    result = []
    for row in rows:
        try:
            if len(row) < 12:
                continue
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            net_zhang = shares_to_zhang(parse_num(row[11]))
            if net_zhang == 0:
                continue
            close_price = twse_prices.get(code, 0)
            result.append({
                "code":      code,
                "name":      name,
                "net_zhang": round(net_zhang),
                "net_yi":    net_zhang * 1000 * close_price / 1e8,
                "market":    "上市",
            })
        except Exception:
            continue
    return result


def _from_tpex_all(records: list[dict], tpex_prices: dict) -> list[dict]:
    """tpex_3insti_daily_trading — Dealers-Difference in shares."""
    result = []
    for r in records:
        try:
            code = str(r.get("SecuritiesCompanyCode", "")).strip()
            name = str(r.get("CompanyName", "")).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            d_key = next((k for k in r if k.strip() == "Dealers-Difference"), None)
            if not d_key:
                continue
            net_zhang = shares_to_zhang(parse_num(r.get(d_key, 0)))
            if net_zhang == 0:
                continue
            close_price = tpex_prices.get(code, 0)
            result.append({
                "code":      code,
                "name":      name,
                "net_zhang": round(net_zhang),
                "net_yi":    net_zhang * 1000 * close_price / 1e8,
                "market":    "上櫃",
            })
        except Exception:
            continue
    return result


def build(twse_t86: dict, tpex_all: list[dict],
          twse_prices: dict, tpex_prices: dict) -> dict:
    combined = _from_twse_t86(twse_t86, twse_prices) + _from_tpex_all(tpex_all, tpex_prices)
    buy_super  = sorted([s for s in combined if s["net_zhang"] > 0],
                        key=lambda s: s["net_yi"], reverse=True)
    sell_super = sorted([s for s in combined if s["net_zhang"] < 0],
                        key=lambda s: s["net_yi"])
    return {
        "buy_super":  buy_super[:100],
        "sell_super": sell_super[:100],
    }
