# 台股每日行情靜態網站

每日自動從 TWSE / TPEX 公開 API 抓取收盤資料，生成靜態 HTML 報告，部署於 `localhost:8080`。

## 功能

- **加權指數**：收盤指數、漲跌點數與百分比
- **市場統計**：上市 / 上櫃 / 興櫃 漲跌筆數、漲跌停計數
- **三大法人彙總**：外資、投信、自營商、合計（上市 + 上櫃）
- **法人買超子類股分析彙總表**：以三大法人個股為基準，外資 / 投信 / 自營商各別金額（加總 = 三大法人）
- **法人賣超子類股分析彙總表**：同上，賣超方向
- **法人買超 / 賣超子類股分析**（CMoney 分類）：外資 / 投信 / 自營商 / 三大法人四頁籤
- **今日盤勢總覽**：AI（Claude Opus）摘要
- **漲幅 / 跌幅前 100 個股**：上市 + 上櫃合併，DataTables 可排序
- **法人買賣超個股**：外資 / 投信 / 三大法人，按金額取前 100 名

## 架構

```
twse_website/
├── config.py               # API 端點、路徑常數
├── update.py               # 一次性執行：抓資料 → 生成報告
├── scheduler.py            # 每日 15:05 自動執行 update.py
├── server.py               # localhost:8080 靜態伺服器
├── fetcher/
│   ├── twse_client.py      # TWSE legacy API
│   ├── tpex_client.py      # TPEX OpenAPI
│   └── market_calendar.py  # 交易日判斷、ROC 日期轉換
├── processor/
│   ├── index_stats.py      # 加權指數
│   ├── market_breadth.py   # 漲跌統計（TWT84U 漲跌停）
│   ├── movers.py           # 漲跌幅前 100
│   ├── institutional.py    # 三大法人彙總
│   ├── foreign_trades.py   # 外資買賣超
│   ├── trust_trades.py     # 投信買賣超
│   ├── combined_inst.py    # 三大法人合計買賣超
│   ├── dealer_trades.py    # 自營商買賣超
│   ├── sector_inst.py      # 子類股法人分析（CMoney 分類）
│   └── ai_summary.py       # AI 摘要
├── generator/
│   ├── report_builder.py   # 並行抓資料 + 組裝 sections + 渲染
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
└── output/                 # 生成的靜態網站（不納入版控）
    ├── index.html
    └── reports/YYYY-MM-DD.html
```

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
- **漲跌停**：上市股票使用 TWT84U 官方參考價比對，上櫃使用 ±10% heuristic
- **子類股彙總**：以三大法人個股為基準，外資 / 投信 / 自營商從相同個股換算（保證加總 = 三大法人）
- **錯誤隔離**：每個 section 獨立 try/except，單一 API 失敗不影響其他區塊
- **並行抓取**：`ThreadPoolExecutor(max_workers=5)` 同時發出所有 API 請求
