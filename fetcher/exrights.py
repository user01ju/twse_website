"""除權息/減資/面額變更參考價 — 還原權息鏈的關鍵輸入。

來源（皆官方免費 JSON，無 key）：
  TWSE TWT49U  除權息參考價        （日期區間，按月查）
  TWSE TWTAUU  減資恢復買賣參考價  （日期區間）
  TWSE TWTB8U  面額變更恢復買賣參考價（日期區間，如股票分割）
  TPEX exDailyQ 除權息參考價        （區間查詢有 100 筆上限 → 逐日查）
  TPEX revivt   減資恢復買賣參考價  （日期區間）

快取：output/data/exrights.csv（date,id,ref）— 與 price_cache 相同的
gh-pages 持久化模式。冷啟動 fallback data/exrights_seed.csv（repo 內，
一次性從 sector_gainer 拷貝）。缺 ref 的退化行為是還原鏈 fallback 前收
（= 未還原），不會炸。
"""
import csv
import logging
import time
from datetime import date, timedelta

import requests

from config import BASE_DIR, OUTPUT_DIR, FETCH_TIMEOUT

logger = logging.getLogger(__name__)

_CACHE_PATH = OUTPUT_DIR / "data" / "exrights.csv"
_SEED_PATH  = BASE_DIR / "data" / "exrights_seed.csv"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# (tag, url_fn(start_iso, end_iso), 日期欄, 代號欄, 參考價欄)
# TWSE 日期參數 YYYYMMDD；TPEX YYYY/MM/DD。回傳日期皆民國年（+1911）。
_RANGE_SOURCES = [
    ("TWSE除權息",
     lambda s, e: f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={s.replace('-', '')}&endDate={e.replace('-', '')}&response=json",
     "資料日期", "股票代號", "除權息參考價"),
    ("TWSE減資",
     lambda s, e: f"https://www.twse.com.tw/rwd/zh/reducation/TWTAUU?startDate={s.replace('-', '')}&endDate={e.replace('-', '')}&response=json",
     "恢復買賣日期", "股票代號", "恢復買賣參考價"),
    ("TWSE面額變更",
     lambda s, e: f"https://www.twse.com.tw/rwd/zh/change/TWTB8U?startDate={s.replace('-', '')}&endDate={e.replace('-', '')}&response=json",
     "恢復買賣日期", "股票代號", "恢復買賣參考價"),
    ("TPEX減資",
     lambda s, e: f"https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate={s.replace('-', '/')}&endDate={e.replace('-', '/')}&response=json",
     "恢復買賣日期", "股票代號", "減資恢復買賣開始日參考價格"),
]


def _get_json(url: str, retries: int = 2) -> dict:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def _roc_to_iso(s: str) -> str | None:
    """'114/09/16' → '2025-09-16'（民國年 +1911）"""
    parts = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "").split("/")
    if len(parts) < 3:
        return None
    try:
        return f"{int(parts[0]) + 1911}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except ValueError:
        return None


def _num(s) -> float | None:
    try:
        v = float(str(s).replace(",", ""))
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_table(j: dict, date_f: str, id_f: str, ref_f: str) -> list[tuple[str, str, float]]:
    """回傳 [(iso_date, code, ref)]。兼容頂層 {fields,data} 與 {tables:[...]} 兩種包法。"""
    tables = j.get("tables") or [j]
    rows = []
    for t in tables:
        fields = t.get("fields") or []
        if date_f not in fields or ref_f not in fields:
            continue
        fi = {f: i for i, f in enumerate(fields)}
        for r in t.get("data") or []:
            d = _roc_to_iso(r[fi[date_f]])
            code = str(r[fi[id_f]]).strip()
            ref = _num(r[fi[ref_f]])
            # 只留 4 碼正股（排除 ETF/權證/特別股）
            if d and ref and len(code) == 4 and code.isdigit():
                rows.append((d, code, ref))
    return rows


def _fetch_range_sources(start: date, end: date) -> list[tuple[str, str, float]]:
    """TWSE 三表 + TPEX 減資表，按月切塊查區間。"""
    rows = []
    for tag, url_fn, date_f, id_f, ref_f in _RANGE_SOURCES:
        cur = start
        while cur <= end:
            month_end = (cur.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            chunk_end = min(month_end, end)
            try:
                j = _get_json(url_fn(cur.isoformat(), chunk_end.isoformat()))
                got = _parse_table(j, date_f, id_f, ref_f)
                rows.extend(got)
                if got:
                    logger.info(f"exrights {tag} {cur}~{chunk_end}: {len(got)} rows")
            except Exception as e:
                logger.warning(f"exrights {tag} {cur}~{chunk_end} failed: {e}")
            cur = chunk_end + timedelta(days=1)
            time.sleep(0.4)
    return rows


def _fetch_tpex_days(start: date, end: date) -> list[tuple[str, str, float]]:
    """TPEX exDailyQ 區間查詢有 100 筆上限 → 逐日查（跳過週末）。"""
    rows = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            d_str = cur.strftime("%Y/%m/%d")
            try:
                j = _get_json(f"https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={d_str}&endDate={d_str}&response=json")
                got = _parse_table(j, "除權息日期", "代號", "除權息參考價")
                rows.extend(got)
                if got:
                    logger.info(f"exrights TPEX除權息 {cur}: {len(got)} rows")
            except Exception as e:
                logger.warning(f"exrights TPEX除權息 {cur} failed: {e}")
            time.sleep(0.35)
        cur += timedelta(days=1)
    return rows


def _load_csv(path) -> dict[str, float]:
    """{'iso_date|code': ref}"""
    refs = {}
    if not path.exists():
        return refs
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 3 and row[0] != "date":
                    try:
                        refs[f"{row[0]}|{row[1]}"] = float(row[2])
                    except ValueError:
                        continue
    except Exception as e:
        logger.warning(f"exrights load failed {path}: {e}")
    return refs


def _load_merged() -> dict[str, float]:
    """seed 打底，cache 覆蓋（cache 是活資料）。"""
    refs = _load_csv(_SEED_PATH)
    refs.update(_load_csv(_CACHE_PATH))
    return refs


def update(today: date, backfill_days: int = 7) -> None:
    """抓 [today-backfill_days, today] 的參考價，merge 進 cache 並存檔。

    在 build 當下自抓 → 除息日 D 的 15:0x 就有 D 的 ref，無跨專案時間差。
    任一來源失敗只 log 不 raise（缺 ref 退化為未還原，下輪再補）。
    """
    start = today - timedelta(days=backfill_days)
    refs = _load_merged()
    before = len(refs)

    for d, code, ref in _fetch_range_sources(start, today):
        refs[f"{d}|{code}"] = ref
    for d, code, ref in _fetch_tpex_days(start, today):
        refs[f"{d}|{code}"] = ref

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "id", "ref"])
        for key in sorted(refs):
            d, code = key.split("|")
            w.writerow([d, code, f"{refs[key]:g}"])
    logger.info(f"exrights cache saved: {len(refs)} rows ({len(refs) - before} new) -> {_CACHE_PATH}")


def load_refs() -> dict[str, dict[str, float]]:
    """給還原鏈用：{iso_date: {code: ref}}"""
    by_date: dict[str, dict[str, float]] = {}
    for key, ref in _load_merged().items():
        d, code = key.split("|")
        by_date.setdefault(d, {})[code] = ref
    return by_date
