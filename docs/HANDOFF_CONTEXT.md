# Codex 接手上下文

Last updated: 2026-05-07

## 專案定位

這個 repo 不是賽馬資訊平台。目標是 HKJC 賽馬統計方程式 / 狙擊模型：

- 預測每匹馬獨贏勝率、三甲機率及排名信心。
- 將模型概率與 HKJC 市場賠率 / 可能派彩比較。
- 只在有可驗證 edge 時才輸出投注建議。
- 按彩池選擇最佳下注方式，而不是只排序最可能贏的馬。
- 每次模型或下注邏輯改動都要用 walk-forward / out-of-sample 驗證。
- 保存賽果、最終賠率、建議賠率、落注後回報，作為自我進化材料。

重要原則：

- 不可以亂改權重。
- 不可以只加 UI 當成模型進步。
- 不可以用 in-sample 結果證明升級。
- Good horse bad price = no bet。
- No edge = no bet。

## Repo / Branch

- Repo: `https://github.com/harryheung19951212-sketch/HKJC_Statistical_Model`
- Branch: `codex/horse-racing-model`
- 本機工作目錄：`C:\Users\user\Desktop\HKJC_Statistical_Model`

開發約定：

- 用本地 repo 做開發、測試、commit、push。
- server 只用來部署，不在 server clone / pull repo。
- 每次完成功能、修正或刪改功能後，要 push 到 repo，並說明改了甚麼。
- 完成後要即時部署到 server。
- 每次功能、修正或刪減都要更新 `docs/DEVELOPMENT_LOG.md`。

不要 commit：

- `.env`
- `data/racing.db`
- `data/raw`
- `data/hkjc`
- `reports`
- `models/*.json`
- API key / password / token

## Server 狀態

Production server:

- Host: `43.228.125.192`
- OS: Ubuntu 24
- Deploy path: `/opt/hkjc-model`
- App URL: `http://43.228.125.192:8765/`
- DB: PostgreSQL via Docker Compose
- App container includes Codex CLI.

不要把 root password、token 或其他秘密寫入 repo。

部署方式：

1. 本地 `git archive` 產生乾淨 tarball。
2. 用 SSH/SFTP 上傳到 server。
3. 解壓到 `/opt/hkjc-model`。
4. `docker compose -f docker-compose.prod.yml build app`
5. `docker compose -f docker-compose.prod.yml up -d`
6. 用 HTTP API 驗證。

## 必跑測試

最少：

```powershell
python -m compileall -q src dashboard tests
python tests\test_smoke.py
python tests\test_hkjc_parser.py
python tests\test_betting.py
node --check src\racing_model\web\app.js
```

目前實務上每次交付前已跑全套：

