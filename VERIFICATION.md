# 資料正確性驗證

> 2026-08-01 規劃、2026-08-02 實作。四專案通用框架（Tier A 每次 CI / Tier B 每日交叉源 / Tier C golden）。
> 本檔描述**現況**，不是計畫。
> 本站已有最完整的 runtime guard（completeness gate / garbage-date / degraded coverage），`verify.py` 是補「交叉源互驗」，不重複實作那些。

## 怎麼跑

```bash
python verify.py --tier a      # 零外部呼叫
python verify.py --tier b      # 交叉源，會打外部 API
python verify.py               # 預設 all
```

exit code：`0` 全過（或只有 SKIP）／`1` 至少一條 FAIL／`2` 沒 FAIL 但有 WARN。CI 只把 `1` 當失敗。

本站資料在 `output/`（從 gh-pages 還原）。本機執行時 `output/` 可能不完整，該情況回 SKIP 而非 FAIL。

## CI 掛法

- **Tier A** → `.github/workflows/daily_update.yml`，獨立 step，位置在 `update.py` 之後、gh-pages 部署之前。FAIL 擋掉部署並讓 workflow 變紅。
- **Tier B** → `.github/workflows/verify.yml`，獨立排程 + `workflow_dispatch`，會先從 gh-pages 還原 `output/` 再驗。

### ⚠️ 順手修掉的結構問題（2026-08-02）

`daily_update.yml` 原本用 `set +e` 跑 `update.py`，把 exit code 存進 `steps.generate.outputs.result` 只印訊息——**所以 update.py 失敗時 workflow 仍然是綠的**，「fail → workflow 紅 → GitHub 寄信」這個假設在本 repo 整個是斷的。

現在的規則：

- `exit 0` = 有新產出 → 繼續驗證與部署
- `exit 2` = 非交易日／資料未就緒 → **保持綠燈安靜重試**（這個語意是刻意的，別讓非交易日變紅燈）
- 其餘（`exit 1` = 非預期錯誤）→ `::error::` + 中止，workflow 變紅

## 紅燈了怎麼辦

**資料不會壞。**FAIL 只擋部署，線上維持前一份好的報告；這一輪的產出直接丟棄，下一輪重新抓、重新驗。暫時性的上游抽風會自我修復，你什麼都不用做。

判斷成因看 FAIL 訊息裡的實際值與門檻：

- 數字接近門檻 → 上游延遲或門檻訂太緊 → 調門檻或不理它
- 數字差很遠 → 我們的解析/計算壞了（多半是上游改版）→ 修程式
- `檢查本身拋例外` → 上游欄位改名/消失 → 修 fetcher

本機重現要先還原線上狀態，否則 `output/` 不全會得到一堆假訊號：

```bash
git fetch origin gh-pages && git archive origin/gh-pages | tar -x -C output/
python verify.py --tier a
```

### 逃生門

確定是假警報、但門檻還沒空修時，跳過驗證直接部署：

```bash
gh workflow run --repo user01ju/twse_website daily_update.yml -f skip_verify=true
```

外部 cron 那條路徑帶 `client_payload.skip_verify=1` 同效。

### 告警降噪（本 repo 特有）

本 workflow 平日 15:00–23:30 每 30 分跑一輪。上游一旦改版導致每輪都 FAIL，**一天會寄出最多 17 封同樣的信**——第二天你就會開始無視它，這套告警等於死了。告警疲勞比沒告警更糟，因為你以為有在看。

所以「擋部署」與「發紅燈」刻意分開：

- **擋部署**：FAIL 一律擋，透過 `steps.verify.outputs.ok` 傳給部署步驟。
- **發紅燈**：查今天本 workflow 是否已失敗過（`gh run list --status failure`），已經紅過就只留 `::warning::` 不再讓 workflow 變紅。**每天上限一封信**，隔天沒修好會再紅一次，形成每日提醒節奏。

刻意**不**做熔斷（今天紅過就整天不再跑）：盤後資料齊全時間不定，重試迴圈是本專案自我修復的機制，停掉它會讓暫時性失敗變成整天停更。

副作用是「job 綠燈但沒部署」的狀態會存在。最後的「輸出執行結果」步驟會明講這件事，log 裡也有 warning。

## Tier A（6 條，零外部呼叫）

