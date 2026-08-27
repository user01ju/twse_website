# 台股每日行情靜態網站

每日自動從 TWSE / TPEX 公開 API 抓取收盤資料，生成靜態 HTML 報告。
CI（GitHub Actions）跑完後以 `peaceiris/actions-gh-pages` 部署到 GitHub Pages；本機可用 `python server.py` 在 `localhost:8080` 預覽。

## 功能

- **加權指數**：收盤指數、漲跌點數與百分比
- **市場統計**：上市 / 上櫃 / 興櫃 漲跌筆數、漲跌停計數、漲跌幅分布直方圖
- **市場趨勢**：收盤 > 20MA 比例、52 週新高低淨值（皆以還原權息序列計算，含近 30 交易日走勢）
- **三大法人彙總**：外資、投信、自營商、合計（上市 + 上櫃）
- **法人買超子類股分析彙總表**：以三大法人個股為基準，外資 / 投信 / 自營商各別金額（加總 = 三大法人）
- **法人賣超子類股分析彙總表**：同上，賣超方向
- **法人買超 / 賣超子類股分析**（CMoney 分類）：外資 / 投信 / 自營商 / 三大法人四頁籤
- **今日盤勢總覽**：AI（Claude Opus）摘要
- **漲幅 / 跌幅前 100 個股**：上市 + 上櫃合併，DataTables 可排序
- **漲跌幅前 100 的子類股分布**（CMoney 分類）
- **法人買賣超個股**：外資 / 投信 / 三大法人，按金額取前 100 名
- **首頁即時快照** `output/today.json`：每次執行都寫，法人資料未就緒時先出「更新中」版本

## 架構

```
twse_website/
├── config.py               # API 端點、路徑常數
├── update.py               # 一次性執行：抓資料 → 生成報告
├── scheduler.py            # 每日 15:05 自動執行 update.py
├── server.py               # localhost:8080 靜態伺服器
├── backfill_prices.py      # 回補歷史收盤到 price cache（market_trend 用）
├── backfill_inst.py        # 回補歷史法人流向（sector_flow 用，預設 60 個交易日）
├── fetcher/
│   ├── twse_client.py      # TWSE legacy API
│   ├── tpex_client.py      # TPEX OpenAPI
│   ├── exrights.py         # 除權息/減資/面額變更參考價（還原權息）
│   ├── price_cache.py      # 每日收盤 OHLC 快取（output/data/prices/）
│   ├── inst_flow_cache.py  # 每日法人淨買賣超快取（子類股層 data/inst_flow/ + 個股層 data/inst_stock/）
│   ├── shares.py           # 發行股數快取（data/shares.json，市值換算用；只有最新快照）
│   └── market_calendar.py  # 交易日判斷、ROC 日期轉換
├── processor/
│   ├── index_stats.py      # 加權指數
│   ├── market_breadth.py   # 漲跌統計（TWT84U 漲跌停）+ 漲跌幅分布
│   ├── market_trend.py     # 20MA breadth、52 週新高低（還原權息序列）
│   ├── sector_flow.py      # 5/20 日資金流向（子類股層 + 個股層）、連續天數、加速度
│   ├── movers.py           # 漲跌幅前 100
│   ├── mover_sector.py     # 漲跌幅前 100 的子類股分布
│   ├── institutional.py    # 三大法人彙總
│   ├── foreign_trades.py   # 外資買賣超
│   ├── trust_trades.py     # 投信買賣超
│   ├── combined_inst.py    # 三大法人合計買賣超
│   ├── dealer_trades.py    # 自營商買賣超
│   ├── sector_inst.py      # 子類股法人分析（CMoney 分類）
│   └── ai_summary.py       # AI 摘要
├── generator/
│   ├── report_builder.py   # 並行抓資料 + 組裝 sections + 渲染
│   ├── today_builder.py    # output/today.json（首頁快照，永不降級）
│   ├── index_builder.py    # 重建 index.html
│   └── renderer.py         # Jinja2 環境與 render 函式
├── templates/
│   ├── base.html
│   ├── report.html
│   ├── index.html.j2
│   └── partials/           # 各區塊 HTML 片段
├── static/
│   ├── css/style.css
│   └── js/report.js
├── cmoney_raw.json         # CMoney 子類股對照表（87 類、1965 支股票）
└── output/                 # 生成的靜態網站（不納入版控，部署到 gh-pages）
    ├── index.html
    ├── today.json
    ├── data/prices/YYYY-MM-DD.json   # 收盤快取，隨 gh-pages 持久化
    ├── data/exrights.csv             # 除權息參考價快取
    └── reports/YYYY-MM-DD.html
```

