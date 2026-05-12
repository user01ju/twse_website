"""Group top-100 gainers/losers by CMoney sub-sector."""
from collections import defaultdict
from .sector_inst import _load_sector_map


def _group(stocks: list[dict], descending: bool = True) -> list[dict]:
    sector_map, parent_map = _load_sector_map()

    groups: dict[str, dict] = defaultdict(lambda: {
        "stocks": [], "sum_pct": 0.0, "parent": ""
    })

    for s in stocks:
        code   = str(s.get("code", "")).strip()
        sector = sector_map.get(code) or "其他"
        parent = parent_map.get(code, "")
        groups[sector]["parent"] = parent
        groups[sector]["sum_pct"] += s.get("change_pct", 0.0)
        groups[sector]["stocks"].append({
            "code":       code,
            "name":       s.get("name", ""),
            "change_pct": s.get("change_pct", 0.0),
            "close":      s.get("close", 0.0),
            "market":     s.get("market", ""),
        })

    result = []
    for sector, d in groups.items():
        count   = len(d["stocks"])
        avg_pct = d["sum_pct"] / count if count else 0.0
        # sort stocks within group same direction as outer sort
        d["stocks"].sort(key=lambda x: x["change_pct"], reverse=descending)
        result.append({
            "sector":   sector,
            "parent":   d["parent"],
            "count":    count,
            "avg_pct":  round(avg_pct, 2),
            "stocks":   d["stocks"],
        })

    # primary: count desc; secondary: avg_pct (direction-aware)
    result.sort(key=lambda x: (x["count"], x["avg_pct"]), reverse=descending)
    return result


def build(gainers: list[dict], losers: list[dict]) -> dict:
    return {
        "gainers": _group(gainers, descending=True),
        "losers":  _group(losers,  descending=False),
    }
