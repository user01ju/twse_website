"""Section 9: 三大法人合計買賣超 by stock."""
from .utils import parse_num, shares_to_zhang, is_warrant, is_stock_code


def _from_twse_t86(t86_data: dict, twse_prices: dict) -> list[dict]:
    """T86 last column (index 18): 三大法人買賣超股數."""
    rows = t86_data.get("data", [])
    result = []
    for row in rows:
        try:
            if len(row) < 19:
                continue
            code      = str(row[0]).strip()
            name      = str(row[1]).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            net_shares = parse_num(row[18])
            if net_shares == 0:
                continue

            net_zhang   = shares_to_zhang(net_shares)
            close_price = twse_prices.get(code, 0)

            # Sub-components. Foreign = 外陸資(4) + 外資自營商(7) so that
            # foreign + trust + dealer == row[18] (三大法人合計) exactly.
            foreign_shares = parse_num(row[4]) + (parse_num(row[7]) if len(row) > 7 else 0)
            foreign_net = shares_to_zhang(foreign_shares)
            trust_net   = shares_to_zhang(parse_num(row[10])) if len(row) > 10 else 0
            dealer_net  = shares_to_zhang(parse_num(row[11])) if len(row) > 11 else 0

            result.append({
                "code":        code,
                "name":        name,
                "net_zhang":   round(net_zhang),
                "net_yi":      net_zhang * 1000 * close_price / 1e8,
                "foreign_net": round(foreign_net),
                "trust_net":   round(trust_net),
                "dealer_net":  round(dealer_net),
                "market":      "上市",
            })
        except Exception:
            continue
    return result


def _from_tpex_all(records: list[dict], tpex_prices: dict) -> list[dict]:
    """tpex_3insti_daily_trading — TotalDifference field in 張."""
    result = []
    for r in records:
        try:
            code      = str(r.get("SecuritiesCompanyCode", "")).strip()
            name      = str(r.get("CompanyName", "")).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            # TotalDifference is in shares (個), divide by 1000 to get 張
            net_zhang = shares_to_zhang(parse_num(r.get("TotalDifference", 0)))
            if net_zhang == 0:
                continue

            close_price = tpex_prices.get(code, 0)

            # Foreign net — field name has spaces/variants
            f_key = next((k for k in r if "ForeignInvestors" in k.replace(" ", "") and "Difference" in k), None)
            t_key = next((k for k in r if "Investment" in k and "Difference" in k), None)
            d_key = next((k for k in r if k.strip() == "Dealers-Difference"), None)

            result.append({
                "code":        code,
                "name":        name,
                "net_zhang":   round(net_zhang),
                "net_yi":      net_zhang * 1000 * close_price / 1e8,
                # sub-components are also in shares — convert to 張
                "foreign_net": round(shares_to_zhang(parse_num(r.get(f_key, 0)))) if f_key else 0,
                "trust_net":   round(shares_to_zhang(parse_num(r.get(t_key, 0)))) if t_key else 0,
                "dealer_net":  round(shares_to_zhang(parse_num(r.get(d_key, 0)))) if d_key else 0,
                "market":      "上櫃",
            })
        except Exception:
            continue
    return result


def build(
    twse_t86: dict,
    tpex_all: list[dict],
    twse_prices: dict,
    tpex_prices: dict,
) -> dict:
    twse_list = _from_twse_t86(twse_t86, twse_prices)
    tpex_list = _from_tpex_all(tpex_all, tpex_prices)
    combined  = twse_list + tpex_list

    buy_super  = sorted([s for s in combined if s["net_zhang"] > 0],
                        key=lambda s: s["net_yi"], reverse=True)
    sell_super = sorted([s for s in combined if s["net_zhang"] < 0],
                        key=lambda s: s["net_yi"])

    return {
        "buy_super":  buy_super[:100],
        "sell_super": sell_super[:100],
    }
