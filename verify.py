#!/usr/bin/env python3
"""資料正確性驗證（見 VERIFICATION.md）。

    python verify.py --tier a     # 零外部呼叫，只讀 output/ 既有檔案
    python verify.py --tier b     # 交叉源不變量，會打外部 API
    python verify.py --tier all   # 預設

Exit code: 0 = 全過(或只有 SKIP) / 1 = 至少一條 FAIL / 2 = 沒 FAIL 但有 WARN。
CI 只把 1 當失敗。

設計原則
  - 每條檢查是獨立函數，回傳 (status, message)；跑完全部才決定 exit code。
  - 單條內部拋例外 → 捕成該條的 FAIL，不影響其他檢查。
  - 本機 output/ 可能不完整（gitignore + 從 gh-pages 還原）→ 缺檔回 SKIP 而非
    FAIL。在 CI（GITHUB_ACTIONS=true）缺檔才算 FAIL，因為 update.py 回 0 就代表
    它寫過 today.json，缺檔本身就是 bug。
  - 本站已有三道 runtime guard（garbage-date / completeness gate / trend coverage），
    這裡不重複實作，只補 guard 看不到的角度：跨端點互驗 + 部署前的產物檢查。
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import CACHE_DIR, OUTPUT_DIR, REPORTS_DIR          # noqa: E402
from fetcher import inst_flow_cache, market_calendar, price_cache  # noqa: E402
from processor.market_trend import _MIN_COVERAGE               # noqa: E402
from processor.utils import is_stock_code, is_warrant, parse_num  # noqa: E402

_TZ = ZoneInfo("Asia/Taipei")
IS_CI = os.environ.get("GITHUB_ACTIONS") == "true"

# Windows 主控台預設 cp950，訊息裡的全形括號/破折號會變亂碼（CI 的 ubuntu 是
# UTF-8，只有本機會踩到）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 門檻（集中在此，改門檻不用翻程式） ──────────────────────────────────────
# today.json 允許落後的「交易日」數。0 = 當日；1 = 資料尚未發布/當班沒跑成功，
# 屬正常重試區間 → WARN；≥2 表示連續兩個交易日沒更新 → FAIL。
MAX_TRADING_DAY_LAG = 1

# exrights.csv 最新一筆距報告日的天數上限。除權息不是天天有，7~10 天沒新資料很
# 正常；超過 30 天多半是 gh-pages cache 沒還原或 exrights.update() 一直失敗。
EXRIGHTS_STALE_DAYS = 30

# price cache 窗口的**絕對**天數下限（覆蓋率比率之外的第二道，理由見
# check_price_window_coverage）。滿窗是 252+30=282 天，健康值 280 上下；
# 52 週新高低本來就要 _HALF_YEAR≈126 天才開始計，200 以下這區塊已無意義。
_MIN_WINDOW_DAYS = 200

# 法人流向窗口（第 7 區塊）的天數下限。滿窗 20 天，少於 10 天加速度就不判了。
_MIN_FLOW_DAYS = 10

# T86 個股股數 × 收盤價 Σ vs BFI82U 金額。差異來源是「收盤價 ≠ 當日成交均價」，
# 2026-07-31 實測買進 +0.31% / 賣出 +0.19%（那天大盤 +7.98%，波動已算大）。
# 3% 給足空間，8% 以上幾乎不可能是均價偏差，一定是單位錯/漏股/parse 壞。
T86_GROSS_TOL_PASS = 3.0
T86_GROSS_TOL_WARN = 8.0
# 淨額是大數相減，直接看相對誤差會在 net≈0 時爆掉 → 用「淨額差 / 買進總額」正規化。
# 2026-07-31 實測 0.15%。
T86_NET_TOL_PASS = 1.0
T86_NET_TOL_WARN = 3.0

# 漲跌家數 vs MI_INDEX 重算。同一個交易日、同一個 universe（不含權證），理論上
# 應完全相等（2026-07-31 實測 1126/179/59 三項全中）。留 3 家的絕對容差吸收
# STOCK_DAY_ALL 與 MI_INDEX 之間可能的落地時間差。
BREADTH_ABS_TOL_PASS = 3
BREADTH_TOL_WARN_PCT = 2.0   # 佔 total 的百分比

# 上櫃 total 來自官方 ListedCompanyNumbers（含未成交/無比價），up+down+flat 少個
# 1~2% 是常態（2026-07-31 實測 13/890 = 1.5%）。超過 5% 表示 highlight 端點的
# 欄位語意變了，或抓到別天的整包。
BREADTH_UNCLASSIFIED_WARN_PCT = 5.0

# partial today.json 裡「某市場 breadth 整個是 0」的寬限截止（台北時，含當日重建）。
# TPEX openapi 的當日資料常要 15:30~16:00 才落地，但本 workflow 15:00 就開跑 →
# 每個交易日的第一輪都會看到上櫃 total=0。那是還沒發布，不是 parse 壞掉。
# 2026-08-03 / 08-04 兩天的 15:00 班次都因此紅燈，還吃掉了「一天一封信」的告警
# 配額（真的壞掉時只剩 warning）—— 假警報比沒警報更貴，就在這裡。
# 過了這個鐘點還是 0 才算真的壞：TPEX 全天不發資料，本站就整天出不了完整報告。
PENDING_BREADTH_CUTOFF_HOUR = 17

# 加權指數收盤：兩邊都是官方數字，應完全相同。留 0.01 點吸收浮點。
TAIEX_TOL_PASS = 0.01
TAIEX_TOL_WARN_PCT = 0.5

# 除權息參考價覆蓋率：交易所標 'X'（無比價）的 4 碼個股，我們必須有參考價，
# 否則漲跌幅會靜默變成假 0%。X 股票中有少數是停牌復牌（本來就沒 ex-rights
# 事件），所以不要求 100%。
EXDIV_COVERAGE_WARN_PCT = 70.0
EXDIV_COVERAGE_MIN_ROWS_FOR_FAIL = 3   # 有 ≥3 檔 X 但一檔 ref 都沒有 → 系統性失敗

# Tier B 外部呼叫預算（全 Tier B 共用，同一端點只抓一次）與抽樣上限。
MAX_EXTERNAL_CALLS = 3
EXTERNAL_SLEEP_SEC = 1.2
SAMPLE_LIMIT = 3

TODAY_JSON = OUTPUT_DIR / "today.json"
EXRIGHTS_CSV = OUTPUT_DIR / "data" / "exrights.csv"
INDEX_HTML = OUTPUT_DIR / "index.html"

MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
       "Accept": "application/json"}

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


class Skip(Exception):
    """檢查前提不成立（缺檔/外部源掛掉/日期對不上）→ SKIP，不是資料錯。"""


class BudgetExceeded(Exception):
    pass


def _missing(what: str) -> tuple[str, str]:
    """缺檔：CI 算 FAIL（update.py 回 0 就該有），本機算 SKIP。"""
    status = FAIL if IS_CI else SKIP
    where = "CI 環境" if IS_CI else "本機環境"
    return status, f"{what} 不存在（{where} → {status}）"


def _strip_tags(s) -> str:
    return re.sub(r"<[^>]*>", "", str(s)).strip()


# ══════════════════════════════════════════════════════════════════════════
# 共用 context
# ══════════════════════════════════════════════════════════════════════════
class Ctx:
    def __init__(self):
        self.today = datetime.now(_TZ).date()
        self._today_json = None
        self._ex_refs = None
        self._fetched: dict[str, object] = {}
        self.calls = 0

    # ── 本地資料 ──────────────────────────────────────────────────────────
    @property
    def today_json(self) -> dict:
        if self._today_json is None:
            if not TODAY_JSON.exists():
                raise Skip(f"{TODAY_JSON} 不存在")
            self._today_json = json.loads(TODAY_JSON.read_text(encoding="utf-8"))
        return self._today_json

    @property
    def report_date(self) -> date:
        return date.fromisoformat(self.today_json["date"])

    @property
    def ex_refs(self) -> dict[str, float]:
        """{code: ref} for report_date，直接讀 output/data/exrights.csv。"""
        if self._ex_refs is None:
            refs = {}
            target = self.today_json["date"]
            if EXRIGHTS_CSV.exists():
                with EXRIGHTS_CSV.open(encoding="utf-8") as f:
                    for row in csv.reader(f):
                        if len(row) >= 3 and row[0] == target:
                            try:
                                refs[row[1].strip()] = float(row[2])
                            except ValueError:
                                continue
            self._ex_refs = refs
        return self._ex_refs

    def holidays_ready(self, *years: int) -> bool:
        """假日 cache 在本機/CI 都在 .cache/（gitignore）。沒有就別碰
        market_calendar — 它會去打網路，Tier A 必須零外部呼叫。"""
        for y in years:
            p = CACHE_DIR / f"holidays_{y}.json"
            if not p.exists():
                return False
            try:
                if not json.loads(p.read_text(encoding="utf-8")):
                    return False
            except Exception:
                return False
        return True

    # ── 外部呼叫（Tier B 專用，共用預算 + 快取） ──────────────────────────
    def _budgeted_get(self, url: str, params: dict, label: str) -> dict:
        if self.calls >= MAX_EXTERNAL_CALLS:
            raise BudgetExceeded(f"外部呼叫預算已用完（上限 {MAX_EXTERNAL_CALLS}）")
        if self.calls:
            time.sleep(EXTERNAL_SLEEP_SEC)
        self.calls += 1
        resp = requests.get(url, params=params, headers=_UA, timeout=40)
        resp.raise_for_status()
        j = resp.json()
        if not isinstance(j, dict) or str(j.get("stat", "")).upper() != "OK":
            raise Skip(f"{label} stat={j.get('stat') if isinstance(j, dict) else type(j)}")
        return j

    def _cached(self, key: str, fn) -> dict:
        if key not in self._fetched:
            try:
                self._fetched[key] = fn()
            except Skip:
                raise
            except BudgetExceeded:
                raise
            except Exception as e:
                # 對方掛掉/超時/非 200 不是我們資料錯 → SKIP
                raise Skip(f"{key} 取得失敗：{type(e).__name__}: {e}")
        return self._fetched[key]

    def mi_index(self) -> dict:
        """MI_INDEX?type=ALLBUT0999 —— 一次呼叫同時拿到三張互相獨立的表：
        每日收盤行情（全部，不含權證）/ 漲跌證券數合計 / 價格指數。
        跟 pipeline 用的 STOCK_DAY_ALL 是不同端點，所以拿來對照才有意義。"""
        def go():
            j = self._budgeted_get(
                MI_INDEX_URL,
                {"response": "json", "date": self.report_date.strftime("%Y%m%d"),
                 "type": "ALLBUT0999"},
                "MI_INDEX",
            )
            got = str(j.get("date", "")).strip()
            want = self.report_date.strftime("%Y%m%d")
            if got != want:
                raise Skip(f"MI_INDEX 回 {got}，today.json 是 {want} — 日期對不上")
            return j
        return self._cached("mi_index", go)

    def mi_table(self, pred) -> dict:
        for t in self.mi_index().get("tables") or []:
            try:
                if pred(t):
                    return t
            except Exception:
                continue
        raise Skip("MI_INDEX 缺少需要的表")

    def mi_quotes(self) -> list[list]:
        t = self.mi_table(lambda t: (t.get("fields") or [None])[0] == "證券代號")
        f = {n: i for i, n in enumerate(t["fields"])}
        return [t["data"], f]

    def t86(self) -> dict:
        from fetcher import twse_client

        def go():
            if self.calls >= MAX_EXTERNAL_CALLS:
                raise BudgetExceeded(f"外部呼叫預算已用完（上限 {MAX_EXTERNAL_CALLS}）")
            if self.calls:
                time.sleep(EXTERNAL_SLEEP_SEC)
            self.calls += 1
            d = twse_client.fetch_t86()
            got = str(d.get("date", "")).strip()
            want = self.report_date.strftime("%Y%m%d")
            if got != want:
                # T86 不吃 date 參數，只回「當前」那天。對不上通常是報告已過時或
                # 交易所已翻到下一日 → 不是資料錯。
                raise Skip(f"T86 回 {got}，today.json 是 {want} — 日期對不上")
            return d
        return self._cached("t86", go)

    def bfi82u(self) -> dict:
        from fetcher import twse_client

        def go():
            if self.calls >= MAX_EXTERNAL_CALLS:
                raise BudgetExceeded(f"外部呼叫預算已用完（上限 {MAX_EXTERNAL_CALLS}）")
            if self.calls:
                time.sleep(EXTERNAL_SLEEP_SEC)
            self.calls += 1
            d = twse_client.fetch_bfi82u()
            got = str(d.get("date", "")).strip()
            want = self.report_date.strftime("%Y%m%d")
            if got != want:
                raise Skip(f"BFI82U 回 {got}，today.json 是 {want} — 日期對不上")
            return d
        return self._cached("bfi82u", go)


# ══════════════════════════════════════════════════════════════════════════
# Tier A — 零外部呼叫
# ══════════════════════════════════════════════════════════════════════════
def check_today_date_expected_trading_day(ctx: Ctx):
    """VERIFICATION.md Tier A 唯一新增項：today.json 日期 = 假日表推出的預期交易日。

    落後天數用交易日算（週末/連假不誤報）。假日表 cache 不在就 SKIP —— Tier A
    不准打網路，而 market_calendar 沒 cache 會去抓 holidaySchedule。
    """
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    rd = ctx.report_date
    if not ctx.holidays_ready(rd.year, ctx.today.year):
        return SKIP, (f"假日表 cache 缺 .cache/holidays_{rd.year}.json 或 "
                      f"holidays_{ctx.today.year}.json — Tier A 不打網路，跳過")

    expected = market_calendar.get_latest_trading_day()
    if rd > expected:
        return FAIL, (f"today.json 日期 {rd} 晚於預期交易日 {expected} "
                      f"（未來日期 / 垃圾日期）")
    if not market_calendar.is_trading_day(rd):
        return FAIL, f"today.json 日期 {rd} 依假日表不是交易日（預期 {expected}）"

    lag, d = 0, expected
    while d > rd and lag <= 10:
        d -= timedelta(days=1)
        if market_calendar.is_trading_day(d):
            lag += 1
    complete = ctx.today_json.get("complete")
    base = (f"today.json {rd}，預期交易日 {expected}，落後 {lag} 個交易日"
            f"（門檻 {MAX_TRADING_DAY_LAG}），complete={complete}")
    if lag == 0:
        return PASS, base
    if lag <= MAX_TRADING_DAY_LAG:
        return WARN, base + " — 資料尚未發布或該班次沒跑成功，下一班應補上"
    return FAIL, base


def check_today_json_structure(ctx: Ctx):
    """today.json 內部一致性：家數加總、極值、指數欄位。壞掉的 parse 幾乎一定
    會在這裡露餡（全 0 / total 對不上 / 漲停 > 上漲）。

    上市 total 是 market_breadth._count() 自己數的 → 必須嚴格等於 up+down+flat。
    上櫃 total 來自 TPEX 官方 highlight 的 ListedCompanyNumbers，那個**包含未成交
    與無比價**，所以 up+down+flat < total 是正常的（2026-07-31 實測差 13 家 =
    1.5%）；只有反過來（sum > total）或缺口大到不合理才有問題。

    當天的 partial today.json 在 PENDING_BREADTH_CUTOFF_HOUR 之前，某市場整個空
    白算「尚未發布」而非資料錯（見該常數）。
    """
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    tj = ctx.today_json
    problems, notes, pending = [], [], []
    # 寬限只給「今天的、還沒補完的」報告；重建舊日期或已標 complete 還缺，就是真的錯。
    grace = (not tj.get("complete")
             and ctx.report_date == ctx.today
             and datetime.now(_TZ).hour < PENDING_BREADTH_CUTOFF_HOUR)
    for mkt in ("twse", "tpex"):
        b = (tj.get("breadth") or {}).get(mkt) or {}
        total = b.get("total", 0)
        if not b or total <= 0:
            what = f"{mkt}: breadth 缺" if not b else f"{mkt}: total={total}"
            (pending if grace else problems).append(what)
            continue
        s = b.get("up", 0) + b.get("down", 0) + b.get("flat", 0)
        if s > total:
            problems.append(f"{mkt}: up+down+flat={s} > total={total}")
        elif mkt == "twse" and s != total:
            problems.append(f"{mkt}: up+down+flat={s} ≠ total={total}（自算欄位應嚴格相等）")
        elif s != total:
            gap = (total - s) / total * 100
            note = f"{mkt}: 未歸類 {total - s} 家（{gap:.1f}%，官方 total 含未成交/無比價）"
            (notes if gap <= BREADTH_UNCLASSIFIED_WARN_PCT else problems).append(note)
        if b.get("limit_up", 0) > b.get("up", 0):
            problems.append(f"{mkt}: limit_up={b['limit_up']} > up={b['up']}")
        if b.get("limit_down", 0) > b.get("down", 0):
            problems.append(f"{mkt}: limit_down={b['limit_down']} > down={b['down']}")
    taiex = tj.get("taiex") or {}
    if tj.get("complete"):
        if not taiex.get("close"):
            problems.append("complete=true 但 taiex.close 缺/為 0")
        if not tj.get("top_gainers") or not tj.get("top_losers"):
            problems.append("complete=true 但 top_gainers/top_losers 空")
    if problems:
        return FAIL, "today.json 結構異常：" + "；".join(problems)
    tw = (tj.get("breadth") or {}).get("twse") or {}
    counted = (f"上市 {tw['total']} 檔 = 漲 {tw['up']} + 跌 {tw['down']} + 平 {tw['flat']}，"
               if tw.get("total") else "")
    base = f"today.json {tj['date']} 結構正常（{counted}加權 {taiex.get('close', '—')}）"
    if pending:
        notes.append(f"{'、'.join(pending)} — partial 報告，資料尚未發布"
                     f"（寬限至台北 {PENDING_BREADTH_CUTOFF_HOUR}:00，下一班補）")
    if notes:
        return PASS, base + " ｜ " + "；".join(notes)
    return PASS, base


def check_today_json_not_stale_partial(ctx: Ctx):
    """partial today.json 卡住：完整資料早該出來卻還是 complete=false。
    completeness gate 是刻意讓它降級的，但降級不該持續超過當個交易日。"""
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    tj = ctx.today_json
    if tj.get("complete"):
        return PASS, f"today.json {tj['date']} complete=true"
    rd = ctx.report_date
    if not ctx.holidays_ready(rd.year, ctx.today.year):
        return SKIP, "假日表 cache 不可用，無法判斷是否已過該交易日"
    expected = market_calendar.get_latest_trading_day()
    if rd >= expected:
        return PASS, (f"today.json {rd} 仍是 partial，但那就是最新交易日 "
                      f"（{expected}），法人資料未就緒屬正常")
    return WARN, (f"today.json {rd} complete=false，但最新交易日已是 {expected} "
                  f"— partial 卡住，法人資料一直沒補上")


def check_report_html_present(ctx: Ctx):
    """完整報告的產物檢查：HTML 存在、非空殼、且 index.html 有連進去。
    renderer / index_builder 半途失敗時，today.json 還是會是 complete=true。"""
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    tj = ctx.today_json
    if not tj.get("complete"):
        return SKIP, f"today.json {tj['date']} 是 partial，沒有對應的完整報告"
    rd = tj["date"]
    html = REPORTS_DIR / f"{rd}.html"
    if not html.exists():
        return _missing(str(html))
    size = html.stat().st_size
    if size < 50_000:      # 實測完整報告約 600KB；50KB 以下必是渲染半殘
        return FAIL, f"{html.name} 只有 {size:,} bytes（門檻 50,000）— 疑似渲染不完整"
    if not INDEX_HTML.exists():
        return _missing(str(INDEX_HTML))
    if rd not in INDEX_HTML.read_text(encoding="utf-8", errors="replace"):
        return FAIL, f"output/index.html 沒有提到 {rd} — index_builder 沒重建"
    return PASS, f"reports/{rd}.html {size:,} bytes，且 index.html 已收錄"


def check_price_window_coverage(ctx: Ctx):
    """趨勢窗口完整度。跟 market_trend 用同一個 _MIN_COVERAGE，但這裡是在
    **部署之前**看：CI 的 gh-pages 還原失敗時 20MA / 52 週新高低會「合理但錯」
    （2026-07-30 踩過：42.7% vs 正確 12.4%）。生產端會標 degraded 並顯示紅色
    橫幅，所以覆蓋率不足只 WARN，不擋部署。

    但**窗口本身縮到只剩幾天**是另一回事，必須 FAIL：比率的分母是窗口起點
    到報告日的工作日數，窗口塌成 1 天時分子分母一起縮 → 1/1 = 100% 過關
    （2026-08-03 踩過：gh-pages 還原失敗 → 冷啟動 → 這條 PASS → force_orphan
    部署把 282 天快取和全部歷史報告抹光）。部署不可逆，所以絕對天數要獨立看。"""
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    rd = ctx.report_date
    window = price_cache.load_window(rd, 252 + 30)
    if not window:
        return (FAIL, f"output/data/prices/ 沒有任何 {rd} 之前的快照 — "
                      f"gh-pages 還原必定失敗了，部署會把線上快取清空")
    cov = price_cache.window_coverage(window, rd)
    recent = price_cache.window_coverage(window[-20:], rd)["pct"]
    worst = min(cov["pct"], recent)
    msg = (f"price cache {cov['got']}/{cov['expected']} 個交易日"
           f"（全窗 {cov['pct']}% / 近20日 {recent}%，門檻 {_MIN_COVERAGE}%）")
    if cov["got"] < _MIN_WINDOW_DAYS:
        return FAIL, msg + (f" — 實得天數低於 {_MIN_WINDOW_DAYS}，窗口已塌陷"
                            f"（比率會假性 100%）；八成是 gh-pages 還原失敗，"
                            f"部署會抹掉線上歷史")
    if worst < _MIN_COVERAGE:
        return WARN, msg + " — 趨勢指標會被標 degraded；CI 請確認 gh-pages 還原成功"
    return PASS, msg


def check_inst_flow_window_coverage(ctx: Ctx):
    """法人流向窗口完整度（第 7 區塊）。

    只 WARN，不 FAIL：真正致命的「gh-pages 還原失敗 → 部署抹掉線上快取」已經由
    check_price_window_coverage FAIL 擋住了（兩份快取住在同一個 output/，一起還原
    也一起消失），這裡再 FAIL 一次只是多一條擋部署的理由。而且這份快取是後加的，
    首次上線與回補中途本來就會薄。生產端窗口不足會自己標 degraded + 紅色橫幅。

    比率之外一樣看絕對天數（分母會跟著縮的老問題），但兩者都只到 WARN。"""
    window = inst_flow_cache.load_window(ctx.report_date, 20)
    if not window:
        return WARN, ("output/data/inst_flow/ 一片空白 — 第 7 區塊會顯示暫無資料；"
                      "跑 python backfill_inst.py 回補")
    cov = price_cache.window_coverage(window, ctx.report_date)
    msg = f"inst_flow {cov['got']}/{cov['expected']} 個交易日（{cov['pct']}%）"
    if cov["got"] < _MIN_FLOW_DAYS:
        return WARN, msg + f" — 少於 {_MIN_FLOW_DAYS} 天，加速度不判、5/20 日累計偏小"
    if cov["pct"] < _MIN_COVERAGE:
        return WARN, msg + " — 窗口有洞，累計值偏小；CI 請確認 gh-pages 還原成功"
    return PASS, msg


def check_exrights_cache_fresh(ctx: Ctx):
    """exrights.csv 是還原權息鏈與除息日漲跌幅基準的唯一輸入，而且它跟
    price_cache 一樣靠 gh-pages 持久化 → 還原失敗會靜默退化成「未還原 + 假 0%」。"""
    if not TODAY_JSON.exists():
        return _missing(str(TODAY_JSON))
    if not EXRIGHTS_CSV.exists():
        return _missing(str(EXRIGHTS_CSV))
    rows, bad_ref, latest = 0, 0, None
    with EXRIGHTS_CSV.open(encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 3 or row[0] == "date":
                continue
            rows += 1
            try:
                d = date.fromisoformat(row[0])
                if float(row[2]) <= 0:
                    bad_ref += 1
            except ValueError:
                bad_ref += 1
                continue
            if latest is None or d > latest:
                latest = d
    if rows == 0 or latest is None:
        return FAIL, f"{EXRIGHTS_CSV.name} 解不出任何有效列（{rows} 列）"
    if bad_ref:
        return FAIL, f"{EXRIGHTS_CSV.name} 有 {bad_ref}/{rows} 列參考價無效（≤0 或解析失敗）"
    age = (ctx.report_date - latest).days
    msg = (f"{EXRIGHTS_CSV.name} {rows:,} 列，最新 {latest}，距報告日 {ctx.report_date} "
           f"{age} 天（門檻 {EXRIGHTS_STALE_DAYS}）")
    if age > EXRIGHTS_STALE_DAYS:
        return WARN, msg + " — cache 可能沒從 gh-pages 還原，或 exrights.update() 持續失敗"
    return PASS, msg


# ══════════════════════════════════════════════════════════════════════════
# Tier B — 交叉源（共用 3 次外部呼叫：MI_INDEX / T86 / BFI82U）
# ══════════════════════════════════════════════════════════════════════════
def check_t86_vs_bfi82u(ctx: Ctx):
    """T86（by stock，單位=股）Σ × 收盤價  ≈  BFI82U（彙總，單位=元）。

    兩個完全獨立的端點，維度也不同（股數 vs 金額），所以單位換算錯、漏股、
    parse 壞、universe 抓錯都會現形。差異的唯一合法來源是「收盤價 ≠ 成交均價」。
    收盤價取自 MI_INDEX 的每日收盤行情表（同樣是 ALLBUT0999 universe，跟 T86
    一對一），不是 pipeline 用的 STOCK_DAY_ALL —— 刻意換源。
    """
    t86 = ctx.t86()
    bfi = ctx.bfi82u()
    rows, fidx = ctx.mi_quotes()

    closes = {}
    ci = fidx["收盤價"]
    for r in rows:
        code = str(r[0]).strip()
        c = parse_num(r[ci])
        if code and c > 0:
            closes[code] = c

    tf = t86.get("fields") or []
    buy_i = [i for i, n in enumerate(tf) if "買進股數" in n]
    sell_i = [i for i, n in enumerate(tf) if "賣出股數" in n]
    net_i = next((i for i, n in enumerate(tf) if n.startswith("三大法人買賣超")), None)
    if not buy_i or not sell_i or net_i is None:
        return FAIL, f"T86 欄位無法辨識：{tf}"

    t_buy = t_sell = t_net = 0.0
    no_price = 0
    for r in t86.get("data") or []:
        p = closes.get(str(r[0]).strip())
        if not p:
            no_price += 1
            continue
        t_buy += sum(parse_num(r[i]) for i in buy_i) * p
        t_sell += sum(parse_num(r[i]) for i in sell_i) * p
        t_net += parse_num(r[net_i]) * p

    bl = {str(x[0]).strip(): x for x in bfi.get("data") or []}
    total = bl.get("合計")
    if not total:
        return FAIL, f"BFI82U 找不到『合計』列：{list(bl)}"
    b_buy, b_sell, b_net = (parse_num(total[1]), parse_num(total[2]), parse_num(total[3]))
    if b_buy <= 0:
        return FAIL, f"BFI82U 合計買進金額 = {b_buy}，無法比對"

    d_buy = (t_buy - b_buy) / b_buy * 100
    d_sell = (t_sell - b_sell) / b_sell * 100 if b_sell else 0.0
    d_net = (t_net - b_net) / b_buy * 100      # 用買進總額正規化，避免 net≈0 時爆掉

    msg = (f"{ctx.report_date} T86Σ vs BFI82U：買進 {t_buy/1e8:,.0f}億 vs "
           f"{b_buy/1e8:,.0f}億 ({d_buy:+.2f}%)，賣出 {t_sell/1e8:,.0f}億 vs "
           f"{b_sell/1e8:,.0f}億 ({d_sell:+.2f}%)，淨額 {t_net/1e8:,.0f}億 vs "
           f"{b_net/1e8:,.0f}億 (差額佔買進總額 {d_net:+.2f}%)；"
           f"門檻 gross ±{T86_GROSS_TOL_PASS}% / net ±{T86_NET_TOL_PASS}%"
           f"，{no_price} 檔無收盤價")
    worst_gross = max(abs(d_buy), abs(d_sell))
    if worst_gross > T86_GROSS_TOL_WARN or abs(d_net) > T86_NET_TOL_WARN:
        return FAIL, msg
    if worst_gross > T86_GROSS_TOL_PASS or abs(d_net) > T86_NET_TOL_PASS:
        return WARN, msg
    return PASS, msg


def check_taiex_close_vs_official(ctx: Ctx):
    """today.json 的加權指數收盤 vs MI_INDEX 價格指數表。pipeline 走
    MI_5MINS_HIST（取最後一筆），這裡走 MI_INDEX —— 不同端點，數字必須一致。"""
    t = ctx.mi_table(lambda t: (t.get("fields") or [None])[0] == "指數")
    row = next((r for r in t["data"] if "發行量加權股價指數" in str(r[0])), None)
    if row is None:
        raise Skip("MI_INDEX 價格指數表找不到『發行量加權股價指數』")
    official = parse_num(row[1])
    ours = parse_num((ctx.today_json.get("taiex") or {}).get("close"))
    if ours <= 0:
        return SKIP, "today.json taiex.close 為空（partial 報告）"
    diff = ours - official
    pct = diff / official * 100 if official else 0.0
    msg = (f"{ctx.report_date} 加權指數 today.json {ours:,.2f} vs MI_INDEX "
           f"{official:,.2f}（差 {diff:+.2f} / {pct:+.3f}%，門檻 {TAIEX_TOL_PASS} 點）")
    if abs(diff) <= TAIEX_TOL_PASS:
        return PASS, msg
    if abs(pct) <= TAIEX_TOL_WARN_PCT:
        return WARN, msg
    return FAIL, msg


def _recount_from_mi(ctx: Ctx, use_ex_refs: bool) -> dict:
    """用 MI_INDEX 每日收盤行情表重算上市漲跌家數，universe 對齊 market_breadth
    （排除權證、收盤價 > 0）。除息列的『漲跌(+/-)』是 'X' → 用除權息參考價定方向。"""
    rows, f = ctx.mi_quotes()
    ci, si, di = f["收盤價"], f["漲跌(+/-)"], f["漲跌價差"]
    refs = ctx.ex_refs if use_ex_refs else {}
    c = {"up": 0, "down": 0, "flat": 0, "total": 0}
    for r in rows:
        code, name = str(r[0]).strip(), str(r[1]).strip()
        close = parse_num(r[ci])
        if close <= 0 or is_warrant(code, name):
            continue
        c["total"] += 1
        sign = _strip_tags(r[si])
        diff = parse_num(r[di])
        ch = diff if sign == "+" else (-diff if sign == "-" else 0.0)
        ref = refs.get(code)
        if ref and ref > 0:
            ch = close - ref
        c["up" if ch > 0 else ("down" if ch < 0 else "flat")] += 1
    return c


def check_breadth_vs_official(ctx: Ctx):
    """today.json 上市漲跌家數 vs MI_INDEX 重算。pipeline 的來源是
    STOCK_DAY_ALL（CSV），這裡是 MI_INDEX（JSON）—— CSV 被截斷、欄位錯位、
    符號吃錯都會讓兩邊分家。"""
    ours = (ctx.today_json.get("breadth") or {}).get("twse") or {}
    if not ours.get("total"):
        return SKIP, "today.json 沒有上市 breadth（partial 報告）"
    theirs = _recount_from_mi(ctx, use_ex_refs=True)
    diffs = {k: ours.get(k, 0) - theirs[k] for k in ("up", "down", "flat", "total")}
    worst = max(abs(v) for v in diffs.values())
    tol_warn = max(BREADTH_ABS_TOL_PASS, theirs["total"] * BREADTH_TOL_WARN_PCT / 100)
    msg = (f"{ctx.report_date} 上市漲跌家數 today.json "
           f"{ours['up']}/{ours['down']}/{ours['flat']}(共{ours['total']}) vs "
           f"MI_INDEX 重算 {theirs['up']}/{theirs['down']}/{theirs['flat']}"
           f"(共{theirs['total']})，最大差 {worst} 家"
           f"（PASS 門檻 {BREADTH_ABS_TOL_PASS}，WARN 門檻 {tol_warn:.0f}）")
    if worst <= BREADTH_ABS_TOL_PASS:
        return PASS, msg
    if worst <= tol_warn:
        return WARN, msg
    return FAIL, msg


def check_exdiv_ref_coverage(ctx: Ctx):
    """交易所把除息/減資/面額變更當日標成『無比價』（漲跌欄 = 'X'）。這些個股
    我們必須在 exrights.csv 有參考價，否則漲跌幅靜默變成假 0%
    （2026-07-30 那批 bug 的源頭）。抽驗列出最多 3 檔沒 ref 的。"""
    rows, f = ctx.mi_quotes()
    ci, si = f["收盤價"], f["漲跌(+/-)"]
    x_stocks = []
    for r in rows:
        code = str(r[0]).strip()
        if _strip_tags(r[si]) == "X" and is_stock_code(code) and parse_num(r[ci]) > 0:
            x_stocks.append((code, str(r[1]).strip()))
    if not x_stocks:
        return SKIP, f"{ctx.report_date} 沒有標記『無比價(X)』的 4 碼個股，無從抽驗"
    refs = ctx.ex_refs
    covered = [c for c, _ in x_stocks if refs.get(c)]
    uncovered = [(c, n) for c, n in x_stocks if not refs.get(c)]
    pct = len(covered) / len(x_stocks) * 100
    sample = "、".join(f"{c} {n}" for c, n in uncovered[:SAMPLE_LIMIT])
    msg = (f"{ctx.report_date} 交易所標無比價的 4 碼個股 {len(x_stocks)} 檔，"
           f"exrights 有參考價 {len(covered)} 檔（{pct:.0f}%，WARN 門檻 "
           f"{EXDIV_COVERAGE_WARN_PCT}%）"
           + (f"；缺 ref：{sample}" if uncovered else ""))
    if not covered and len(x_stocks) >= EXDIV_COVERAGE_MIN_ROWS_FOR_FAIL:
        return FAIL, msg + " — 一檔都沒有，exrights 抓取整組失敗"
    if pct < EXDIV_COVERAGE_WARN_PCT:
        return WARN, msg
    return PASS, msg


def check_exdiv_not_fake_flat(ctx: Ctx):
    """2026-07-30『除息 X → 假平盤』的迴歸測試。

    重算兩次上市持平家數：naive（完全照交易所漲跌欄，除息股一律 flat）與
    ref-aware（有參考價就用 close vs ref）。today.json 必須等於 ref-aware；
    等於 naive 而不等於 ref-aware，就代表 ex_refs 沒有傳進 market_breadth，
    bug 復發。這是對**產出**的檢查，不是對函數的檢查。
    """
    ours = (ctx.today_json.get("breadth") or {}).get("twse") or {}
    if not ours.get("total"):
        return SKIP, "today.json 沒有上市 breadth（partial 報告）"
    naive = _recount_from_mi(ctx, use_ex_refs=False)["flat"]
    aware = _recount_from_mi(ctx, use_ex_refs=True)["flat"]
    mine = ours["flat"]
    if naive == aware:
        return SKIP, (f"{ctx.report_date} 當日沒有『有參考價且收盤 ≠ 參考價』的個股"
                      f"（naive flat = ref-aware flat = {aware}），這條區分不出差異")
    msg = (f"{ctx.report_date} 上市持平家數 today.json {mine}，"
           f"ref-aware 重算 {aware}，naive（吃交易所漲跌欄）{naive}")
    if mine == aware:
        return PASS, msg + " — 除權息基準有生效"
    if mine == naive:
        return FAIL, msg + " — 等於 naive，除息股被算成假平盤（7/30 bug 復發）"
    return FAIL, msg + " — 三者都不同，漲跌基準邏輯與兩種算法都對不上"


# ══════════════════════════════════════════════════════════════════════════
TIER_A = [
    ("today-date-expected-trading-day", check_today_date_expected_trading_day),
    ("today-json-structure",            check_today_json_structure),
    ("today-json-not-stale-partial",    check_today_json_not_stale_partial),
    ("report-html-present",             check_report_html_present),
    ("price-window-coverage",           check_price_window_coverage),
    ("exrights-cache-fresh",            check_exrights_cache_fresh),
    ("inst-flow-window-coverage",       check_inst_flow_window_coverage),
]

TIER_B = [
    ("t86-vs-bfi82u",          check_t86_vs_bfi82u),
    ("taiex-close-vs-official", check_taiex_close_vs_official),
    ("breadth-vs-official",    check_breadth_vs_official),
    ("exdiv-ref-coverage",     check_exdiv_ref_coverage),
    ("exdiv-not-fake-flat",    check_exdiv_not_fake_flat),
]


def run(checks, ctx) -> list[tuple[str, str, str]]:
    out = []
    for check_id, fn in checks:
        try:
            status, msg = fn(ctx)
        except Skip as e:
            status, msg = SKIP, str(e)
        except BudgetExceeded as e:
            status, msg = SKIP, str(e)
        except Exception as e:
            status = FAIL
            msg = f"檢查本身拋例外：{type(e).__name__}: {e}"
            traceback.print_exc(file=sys.stderr)
        out.append((status, check_id, msg))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="twse_website 資料驗證")
    ap.add_argument("--tier", choices=["a", "b", "all"], default="all")
    args = ap.parse_args()

    ctx = Ctx()
    checks = []
    if args.tier in ("a", "all"):
        checks += TIER_A
    if args.tier in ("b", "all"):
        checks += TIER_B

    results = run(checks, ctx)
    for status, check_id, msg in results:
        print(f"[{status}] {check_id} — {msg}")

    n = {s: sum(1 for r in results if r[0] == s) for s in (PASS, FAIL, WARN, SKIP)}
    print(f"verify: {n[PASS]} passed, {n[FAIL]} failed, {n[WARN]} warned, "
          f"{n[SKIP]} skipped (tier={args.tier})")

    if n[FAIL]:
        return 1
    if n[WARN]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
