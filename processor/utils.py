import re as _re


_WARRANT_RE = _re.compile(r"[購售]\d")


def is_warrant(code: str, name: str = "") -> bool:
    """Return True if this security is a warrant (認購/認售權證).
    Warrant names have '購' or '售' followed by a serial digit
    (e.g. 信驊凱基59售02, 台積電群益9A購01). A bare 購/售 without a
    trailing digit is a real company name (e.g. 2945 三商家購).
    """
    return bool(_WARRANT_RE.search(name))


def is_stock_code(code: str) -> bool:
    """Return True only for regular 4-digit stock codes (e.g. '2330').
    Excludes ETFs (5-digit starting with 00xxx), warrants (6-digit), etc.
    """
    return len(code) == 4 and code.isdigit()


def parse_num(s) -> float:
    """Parse number string with commas and sign prefix, e.g. '1,234,567' or '+0.85'."""
    if s is None:
        return 0.0
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def shares_to_zhang(shares: float) -> float:
    """Convert shares (個) to 張 (1000 shares)."""
    return shares / 1000


def nt_to_yi(nt_dollars: float) -> float:
    """Convert NT$ to 億 (1e8)."""
    return nt_dollars / 1e8


def change_pct(close: float, change: float) -> float:
    """Calculate % change. close and change are already floats."""
    prev = close - change
    if prev == 0:
        return 0.0
    return (change / prev) * 100


def _is_limit(close: float, prev: float, direction: int) -> bool:
    """
    Check if close hit the price limit (台灣漲跌停判斷).
    Compare close against the exact tick-rounded limit price.
    Tries ±10% / ±15% / ±20% to cover stocks, leveraged/inverse ETFs.
    direction: +1 = up, -1 = down
    """
    for pct in (0.10, 0.15, 0.20):
        limit = round(prev * (1 + direction * pct), 2)
        if abs(close - limit) < 0.005:   # within half a tick
            return True
    return False


def price_status(close: float, change: float) -> str:
    """Classify stock price movement."""
    if change == 0:
        return "flat"
    prev = close - change
    if prev <= 0:
        return "up" if change > 0 else "down"
    if change > 0:
        return "limit_up" if _is_limit(close, prev, +1) else "up"
    return "limit_down" if _is_limit(close, prev, -1) else "down"
