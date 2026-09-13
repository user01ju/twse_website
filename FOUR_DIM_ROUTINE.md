# 法人四維透視 — 每日排程 runbook

排程任務 `four-dim-report`（Claude Code desktop scheduled task，週一～五 18:00～23:00 每小時一次，本機時間）
照這份文件跑。每一輪都是全新 session，沒有記憶，所以步驟要照抄。

固定 Artifact（每天更新同一個網址，不要開新的）：
`https://claude.ai/code/artifact/68899890-6749-400c-b5a1-9b14c6a77dbb`

工作目錄 `C:\Users\Rabbit\claude_code\twse_website`，所有 python 指令前綴 `PYTHONIOENCODING=utf-8`。

## 流程

1. `python four_dim_report.py check --sync`
   - exit **4** 非交易日、exit **5** 今天已產出 → 直接結束，不通知。
   - exit **3** 資料未齊（CI 還沒推 gh-pages）→ 結束，不通知；排程下一個整點會再來。
     只有在 **23:00 那輪**（本機時間 ≥ 23:00）仍是 exit 3 時，用 PushNotification 講一句
     「法人四維 MM-DD：資料到 23:00 仍未更新，今天跳過」。
   - exit **0** → 往下。
2. `python four_dim_report.py data` → stdout 是給你讀的文字摘要（型態分布、七組大票、連買連賣、
   外資投信分歧、子類股前 25），同時寫 `output/four_dim/<date>.json`。
   exit 3 = 快取窗口 degraded → PushNotification「法人四維 MM-DD：快取不完整，略過」後結束。
3. 讀摘要，寫敘事 JSON 到 `output/four_dim/<date>.narr.json`：
   ```json
   {
     "market": "可省略；省略時頁面自動用個股漲跌中位數",
     "dist_note": "02 分布段落的一句話：棄守佔幾成、主流延續幾檔扛多少、大票門檻",
     "labels": ["散布圖要直接標名的代號，20~24 檔，就是你下面點名的那些"],
     "groups": [ {"lead": "一句定調", "items": ["<b>可用粗體</b>，講具體股票與數字", "..."]}, ... 共 7 個，順序固定 ],
     "split": [["代號", "一句解讀：誰接誰倒"], ... 8~10 檔 ],
     "conclusions": [ {"h": "標題", "p": "兩三句"}, ... 共 4 個 ]
   }
   ```
   groups 順序：主流延續 / 拉回續買 / 追漲回補+低接轉買 / 買不漲 / 漲多轉賣+停損轉賣 / 拉高調節+反彈續賣 / 棄守。
   寫法：繁中、casual、terse、把讀者當專家。每組 2~4 條 items，每條要有代號可對照的股票名與數字
   （5 日/20 日淨額、漲跌、52w 位階、法人成本、外資投信拆分、連續天數），不要空泛形容。
   結論四則要回答：錢集中在誰、這週的新變數是什麼、賣壓有沒有止跌訊號、型態切換的速度。
   參考範例：`output/four_dim/2026-09-11.narr.json`（若存在）。
4. `python four_dim_report.py render --date <date> --narrative output/four_dim/<date>.narr.json`
   → `output/four_dim/<date>.html`。
5. 發布：先 Artifact `action: read` 上面那個 url（更新前必須先讀），再 Artifact publish
   `file_path = output/four_dim/<date>.html`、`url` = 同一個網址、`label` = `<date>`、
   `description` = 「<date> 台股三大法人個股層四維分析：…」。**不要傳 favicon**。
6. 寫 marker：建立空檔 `output/four_dim/<date>.done`（下一輪 check 會回 exit 5）。
7. PushNotification（status proactive，< 200 字元，一行）：
   「法人四維 MM-DD 出爐：<最大賣壓型態與金額>；<主流是誰>；<一個新變數> → <artifact url>」。
8. 記憶：`C:\Aiber\daily\<date>.md` 補一段「## 法人四維」3~5 行（沒有該檔就照 `C:\Aiber\_templates\daily.md` 開），
   然後在 `C:\Aiber` 做 `git add -A && git commit -m "daily <date>：法人四維" && git push`。
   **不要**動 twse_website 這個 repo 的 git（output/ 本來就在 .gitignore）。

## 為什麼這樣設計

- 重試靠「每小時一次 + done marker」而不是 sleep：session 不該掛著等，marker 讓已完成的那天後面幾輪秒退。
- `check --sync` 只還原 gh-pages 的 `data/`（不碰 reports/today.json），本機 output/ 被 rebuild_local 改過的東西不會被誤傷。
- 資料與敘事分開：數據由 `sector_flow` 算（跟網站第 5/6 區塊同口徑），敘事由 Claude 每天重寫，模板不動。