```powershell
$tests = Get-ChildItem tests\test_*.py | Sort-Object Name
foreach ($t in $tests) {
  python $t.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

## 目前已完成重點

### 賽事資料 / HKJC 接入

- HKJC racecard / results parser。
- 傳統中文馬名、騎師、練馬師優先顯示。
- 2026-05-06 沙田歷史資料已可回填及解析。
- 2026-05-09 沙田未開跑賽事可載入為 `scheduled`。
- 修正過未來賽事誤抓結果問題：未來 HKJC race day 不會被結果頁污染成已完賽。

### 賽事生命週期

- `scheduled`：未進行，可更新賠率。
- `live`：開跑 / 凍結，保留最後即時賠率。
- `resulted`：已完賽，保存賽果及最終賠率作訓練材料。
- 已完賽不能被手動降回未開跑。
- 背景自動刷新已改成 `active_race_only`：
  - 只更新使用者目前打開的場次。
  - 未打開場次休眠，減輕 server 負擔。
  - 切頁 / 關頁 / browser hidden 會令 active race 休眠。
- `全域更新` 按鈕是手動全域流程：
  - 找下一場 `scheduled` race。
  - 刷新該場 win/place odds。
  - 刷新組合可能派彩。
  - 檢查賽果是否可用。

### Web UI

- 左側賽事 menu 已改成文件夾結構：
  - `未進行`
  - `已完賽`
  - 入面再按賽事日期分類。
- `載入賽日` 及 `歷史回填` 已改成可開合分組。
- 左側新增頂層頁面 menu：
  - `賽事分析`
  - `方程式覆蓋率 / 盲點報告`
  - `回測 / 智能迭代中心`
- 覆蓋率及智能迭代中心已從每場賽事頁抽離成獨立頁面。
- 切到 coverage / analytics 頁時，不會繼續刷新當前賽事。

### 方程式覆蓋率 / 盲點報告

- 已新增 `/api/coverage`。
- 覆蓋 32 個項目：
  - 21 個主分析項。
  - 11 個額外盲點。
- 每項標示：
  - 已完成
  - 部分完成
  - 未完成
  - 需要外部數據
- 顯示：
  - 資料來源
  - 支援檔案
  - 目前支援
  - 缺口
  - 下一步
  - 模型風險
  - 驗證門檻
- 內容已盡量中文化。
- UI 改成點擊主項才展開細項。

### 模型 / 回測 / 下注

- Pairwise ranking model。
- Independent top-3 model。
- 輸出 win probability、top-3 probability、feature contribution。
- WIN / PLACE EV 判斷。
- Fractional Kelly：
  - conservative
  - standard
  - aggressive
- 單注 / 單場風險 cap。
- No-bet gate：
  - 無 edge 不下注。
  - 已開跑 / 已完賽不下注。
  - 弱 EV / 弱 edge 不下注。
- Backtest：
  - fixed stake positive-EV win/place。
  - walk-forward model version comparison。
  - calibration bins。
  - Brier / log loss。
- Model registry：
  - 記錄 out-of-sample 評估。
  - 顯示 promotion gate 狀態。
- Betting ledger：
  - 保存建議時賠率、概率、EV、edge、stake。
  - 對數最終賠率 / 賽果。
  - 計 P/L、slippage、CLV 參考。

### 組合彩池 / 可能派彩

- 已有 exotic candidates：
  - QIN
  - QPL
  - FCT
  - TRIO
  - TCE
  - FIRST4
  - QUARTET
- 已有 exotic_dividends table / lookup。
- 已支援 GraphQL / MQTT provider refresh probable dividends。
- EV ticket 會用可能派彩與模型概率計算。
- 已有 position Q vs trio upgrade 概念。

## 最近重要 commits

- `2df3cac Localize coverage report details`
- `dca07d0 Split model reports into top-level views`
- `e7f00aa Rename lifecycle button to global update`
- `f1486ed Make manual lifecycle step global`
- `a23e50b Collapse sidebar ingestion controls`
- `fa355dc Limit live refresh to active race`
- `aad238e Group race menu by status and date`
- `93ccf54 Add live exotic dividend refresh`
- `21e0100 Fix empty exotic dividend lookup`
- `080be6b Add exotic dividend EV support`

## 我同使用者形成的上下文 / 偏好

- 使用者主要用廣東話溝通，UI 亦應盡量用中文。
- 系統如非必要不應顯示英文馬名、騎師、練馬師。
- 使用者重視「模型方程式」多於資訊展示。
- 開發要跟 36 個賽馬模型主項思路走：
  - 原先 21 個主分析項。
  - 後續擴展到市場效率、臨場資金流、彩池選擇、組合派彩、即日偏差、錯誤分類、多目標優化等。
- 使用者明確要求：
  - 每次改完要 push repo。
  - 每次改完要說明改了甚麼。
  - 功能完成要立即部署到 server。
  - server 只用來部署，本機 repo 做開發。
- UI 方向：
  - 避免所有內容塞在單一賽事頁。
  - 重要報告應獨立成頁。
  - menu 太長時要用文件夾 / 開合結構。
  - 即時資訊只刷新使用者正在看的場次。

## 仍未完成 / 未做事項

### 最高優先

1. 真實賽日驗證 live odds / probable dividend feed
   - MQTT / GraphQL provider 已有骨架，但需要真實賽日跑通。
   - 要記錄最後 5 分鐘、2 分鐘、30 秒 odds movement。
   - 要分辨 smart money vs public chase。

2. Final exotic dividend settlement
   - 目前可用 probable / estimated dividends。
   - 仍未完整自動結算 final exotic dividends。
   - 組合票 ledger / P/L / slippage 仍未完整。

3. Pool takeout / market efficiency
   - 未有正式彩池抽水成本表。
   - EV 仍未完整扣除不同彩池成本及市場效率 noise buffer。

4. Pool selection model
   - 已可產生組合候選，但未真正做到跨 WIN / PLA / QIN / QPL / TRIO / TCE / FIRST4 / QUARTET 的最佳彩池選擇。
   - 需要用 hit probability × probable dividend × risk / exposure 決定買法或 no-bet。

5. Correlated exposure
   - 目前有單注 / 單場 cap。
   - 未有同一匹馬、同一組腳位跨多張組合票的相關曝險減注。

### 模型特徵缺口

6. 馬匹標準化速度 / 能力評分
   - 需要按馬場、距離、班次、場地狀態、日期標準化。

7. 距離 / 場地 / 跑道適性加深
   - 目前只是部分特徵。
   - 需要增程 / 縮程、草地 / 全天候、濕地變化。

8. 檔位偏差表
   - 需要按馬場、距離、欄位、賽道、場地狀態、field size 建表。

9. 步速模擬
   - 目前只有基本 running_style。
   - 需要推斷領放馬、前置馬、後上馬、受阻風險、race shape simulation。

10. 即日賽道偏差
   - 已有 early engine，但需要更多 replay 驗證。
   - 每跑完一場要更新後面場次。

11. 騎師 / 練馬師近期狀態及意圖
   - 仍未有完整 rolling form、騎練配合、馬房部署、轉倉改善。

12. 配備變化特徵
   - 已儲存 gear，但未完整轉成首次戴、除去、重戴、組合配備訊號。

13. 晨操 / 試閘質素
   - 有 workout score，但試閘強度、姿態、催策、時間衰減仍弱。

14. 天氣 / 場地惡化特徵
   - 已有天氣顯示，仍未持久化成模型特徵。

15. 臨場亮相 / 視覺狀態
   - 未做。
   - 可先做人工結構化輸入，再考慮影像。

### 評估 / 自我進化

16. Promotion command
   - 目前有 registry 報告，但沒有自動 promote command。
   - 需要拒絕未通過 OOS / calibration / drawdown / CLV gate 的模型替換。

17. Calibration hard gate
   - 目前 calibration 是報告。
   - 未成為 stake sizing / promotion 的硬性 gate。

18. Error taxonomy deeper loop
   - 已有 persistent error reviews。
   - 仍需用錯誤分類直接決定下一輪特徵實驗及驗證目標。

19. 多目標優化
   - 尚未同時優化 ROI、命中率、Brier、log loss、最大回撤、volatility、分彩池 ROI。

20. 真實落注執行層
   - 未做自動下注。
   - 也未記錄真實 bet-time odds / confirmation。
   - 目前只做建議及 ledger 對數。

## 建議下一步

下一個 Codex 不應再先加 UI。建議集中做：

1. `pool takeout + pool efficiency cost table`
2. `final exotic dividend settlement`
3. `exotic betting ledger reconciliation`
4. `late market flow feature validation`
5. `calibration hard gate for staking / model promotion`

每一步都要有測試，並用 walk-forward / out-of-sample / replay 證明沒有破壞模型。