| check-id | 驗什麼 |
|---|---|
| `today-date-expected-trading-day` | `today.json` 日期 = 假日表推出的預期交易日 |
| `today-json-structure` | 上市/上櫃家數自洽（漲+跌+平 = 總數）、指數存在 |
| `today-json-not-stale-partial` | `complete` 旗標不得是 partial |
| `report-html-present` | 當日 HTML 產出存在且被 index 收錄 |
| `price-window-coverage` | price cache 覆蓋率（全窗 / 近 20 日門檻 90%） |
| `exrights-cache-fresh` | exrights.csv 最新日期距報告日 ≤30 天 |

`today-date-expected-trading-day` 用本 repo 的 `market_calendar`（2026-08-01 修好後才可靠）。本 repo 是四個 repo 裡唯一有真正假日表的。

`price-window-coverage` 對應既有的 degraded coverage guard：price cache 不全時趨勢數字會「合理但錯」（2026-07-30 踩過：20MA 42.7% vs 正確 12.4%）。

## Tier B（5 條，交叉源）

| check-id | 驗什麼 |
|---|---|
| `t86-vs-bfi82u` | **T86 個股淨買賣 Σ ≈ BFI82U 彙總** |
| `taiex-close-vs-official` | 加權指數 close vs MI_INDEX |
| `breadth-vs-official` | 漲跌家數 vs 官方市場統計端點重算 |
| `exdiv-ref-coverage` | 交易所標無比價的個股，exrights 要有參考價 |
| `exdiv-not-fake-flat` | 除息日 `change_pct` 不得為假 0 |

**`t86-vs-bfi82u` 是四個 repo 裡最有價值的一條**：兩個獨立端點互驗，單位錯、漏股、parse 壞都會現形。門檻 gross ±3% / net ±1%（T86 是個股明細加總、BFI82U 是官方彙總，兩者本來就有零股與盤後定價的細微差異）。實測 2026-07-31：買進 5,003億 vs 4,988億（+0.31%）。

`exdiv-not-fake-flat` 是 2026-07-30「除息 X → 假平盤」bug 的迴歸測試（見 llm_wiki: twse-exdiv-change-x-zero-pct）。做法是比對 ref-aware 重算的持平家數與 naive（直接吃交易所漲跌欄）的差異，確認除權息基準有生效。

外部源掛掉／超時／非 200 一律 SKIP。呼叫上限 3 次、間隔 ≥1 秒。

## Cross-repo

原規劃「cross-repo 週檢建議掛本 repo（CI 慣例最成熟）」**沒有實作，也不打算做**。三條互驗已分散進各 repo 的 Tier B：

1. 加權指數 close（本站 `today.json` ↔ sector_gainer `data/market_index.csv`）→ sector_gainer 的 `index-close-vs-twse-website`
2. 月底收盤（financial_report `data/prices.json` ↔ sector_gainer `data/daily/*.csv`）→ financial_report 的 `price-return-vs-sector-gainer`
3. exrights 超集（本站 `output/data/exrights.csv` ⊃ sector_gainer `data/exrights.csv`）→ sector_gainer 的 `exrights-vs-twse-website`

四個 repo 資料都公開，全走 `raw.githubusercontent`，不需 sibling checkout。多專案各自實作同一份資料的抓取、互相比對，等於免費的 double-entry bookkeeping——但只需要實作一次，掛在哪一側都一樣。

## 沒做的

**Tier C golden regression**：`rebuild.bat` 固定歷史日 → diff `today.json`（或整份 HTML）。這輪刻意沒做，成本效益也最差——⚠️ 要先還原 gh-pages state 再跑：

```bash
git fetch origin gh-pages && git archive origin/gh-pages | tar -x -C output/
```

否則 price_cache 不全，趨勢數字會「合理但錯」，golden 反而 diff 出一堆假警報（2026-07-30 踩過）。

## ⚠️ 未驗證的前提（本 repo 最重要的未知數）

整套的告警依賴「FAIL → exit 1 → workflow 紅 → GitHub 寄信」。**這條路徑還沒實測過。**

而且本 repo 的風險最高：GitHub 的 workflow 失敗通知是綁 **scheduled run** 的，但本 repo 主要靠外部 cron-job.org 打 `repository_dispatch` 觸發（因為公開 repo 的 schedule 延遲嚴重）。那條觸發路徑失敗會不會寄信**未確認**。若不會，上面所有檢查都收不到告警，等於白寫。

驗法：push 之後故意讓一條檢查失敗，分別用 schedule 與 repository_dispatch 各觸發一次，確認信有沒有進來。
