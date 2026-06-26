"""Group institutional buying (外資/投信/三大法人) by CMoney sub-sector."""
import json
import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

_SECTOR_MAP: dict[str, str] | None = None   # code → sub-sector name
_SECTOR_PARENT: dict[str, str] | None = None  # code → parent category


def _load_sector_map() -> tuple[dict, dict]:
    global _SECTOR_MAP, _SECTOR_PARENT
    if _SECTOR_MAP is not None:
        return _SECTOR_MAP, _SECTOR_PARENT

    json_path = Path(__file__).parent.parent / "cmoney_raw.json"
    if not json_path.exists():
        logger.warning("cmoney_raw.json not found; sector grouping unavailable")
        _SECTOR_MAP = {}
        _SECTOR_PARENT = {}
        return _SECTOR_MAP, _SECTOR_PARENT

    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)   # [{parent, name, code, stocks:[{id, name}]}]

    code_to_sector = {}
    code_to_parent = {}
    for cat in raw:
        parent = cat["parent"]
        sector = cat["name"]
        for s in cat.get("stocks", []):
            sid = str(s["id"]).strip()
            code_to_sector[sid] = sector
            code_to_parent[sid] = parent

    _SECTOR_MAP   = code_to_sector
    _SECTOR_PARENT = code_to_parent
    logger.info(f"Loaded CMoney sector map: {len(code_to_sector)} stocks")
    return _SECTOR_MAP, _SECTOR_PARENT


def _group_by_sector(stocks: list[dict], sell_side: bool = False) -> list[dict]:
    """
    Group stocks by CMoney sub-sector.
    sell_side=True  → sort by net_yi ascending (most negative first), pct uses abs values.
    sell_side=False → sort by net_yi descending.
    """
    sector_map, parent_map = _load_sector_map()

    groups: dict[str, dict] = defaultdict(lambda: {"net_yi": 0.0, "net_zhang": 0, "stocks": []})

    no_sector = 0
    for s in stocks:
        code   = str(s.get("code", "")).strip()
        sector = sector_map.get(code, "")
        if not sector:
            no_sector += 1
            sector = "其他"
        parent = parent_map.get(code, "")

        groups[sector]["net_yi"]    += s.get("net_yi", 0)
        groups[sector]["net_zhang"] += s.get("net_zhang", 0)
        groups[sector]["parent"]     = parent
        groups[sector]["stocks"].append({
            "code":      code,
            "name":      s.get("name", ""),
            "net_zhang": s.get("net_zhang", 0),
            "net_yi":    s.get("net_yi", 0),
            "market":    s.get("market", ""),
        })

    if no_sector:
        logger.debug(f"  {no_sector} stocks had no CMoney sector")

    total_abs_yi = sum(abs(d["net_yi"]) for d in groups.values()) or 1.0

    # Sort: sell → ascending net_yi (worst first); buy → descending
    sort_key  = lambda x: x[1]["net_yi"]
    sort_desc = not sell_side

    result = []
    for sector, d in sorted(groups.items(), key=sort_key, reverse=sort_desc):
        # Within sector: sort by net_zhang (sell → ascending / buy → descending)
        d["stocks"].sort(key=lambda s: s["net_zhang"], reverse=sort_desc)
        result.append({
            "sector":    sector,
            "parent":    d.get("parent", ""),
            "count":     len(d["stocks"]),
            "net_yi":    round(d["net_yi"], 2),
            "net_zhang": d["net_zhang"],
            "abs_yi":    round(abs(d["net_yi"]), 2),
            "pct":       round(abs(d["net_yi"]) / total_abs_yi * 100, 1),
            "stocks":    d["stocks"],
        })

    return result


def _summary_from_combined(stocks: list[dict], sell_side: bool) -> tuple[list[dict], dict]:
    """
    直接從 combined 個股的 foreign_net/trust_net/dealer_net（張）換算各機構億元。
    保證：外資 + 投信 + 自營商 = 三大法人（每行精確成立）。
    sell_side=True：sign=-1，combined_yi 為負，乘以 -1 後顯示正數。
    """
    sector_map, parent_map = _load_sector_map()
    sign = -1 if sell_side else 1

    groups: dict[str, dict] = defaultdict(lambda: {
        "combined_yi": 0.0, "foreign_yi": 0.0,
        "trust_yi":    0.0, "dealer_yi":  0.0,
        "stocks": [],       "parent":     "",
    })

    for s in stocks:
        code      = str(s.get("code", "")).strip()
        net_zhang = s.get("net_zhang", 0)
        net_yi    = s.get("net_yi",    0.0)
        if net_yi == 0:
            continue
        ppz = net_yi / net_zhang if net_zhang else 0.0   # 億 per 張

        sector = sector_map.get(code, "其他")
        g = groups[sector]
        g["combined_yi"] += net_yi * sign
        g["foreign_yi"]  += s.get("foreign_net", 0) * ppz * sign
        g["trust_yi"]    += s.get("trust_net",   0) * ppz * sign
        g["dealer_yi"]   += s.get("dealer_net",  0) * ppz * sign
        g["parent"]       = parent_map.get(code, "")
        g["stocks"].append({
            "code":      code,
            "name":      s.get("name", ""),
            "net_yi":    abs(net_yi),
        })

    rows = []
    for sector, d in sorted(groups.items(),
                             key=lambda x: x[1]["combined_yi"], reverse=True):
        if d["combined_yi"] <= 0:
            continue
        d["stocks"].sort(key=lambda s: s["net_yi"], reverse=True)
        rows.append({
            "sector":      sector,
            "parent":      d["parent"],
            "count":       len(d["stocks"]),
            "combined_yi": round(d["combined_yi"], 1),
            "foreign_yi":  round(d["foreign_yi"],  1),
            "trust_yi":    round(d["trust_yi"],    1),
            "dealer_yi":   round(d["dealer_yi"],   1),
            "stocks":      d["stocks"],
        })

    totals = {
        "tot_combined": round(sum(r["combined_yi"] for r in rows), 1),
        "tot_foreign":  round(sum(r["foreign_yi"]  for r in rows), 1),
        "tot_trust":    round(sum(r["trust_yi"]    for r in rows), 1),
        "tot_dealer":   round(sum(r["dealer_yi"]   for r in rows), 1),
    }
    return rows, totals


