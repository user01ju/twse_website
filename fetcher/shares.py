"""發行股數快取（市值換算用）。

來源是兩個「公司基本資料」端點，各自一次回全市場，**只有最新快照、沒有歷史**：
  TWSE  openapi/v1/opendata/t187ap03_L    欄位「已發行普通股數或TDR原股發行股數」
  TPEX  openapi/v1/mopsfin_t187ap03_O     欄位 IssueShares

Files: output/data/shares.json — {"2330": 25930380458, ...}（股）

股數變動（增減資、可轉債轉換）以月為尺度，而流向窗口只有 20 天，所以整個窗口
共用「今天的股數」在誤差上可接受；但也因此**市值比是近似值**，別拿它做精算。
抓失敗就沿用舊快取（寧可用昨天的股數，也不要整欄變空）。
"""
import json
import logging

import requests

from config import OUTPUT_DIR
from processor.utils import is_stock_code, parse_num

logger = logging.getLogger(__name__)

_PATH = OUTPUT_DIR / "data" / "shares.json"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"}
_TWSE = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_MIN_ROWS = 800          # 兩邊合計低於此 → 視為抓壞，不覆蓋舊快取


def _fetch() -> dict[str, float]:
    out: dict[str, float] = {}
    for url, code_key, share_key in (
        (_TWSE, "公司代號", "已發行普通股數或TDR原股發行股數"),
        (_TPEX, "SecuritiesCompanyCode", "IssueShares"),
    ):
        try:
            rows = requests.get(url, headers=_HEADERS, timeout=40).json()
        except Exception as e:
            logger.warning(f"shares: {url} 抓取失敗 — {e}")
            continue
        for r in rows or []:
            code = str(r.get(code_key, "")).strip()
            n = parse_num(r.get(share_key, 0))
            if is_stock_code(code) and n > 0:
                out[code] = n
    return out


def update() -> bool:
    """重抓並寫入快取。抓到的筆數太少就保留舊檔，回 False。"""
    fresh = _fetch()
    if len(fresh) < _MIN_ROWS:
        logger.warning(f"shares: 只抓到 {len(fresh)} 檔（門檻 {_MIN_ROWS}）→ 保留舊快取")
        return False
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
    logger.info(f"shares cache saved: {_PATH} ({len(fresh)} 檔)")
    return True


def load() -> dict[str, float]:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"shares load failed: {e}")
        return {}
