"""每日「法人 × CMoney 子類股」淨買賣超快取（億元）。

Files: output/data/inst_flow/YYYY-MM-DD.json — 跟 prices 一樣住在 output/ 裡，
隨 gh-pages 部署持久化、下一輪 CI 還原（runner 是 ephemeral）。

Format: {"IC設計": {"c": 12.34, "f": 8.1, "t": 3.0, "d": 1.2, "n": 25}, ...}
  c=三大法人  f=外資  t=投信  d=自營商（億元）   n=當日有量的成分股數

口徑與第 5/6 區塊同源（直接用 combined_inst 的解析器），但**不套 top-100 截斷** —
資金流向要看整個子類股，不是只看當日前 100 大個股。
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from config import OUTPUT_DIR
from processor import combined_inst, sector_inst

logger = logging.getLogger(__name__)

_DIR = OUTPUT_DIR / "data" / "inst_flow"

# 寫入下限：完整日 T86 約 1300 列、TPEX 約 900 列。低於下限代表某一市場沒到齊，
# 寧可不寫也不要讓半份資料汙染 5/20 日累計（半份不會報錯，只會安靜偏小）。
_MIN_TWSE_ROWS = 500
_MIN_TPEX_ROWS = 300


def _path(d: date) -> Path:
    return _DIR / f"{d.isoformat()}.json"


def aggregate(twse_t86: dict, tpex_all: list[dict],
              twse_prices: dict, tpex_prices: dict) -> dict:
    """個股法人淨額（張）× 當日收盤 → 子類股淨買賣超（億元）。"""
    rows  = combined_inst._from_twse_t86(twse_t86 or {}, twse_prices or {})
    rows += combined_inst._from_tpex_all(tpex_all or [], tpex_prices or {})

    prices = {**(twse_prices or {}), **(tpex_prices or {})}   # 上市櫃代號全國唯一
    sector_map, _ = sector_inst._load_sector_map()

    out: dict[str, dict] = {}
    for s in rows:
        close = prices.get(s["code"], 0)
        if close <= 0:
            continue
        k = 1000 * close / 1e8      # 張 → 億元
        g = out.setdefault(sector_map.get(s["code"]) or "其他",
                           {"c": 0.0, "f": 0.0, "t": 0.0, "d": 0.0, "n": 0})
        g["c"] += s["net_yi"]                 # 與第 5/6 區塊同一個數字
        g["f"] += s["foreign_net"] * k
        g["t"] += s["trust_net"]   * k
        g["d"] += s["dealer_net"]  * k
        g["n"] += 1

    return {
        sec: {"c": round(g["c"], 3), "f": round(g["f"], 3),
              "t": round(g["t"], 3), "d": round(g["d"], 3), "n": g["n"]}
        for sec, g in out.items()
    }


def save(d: date, twse_t86: dict, tpex_all: list[dict],
         twse_prices: dict, tpex_prices: dict) -> bool:
    """聚合並寫入快取。任一市場列數不足就拒寫（回 False）。"""
    n_twse = len(((twse_t86 or {}).get("data")) or [])
    n_tpex = len(tpex_all or [])
    if n_twse < _MIN_TWSE_ROWS or n_tpex < _MIN_TPEX_ROWS:
        logger.warning(f"inst_flow: 列數不足 (T86 {n_twse} / TPEX {n_tpex}) → 不寫 {d}，"
                       f"避免半份資料汙染 5/20 日累計")
        return False

    sectors = aggregate(twse_t86, tpex_all, twse_prices, tpex_prices)
    if len(sectors) < 20:
        logger.warning(f"inst_flow: 只聚出 {len(sectors)} 個子類股 → 不寫 {d}")
        return False

    _DIR.mkdir(parents=True, exist_ok=True)
    _path(d).write_text(json.dumps(sectors, ensure_ascii=False), encoding="utf-8")
    logger.info(f"inst_flow cache saved: {_path(d)} ({len(sectors)} 子類股)")
    return True


def load(d: date) -> dict:
    p = _path(d)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"inst_flow load failed {p}: {e}")
        return {}


def load_window(end: date, days: int) -> list[tuple[date, dict]]:
    """回傳 end（含）往前最多 days 個有快取的日子，舊 → 新。"""
    result = []
    d = end
    for _ in range(days * 2):       # 掃 2 倍日曆天，蓋過週末與國定假日
        if len(result) >= days:
            break
        data = load(d)
        if data:
            result.append((d, data))
        d -= timedelta(days=1)
    result.reverse()
    return result
