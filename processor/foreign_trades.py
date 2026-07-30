"""Sections 7+10: 外資買賣超 by stock."""
from .utils import parse_num, shares_to_zhang, is_warrant, is_stock_code


def _from_twse_t86(t86_data: dict, twse_prices: dict) -> list[dict]:
    """
    T86 fields (by index): 0=代號, 1=名稱,
    2=外陸資買進, 3=外陸資賣出, 4=外陸資買賣超(不含自營),
    5=外資自營買進, 6=外資自營賣出, 7=外資自營買賣超
    """
    fields = t86_data.get("fields", [])
    rows   = t86_data.get("data", [])
    result = []
    for row in rows:
        try:
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue
            buy_shares  = parse_num(row[2])
            sell_shares = parse_num(row[3])
            net_shares  = parse_num(row[4])
            # Add foreign dealers
            if len(row) > 7:
                buy_shares  += parse_num(row[5])
                sell_shares += parse_num(row[6])
                net_shares  += parse_num(row[7])

            buy_zhang  = shares_to_zhang(buy_shares)
            sell_zhang = shares_to_zhang(sell_shares)
            net_zhang  = shares_to_zhang(net_shares)
            close_price = twse_prices.get(code, 0)

            result.append({
                "code":       code,
                "name":       name,
                "buy_zhang":  round(buy_zhang),
                "sell_zhang": round(sell_zhang),
                "net_zhang":  round(net_zhang),
                "net_yi":     net_zhang * 1000 * close_price / 1e8,
                "market":     "上市",
            })
        except Exception:
            continue
    return result


def _from_tpex_qfii(records: list[dict], tpex_prices: dict) -> list[dict]:
    """tpex_3insti_qfii_trading — amounts in 張."""
    result = []
    for r in records:
        try:
            code = str(r.get("SecuritiesCompanyCode", "")).strip()
            name = str(r.get("CompanyName", "")).strip()
            if not is_stock_code(code) or is_warrant(code, name):
                continue

            # Field names have inconsistent spacing — find by partial match
            buy_key  = next((k for k in r if "TotalBuy" in k.replace(" ", "")), None)
            sell_key = next((k for k in r if "TotalSell" in k.replace(" ", "") or "Total Sell" in k), None)
            diff_key = next((k for k in r if "Difference" in k.replace(" ", "")), None)

            buy_zhang  = parse_num(r.get(buy_key,  0)) if buy_key  else 0
            sell_zhang = parse_num(r.get(sell_key, 0)) if sell_key else 0
            net_zhang  = parse_num(r.get(diff_key, 0)) if diff_key else buy_zhang - sell_zhang
            close_price = tpex_prices.get(code, 0)

            result.append({
                "code":       code,
                "name":       name,
                "buy_zhang":  round(buy_zhang),
                "sell_zhang": round(sell_zhang),
                "net_zhang":  round(net_zhang),
                "net_yi":     net_zhang * 1000 * close_price / 1e8,
                "market":     "上櫃",
            })
        except Exception:
            continue
    return result


def build(
    twse_t86: dict,
    tpex_qfii: list[dict],
    twse_prices: dict,
    tpex_prices: dict,
    pcts: dict | None = None,
) -> dict:
    twse_list = _from_twse_t86(twse_t86, twse_prices)
    tpex_list = _from_tpex_qfii(tpex_qfii, tpex_prices)
    combined  = twse_list + tpex_list
    # 當日漲跌幅（無報價 → None，前端顯示 –）
    for s in combined:
        s["change_pct"] = (pcts or {}).get(s["code"])

    buy_super  = sorted([s for s in combined if s["net_zhang"] > 0],
                        key=lambda s: s["net_yi"], reverse=True)
    sell_super = sorted([s for s in combined if s["net_zhang"] < 0],
                        key=lambda s: s["net_yi"])

    ranked_by_amount = sorted(combined, key=lambda s: abs(s["net_yi"]), reverse=True)[:100]

    return {
        "buy_super":      buy_super[:100],
        "sell_super":     sell_super[:100],
        "ranked_by_amount": ranked_by_amount,
    }
