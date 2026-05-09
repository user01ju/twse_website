"""Sections 3+4: 三大法人彙總 (上市 + 上櫃)."""
from .utils import parse_num, nt_to_yi

# Display order for TWSE (BFI82U row names, exactly as they appear in the API)
_TWSE_ROW_ORDER = [
    "自營商(自行買賣)",
    "自營商(避險)",
    "投信",
    "外資及陸資(不含外資自營商)",
    "外資自營商",
    "合計",
]

# TPEX investor names → display label (strip leading ideographic space 　)
_TPEX_ROW_ORDER = [
    ("自營商(自行買賣)",            "自營商(自行買賣)"),
    ("自營商(避險)",                "自營商(避險)"),
    ("投信",                        "投信"),
    ("外資及陸資(不含自營商)",      "外資及陸資(不含自營商)"),
    ("外資自營商",                  "外資自營商"),
    ("三大法人合計",                "合計"),
]


def _to_yi(row, idx):
    return nt_to_yi(parse_num(row[idx])) if len(row) > idx else 0.0


def _parse_twse_bfi82u(data: dict) -> list[dict]:
    """Return rows in the order defined by _TWSE_ROW_ORDER."""
    rows = data.get("data", [])
    # Build lookup: stripped name → row
    lookup = {str(r[0]).strip(): r for r in rows if r}

    result = []
    for label in _TWSE_ROW_ORDER:
        row = lookup.get(label)
        if row:
            result.append({
                "name":     label,
                "buy_yi":   _to_yi(row, 1),
                "sell_yi":  _to_yi(row, 2),
                "net_yi":   _to_yi(row, 3),
                "is_total": label == "合計",
            })
    return result


def _parse_tpex_summary(records: list[dict]) -> list[dict]:
    """Return rows in the order defined by _TPEX_ROW_ORDER."""
    # Build lookup: strip leading whitespace and trailing * from Investor name
    lookup = {r.get("Investor", "").strip().rstrip("*"): r for r in records}

    result = []
    for api_name, display_label in _TPEX_ROW_ORDER:
        r = lookup.get(api_name)
        if r:
            result.append({
                "name":     display_label,
                "buy_yi":   nt_to_yi(parse_num(r.get("PurchaseAmount", 0))),
                "sell_yi":  nt_to_yi(parse_num(r.get("SaleAmount", 0))),
                "net_yi":   nt_to_yi(parse_num(r.get("Net", 0))),
                "is_total": display_label == "合計",
            })
    return result


def build(twse_bfi82u: dict, tpex_summary: list[dict]) -> dict:
    return {
        "twse": _parse_twse_bfi82u(twse_bfi82u),
        "tpex": _parse_tpex_summary(tpex_summary),
    }
