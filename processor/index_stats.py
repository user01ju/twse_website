"""Section 1: 加權指數 stats."""
from .utils import parse_num, nt_to_yi


def build(taiex_index: list[dict], stock_day_all: list[dict], fmtqik: dict = None) -> dict:
    """
    Returns:
      close, sign (+/-), change_pts, change_pct, trading_amount_yi, transaction_count

    fmtqik: optional FMTQIK dict with official market totals (trade_value, transaction).
            When present, overrides STOCK_DAY_ALL sums which exclude ETFs/warrants.
    """
    taiex = next(
        (r for r in taiex_index if "發行量加權股價指數" in r.get("指數", "")),
        None,
    )
    if not taiex:
        raise ValueError("發行量加權股價指數 not found in MI_INDEX data")

    close       = parse_num(taiex.get("收盤指數", 0))
    sign        = taiex.get("漲跌", "+")
    change_pts  = parse_num(taiex.get("漲跌點數", 0))
    change_pct  = parse_num(taiex.get("漲跌百分比", 0))
    if sign == "-":
        change_pts = -abs(change_pts)
        change_pct = -abs(change_pct)

    if fmtqik and fmtqik.get("trade_value"):
        trading_amount_yi = nt_to_yi(fmtqik["trade_value"])
        transaction_count = int(fmtqik.get("transaction", 0))
    else:
        total_value = sum(parse_num(s.get("TradeValue", 0)) for s in stock_day_all)
        total_txn   = sum(int(parse_num(s.get("Transaction", 0))) for s in stock_day_all)
        trading_amount_yi = nt_to_yi(total_value)
        transaction_count = total_txn

    return {
        "close":              close,
        "change_pts":         change_pts,
        "change_pct":         change_pct,
        "trading_amount_yi":  trading_amount_yi,
        "transaction_count":  transaction_count,
    }