def build_summary(combined_data: dict) -> dict:
    """
    以三大法人 buy_super/sell_super 個股為基準。
    各機構金額從同批個股的 foreign_net/trust_net/dealer_net 換算。
    外資 + 投信 + 自營商 = 三大法人（每行精確成立）。
    """
    buy_rows,  buy_totals  = _summary_from_combined(
        combined_data.get("buy_super")  or [], sell_side=False)
    sell_rows, sell_totals = _summary_from_combined(
        combined_data.get("sell_super") or [], sell_side=True)
    return {
        "buy":  {**buy_totals,  "rows": buy_rows},
        "sell": {**sell_totals, "rows": sell_rows},
    }


def build_merged(combined_data: dict) -> list[dict]:
    """三大法人合併 by 子類股：買超+賣超個股一起看。
    每個子類股的淨買賣超 = 該類股所有個股 net_yi 加總（含正負），
    子類股依淨額排序（正到負），類股內個股亦依 net_yi 排序（正到負）。
    """
    sector_map, parent_map = _load_sector_map()

    # buy_super (net_yi>0) + sell_super (net_yi<0) = 全部有量的個股
    stocks = [s for s in (combined_data.get("buy_super") or []) if s.get("net_zhang", 0) != 0]
    stocks += [s for s in (combined_data.get("sell_super") or []) if s.get("net_zhang", 0) != 0]

    groups: dict[str, dict] = defaultdict(lambda: {"net_yi": 0.0, "net_zhang": 0, "stocks": [], "parent": ""})
    for s in stocks:
        code   = str(s.get("code", "")).strip()
        sector = sector_map.get(code) or "其他"
        groups[sector]["parent"]     = parent_map.get(code, "")
        groups[sector]["net_yi"]    += s.get("net_yi", 0.0)
        groups[sector]["net_zhang"] += s.get("net_zhang", 0)
        groups[sector]["stocks"].append({
            "code":      code,
            "name":      s.get("name", ""),
            "net_zhang": s.get("net_zhang", 0),
            "net_yi":    s.get("net_yi", 0.0),
            "market":    s.get("market", ""),
        })

    result = []
    for sector, d in groups.items():
        d["stocks"].sort(key=lambda x: x["net_yi"], reverse=True)   # 正到負
        # 總成交額：買超與賣超金額取絕對值加總（不相扣）
        abs_yi = sum(abs(st["net_yi"]) for st in d["stocks"])
        result.append({
            "sector":    sector,
            "parent":    d["parent"],
            "count":     len(d["stocks"]),
            "net_yi":    round(d["net_yi"], 2),
            "abs_yi":    round(abs_yi, 2),
            "net_zhang": d["net_zhang"],
            "stocks":    d["stocks"],
        })

    result.sort(key=lambda x: x["net_yi"], reverse=True)            # 子類股正到負
    return result


def build(
    foreign_data:  dict,
    trust_data:    dict,
    combined_data: dict,
    dealer_data:   dict = None,
) -> dict:
    """Returns buy/sell sector groups for 外資, 投信, 自營商, 三大法人."""
    def _side(data: dict, key: str, sell: bool) -> list[dict]:
        stocks = [s for s in (data.get(key) or []) if s.get("net_zhang", 0) != 0]
        return _group_by_sector(stocks, sell_side=sell)

    dealer_data = dealer_data or {}
    return {
        "foreign_buy":  _side(foreign_data,  "buy_super",  False),
        "foreign_sell": _side(foreign_data,  "sell_super", True),
        "trust_buy":    _side(trust_data,    "buy_super",  False),
        "trust_sell":   _side(trust_data,    "sell_super", True),
        "dealer_buy":   _side(dealer_data,   "buy_super",  False),
        "dealer_sell":  _side(dealer_data,   "sell_super", True),
        "combined_buy": _side(combined_data, "buy_super",  False),
        "combined_sell":_side(combined_data, "sell_super", True),
    }