CI runner 是 ephemeral，所以每次執行會先從 `gh-pages` 還原 `output/`（歷史報告 + price/exrights 快取），跑完再整份重推（`force_orphan: true`，不留 gh-pages 歷史）。

## 資料來源

| 資料 | 來源 |
|------|------|
| 加權指數 | TWSE `MI_5MINS_HIST` |
| 上市個股行情 | TWSE `STOCK_DAY_ALL` |
| 漲跌停參考價 | TWSE `TWT84U` |
| 上市三大法人彙總 | TWSE `BFI82U` |
| 上市三大法人個股 | TWSE `T86` |
| 上櫃行情 | TPEX `tpex_mainboard_daily_close_quotes` |
| 上櫃漲跌統計 | TPEX `tpex_mainborad_highlight` |
| 上櫃三大法人彙總 | TPEX `tpex_3insti_summary` |
| 上櫃外資買賣超 | TPEX `tpex_3insti_qfii_trading` |
| 上櫃投信買賣超 | TPEX `tpex_3insti_trading` |
| 上櫃三大法人合計 | TPEX `tpex_3insti_daily_trading` |
| 興櫃行情 | TPEX `tpex_esb_latest_statistics` |
| 休市日曆 | TWSE `holidaySchedule`（`year` 參數無效，永遠回當年度） |
| 上市除權息參考價 | TWSE `TWT49U` |
| 上市減資／面額變更參考價 | TWSE `TWTAUU` / `TWTB8U` |
| 上櫃除權息／減資參考價 | TPEX `exDailyQ` / `revivt` |
| 子類股分類 | CMoney（本地 `cmoney_raw.json`） |

## 安裝與執行

```bash
pip install -r requirements.txt

# 手動生成今日報告
python update.py

# 啟動靜態伺服器（localhost:8080）
python server.py

# 啟動排程（每日 15:05 自動抓資料）
python scheduler.py
```

### 環境變數（`.env`）

```
ANTHROPIC_API_KEY=sk-ant-...   # AI 摘要用，無則略過
FORCE_REBUILD=false             # true 強制重建已存在的報告
```

## 技術細節

- **排序**：買賣超個股與子類股彙總均按金額（億元）排序，取前 100 名
- **漲跌停**：上市股票使用 TWT84U 官方參考價比對；上櫃直接吃官方 `tpex_mainborad_highlight` 的漲跌停家數（highlight 抓不到時才退回 ±10% heuristic）
- **還原權息**：除息／減資／面額變更當日交易所 `Change` 欄不可用（TWSE 給 `'X'`），漲跌幅、breadth、分布圖一律改以除權息參考價為基準；`market_trend` 的 20MA 與 52 週新高低則跑在還原權息累積序列上
- **標的範圍**：全站只收 4 碼普通股（排除 ETF、權證、興櫃代碼）
- **子類股彙總**：以三大法人個股為基準，外資 / 投信 / 自營商從相同個股換算（保證加總 = 三大法人）
- **資金流向（第 7 區塊）**：吃 `output/data/inst_flow/` 的每日快取，與第 5/6 區塊同一組解析器但**不套 top-100 截斷**（整個子類股的進出才是流向）；近 5 日漲跌用還原權息序列等權平均；窗口不足會標 degraded；點子類股列可展開成分股（個股層快取只留任一法人別 |淨額| ≥ 0.05 億的，砍掉六成檔數但只丟 0.6% 金額）；另有擴散度（買/賣家數、龍頭佔比）判斷這波是全族群還是單一檔撐的
- **個股資金流向（第 8 區塊）**：同一份 20 日窗口不分子類股，各法人別列出買超前 50 + 賣超前 50；加速度門檻降到 0.3 億/日（個股 20 日日均絕對值 p90 只有 1.26 億，沿用子類股的 1 億等於只有前 12% 判得到）；另有市值、「5 日買賣超 / 市值」、52 週位階、現價 vs 法人加權平均成本、外資投信一致性等欄，市值 = 報告日收盤 × 發行股數（`t187ap03_L` / `mopsfin_t187ap03_O`，覆蓋 1154/1162 檔，缺的都是 4 碼 ETF）
- **錯誤隔離**：每個 section 獨立 try/except，單一 API 失敗不影響其他區塊
- **並行抓取**：`ThreadPoolExecutor(max_workers=5)` 同時發出所有 API 請求
