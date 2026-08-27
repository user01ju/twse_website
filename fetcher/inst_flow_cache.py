"""每日法人淨買賣超快取（億元），子類股層 + 個股層兩份。

Files（跟 prices 一樣住在 output/ 裡，隨 gh-pages 部署持久化、下一輪 CI 還原）：
  output/data/inst_flow/YYYY-MM-DD.json   子類股層，約 6 KB/日
    {"IC設計": {"c": 12.34, "f": 8.1, "t": 3.0, "d": 1.2, "n": 25}, ...}
  output/data/inst_stock/YYYY-MM-DD.json  個股層（展開列用），約 30 KB/日
    {"2330": ["台積電", 12.34, 10.1, 1.2, 1.0], ...}   [名稱, c, f, t, d]

  c=三大法人  f=外資  t=投信  d=自營商（億元）   n=當日有量的成分股數

個股層只留任一法人別 |淨額| ≥ _STOCK_MIN_YI 的，長尾砍掉六成檔數但只丟 0.6%
金額（2026-08-26 實測：1842 檔 58 KB → 747 檔 24 KB，涵蓋 99.4%）。

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

_DIR       = OUTPUT_DIR / "data" / "inst_flow"
_STOCK_DIR = OUTPUT_DIR / "data" / "inst_stock"

# 個股層寫入門檻（億）：四個法人別取最大絕對值
_STOCK_MIN_YI = 0.05

# 寫入下限：完整日 T86 約 1300 列、TPEX 約 900 列。低於下限代表某一市場沒到齊，
# 寧可不寫也不要讓半份資料汙染 5/20 日累計（半份不會報錯，只會安靜偏小）。
_MIN_TWSE_ROWS = 500
_MIN_TPEX_ROWS = 300


def _path(d: date) -> Path:
    return _DIR / f"{d.isoformat()}.json"


def _stock_path(d: date) -> Path:
    return _STOCK_DIR / f"{d.isoformat()}.json"


def per_stock(twse_t86: dict, tpex_all: list[dict],
              twse_prices: dict, tpex_prices: dict) -> dict:
    """個股法人淨額（張）× 當日收盤 → {code: (name, c, f, t, d)}（億元）。"""
    rows  = combined_inst._from_twse_t86(twse_t86 or {}, twse_prices or {})
    rows += combined_inst._from_tpex_all(tpex_all or [], tpex_prices or {})

    prices = {**(twse_prices or {}), **(tpex_prices or {})}   # 上市櫃代號全國唯一
    out = {}
    for s in rows:
        close = prices.get(s["code"], 0)
        if close <= 0:
            continue
        k = 1000 * close / 1e8      # 張 → 億元
        out[s["code"]] = (s["name"],
                          s["net_yi"],                 # 與第 5/6 區塊同一個數字
                          s["foreign_net"] * k,
                          s["trust_net"]   * k,
                          s["dealer_net"]  * k)
    return out


def aggregate(twse_t86: dict, tpex_all: list[dict],
              twse_prices: dict, tpex_prices: dict) -> dict:
    """子類股層淨買賣超（億元）。"""
    return _by_sector(per_stock(twse_t86, tpex_all, twse_prices, tpex_prices))


def _by_sector(stocks: dict) -> dict:
    sector_map, _ = sector_inst._load_sector_map()
    out: dict[str, dict] = {}
    for code, (_name, c, f, t, d) in stocks.items():
        g = out.setdefault(sector_map.get(code) or "其他",
                           {"c": 0.0, "f": 0.0, "t": 0.0, "d": 0.0, "n": 0})
        g["c"] += c
        g["f"] += f
        g["t"] += t
        g["d"] += d
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

    stocks  = per_stock(twse_t86, tpex_all, twse_prices, tpex_prices)
    sectors = _by_sector(stocks)
    if len(sectors) < 20:
        logger.warning(f"inst_flow: 只聚出 {len(sectors)} 個子類股 → 不寫 {d}")
        return False

    _DIR.mkdir(parents=True, exist_ok=True)
    _path(d).write_text(json.dumps(sectors, ensure_ascii=False), encoding="utf-8")

    # 個股層：長尾砍掉（門檻見模組 docstring）。子類股層已經寫成功了，這份失敗
    # 只讓展開列少一天，不回報 False（母表數字仍然完整）。
    try:
        trimmed = {
            code: [name, round(c, 3), round(f, 3), round(t, 3), round(dl, 3)]
            for code, (name, c, f, t, dl) in stocks.items()
            if max(abs(c), abs(f), abs(t), abs(dl)) >= _STOCK_MIN_YI
        }
        _STOCK_DIR.mkdir(parents=True, exist_ok=True)
        _stock_path(d).write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")
        logger.info(f"inst_flow cache saved: {_path(d)} ({len(sectors)} 子類股 / "
                    f"{len(trimmed)} 檔個股)")
    except Exception as e:
        logger.warning(f"inst_stock 寫入失敗（母表不受影響）: {e}")
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


def load_stocks(d: date) -> dict:
    p = _stock_path(d)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"inst_stock load failed {p}: {e}")
        return {}


def load_window(end: date, days: int, stocks: bool = False) -> list[tuple[date, dict]]:
    """回傳 end（含）往前最多 days 個有快取的日子，舊 → 新。

    stocks=True 換成讀個股層。日期序列以**子類股層**為準（它是 save() 的成功條件），
    個股層缺檔就給 {} —— 兩份錯開時展開列會少一天，但母表與展開列的窗口仍對齊。
    """
    result = []
    d = end
    for _ in range(days * 2):       # 掃 2 倍日曆天，蓋過週末與國定假日
        if len(result) >= days:
            break
        data = load(d)
        if data:
            result.append((d, load_stocks(d) if stocks else data))
        d -= timedelta(days=1)
    result.reverse()
    return result
