# Codex 接手上下文

Last updated: 2026-05-09

## Latest Handoff - 2026-05-09

This is the current handoff note for the next Codex agent. Treat this section as the authoritative recent context even if older Chinese text below appears mojibake/garbled because of encoding.

### Current Branch And Deployment

- Repo: `https://github.com/harryheung19951212-sketch/HKJC_Statistical_Model`
- Branch: `codex/horse-racing-model`
- Latest handoff update: granular coverage maturity score after pool replay gate for pool choice. Previous pushed commit before this work: `cf822a5 Gate pool choice with replay calibration`.
- Production server: `43.228.125.192`
- Production app path: `/opt/hkjc-model`
- Public app URL: `http://43.228.125.192:8765/`
- Production app uses Docker Compose with PostgreSQL. Do not commit `.env`, `.env.production`, data DBs, raw data, model snapshots, reports, passwords, tokens, or server secrets.

### Current Development Focus - Pool Replay Gate For Pool Choice

- The latest local work strengthens indicator 24 彩池選擇模型 and indicator 25 組合派彩預測.
- Exotic dividends are now classified as official probable, final result, estimated, unverified, or missing.
- Only HKJC GraphQL/MQTT `probable` exotic dividends can pass the live betting gate. Final result dividends are replay/settlement only; manual/unverified prices are watch-only even when EV is positive.
- Pool-choice rows now expose official price coverage, price quality, and `need_official_dividend` verdicts so another Codex can continue with real pool replay and final-dividend calibration.
- UI candidate cards show dividend quality and the exact price-gate reason.
- Pool replay now includes `final_dividend_audit`, splitting pending confirmed exotic tickets into no-results, known-loss ready-to-settle, winning tickets waiting for final dividend, and winning tickets with final dividend ready but not yet reconciled.
- `/api/pool-replay/reconcile` now uses the audit to refresh HKJC results/final dividends for flagged races, then runs betting-ledger reconciliation and returns before/after audit summaries.
- Global update also reconciles the race ledger/pool replay immediately after a race becomes resulted.
- New in this turn: `pool_replay_calibration()` converts settled pool replay into per-pool statuses. Betting now accepts `pool_replay_gate`; a pool with poor settled ROI can be blocked, pools waiting for final dividend / reconciliation can be reduced, and pool-choice UI shows replay status, sample size, replay ROI, stake factor, and reason.
- The active race API caches this gate for 60 seconds so the 30-second race refresh does not recalculate full replay state every time.
- Coverage scoring now uses per-item `maturity_score` instead of only coarse statuses. The old status-only score is still exposed as `status_coverage_score`; item 24 and 25 now move the overall score after the recent pool-choice/final-dividend work without pretending they are fully complete.
- New in this turn: pool replay now builds contextual segments by track, distance bucket, class, track/distance, and track/distance/class. Active betting passes the current race row into `pool_replay_calibration()`. If a matching slice has enough settled samples, it can block/reduce a pool by slice ROI; if not, the system falls back to the global pool gate and shows that reason in pool-choice UI.
- New after that: pool replay now includes `pool_choice_optimizer`, a chronological walk-forward replay that only uses prior settled tickets to decide pass/reduce/block for each pool, then compares the gated result against the baseline all-ticket replay. Pool-choice UI shows optimizer status, policy, walk-forward ROI, ROI delta, and reason.
- Coverage now shows overall `coverage_score` around 52.8%. `status_coverage_score` is now a moving status progress score around 52.3%, while `coarse_status_score` preserves the old coarse baseline around 51.8%. Item 24 maturity is 72%; item 25 maturity is 62%.
- Important scoring convention: do not call the moving status score `舊狀態分` in UI. It should be `狀態進度分`; `粗分類基準` is the old coarse score and only moves when a main status changes.
- Next best step: extend the optimizer from per-pool global replay into per-context threshold tables and add statistical confidence/sample guards; do not raise coverage again unless tests/replay evidence exist.

### Recent Betting And Settlement Changes

- Payout reconciliation is now limited to rows whose `betting_recommendations.execution_status` is `confirmed`.
- Positive-stake model recommendations are not automatically treated as bought tickets anymore. They remain `suggested` unless they pass the execution model gate.
- The execution model gate checks model action, official live price source, required dividend, EV, edge, cost-adjusted EV, calibration, exposure, and pool-choice verdict.
- Manual confirmation also goes through the same gate. Stale or estimated prices must not be forced into settlement.
- Same logical ticket dedupe is enforced by race, market, horse/combination, risk profile, and model path. Refreshes reuse the existing row and must not reduce recorded stake.
- Open confirmed tickets are refreshed every 30 seconds from the latest official HKJC WIN/PLACE odds or official probable exotic dividends. Estimated exotic dividends are ignored for live betting.
- The UI wording was changed away from "estimated/current estimate" language toward latest pool price/live pool wording.
- Settlement replay, betting-slip state, pool-choice scoring, and exotic candidate handling should all use the latest official pool price, not a locked bet-time estimate.

### Pre-Post Training Fill Rule

- Normal operation: no edge means no bet. A good horse at a bad price is still no bet.
- In the final 5 minutes before estimated post time only, if a race has fewer than 5 confirmed settlement tickets, the ledger supplements up to 5 tickets for training.
- Supplemental fill requires at least 2 exotic-pool tickets when candidates are available.
- Fill selection is closest-to-gate, but win/top-3/hit probability is more important than odds. Do not select extreme high-odds low-hit candidates just because the payout is large.
- Training-fill tickets are marked with `execution_value_status='pre_post_training_fill'`.

### Production Data Maintenance Done

- On 2026-05-09, the user asked to clear all current payout reconciliation tickets.
- Production backup was created at `/opt/hkjc-model/data/backups/betting_recommendations_clear_20260509_070310.sql`.
- All production rows with `execution_status='confirmed'` were reset to `suggested`.
- Execution odds, stake, settlement result, return, profit/loss, CLV, slippage, and reconciliation fields were cleared for those rows.
- Cleared rows were marked with `execution_value_status='cleared_from_settlement'`.
- Verified production confirmed ticket count: `194 -> 0`.
- Verified production `/api/state` returned `ok` after deployment.

### Files Most Relevant To Recent Work

- `src/racing_model/betting_ledger.py`: betting recommendation persistence, execution gate, live price refresh, logical ticket dedupe, pre-post training fill.
- `src/racing_model/betting.py`: betting decision generation, staking, EV and pool decision inputs.
- `src/racing_model/fast_betting.py`: fast betting payload and refresh flow.
- `src/racing_model/web/app.js`: UI labels, settlement filters, betting display behavior.
- `src/racing_model/coverage.py`: 36-factor coverage/support status; update this when behavior changes.
- `tests/test_betting_ledger.py`, `tests/test_fast_betting_refresh.py`, `tests/test_betting.py`, `tests/test_betting_settlement.py`, `tests/test_ui_localization.py`, `tests/test_coverage.py`: focused regression coverage for the recent changes.

### Verification Recently Used

- `python -m pytest tests\test_betting_ledger.py tests\test_fast_betting_refresh.py tests\test_betting.py tests\test_betting_settlement.py tests\test_coverage.py -q`
- `python -m pytest -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- Production checks: SQL count of confirmed rows and `curl http://127.0.0.1:8765/api/state`.

### Deployment Pattern

After code, docs, data behavior, feature, fix, or cleanup work:

1. Commit locally on `codex/horse-racing-model`.
2. Push to GitHub.
3. Deploy to production by archiving local `HEAD`, uploading it to the server, extracting into `/opt/hkjc-model`, running `docker compose -f docker-compose.prod.yml build app`, then `docker compose -f docker-compose.prod.yml up -d`.
4. Verify the app with `/api/state` and any relevant SQL/API checks.

Do not place SSH passwords or tokens in repo files or logs.

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
- 每次開發都要同步更新「方程式覆蓋率 / 盲點報告」：若有模型、特徵、投注、校準、資料或 UI 行為改動，必須更新 `src/racing_model/coverage.py` 對應 36 項指標的 current support / limitations / next steps，並在 `docs/DEVELOPMENT_LOG.md` 寫清楚覆蓋率或盲點有何變化。
- 每次驗收都要至少檢查 `/api/coverage` 或 `tests/test_coverage.py`，確保使用者可以直接看到最新進度與剩餘盲點。

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
- 覆蓋 36 個主項：
  - 1-21：核心方程式主項。
  - 22-36：進階實戰、live、風控、執行及自我進化主項。
- 每項標示：
  - 已完成
  - 部分完成
  - 未完成
  - 需要外部數據
- 顯示：
  - 資料來源
  - 支援檔案
  - 細項
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
  - 派彩對數只 replay 已通過落飛模型 gate 的 `confirmed` tickets；未過 gate 的 positive-stake rows 只作 `suggested` 建議留痕。
  - 例外：開賽前最後 5 分鐘若 confirmed 不足 5 條，會按接近 gate 程度補足到 5 條訓練飛，且至少 2 條組合飛；排序以勝率/三甲率/命中概率優先，賠率只作次要因素。

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

## 2026-05-08 36 主項 Coverage 對齊

- 使用者已明確定義 36 個賽馬模型主項，這是之後開發的目標尺。
- `/api/coverage` 已由舊 32 項改為完整 36 主項：
  - 1-21：核心方程式主項。
  - 22-36：進階實戰、live、風控、執行及自我進化主項。
- 每項都有中文名稱、狀態、細項、資料來源、支援檔案、缺口、下一步、模型風險及驗證門檻。
- UI 的「方程式覆蓋率 / 盲點報告」會在展開項目時顯示細項。
- 現時仍未「完成 36 項」；多數項目是部分完成，血統、賽事節奏模擬及亮相圈/即時狀態仍是主要缺口。

## 2026-05-08 下注組合風險控制

- `build_betting_decisions()` 會在 Kelly、單注 cap、單場 cap 後再跑相關曝險控制。
- 新增三種曝險快照：
  - 同馬曝險：同一匹馬跨 WIN/PLACE/組合票的總 stake。
  - 彩池曝險：同一 pool 的總 stake。
  - 同腳位曝險：同一組馬跨 QIN/QPL/TRIO/TCE 等的總 stake。
- 超過 cap 時會按最緊的一項自動降注；低於最低投注單位時會轉為觀望 / 不加注。
- `/api/betting` payload 增加 `exposure_report`、`exposure_adjusted`，ticket/candidate 會有 `exposure_action`、`exposure_reason`、`exposure_adjustment_factor`。
- UI「即場投注建議」會顯示「下注組合風險控制」開合區塊及每張飛的曝險狀態。
- 仍未完成：要用 bankroll replay 證明 exposure graph 降低最大回撤且不破壞 ROI。

## 2026-05-08 下注執行層 / Bet-time Confirmation

- `betting_recommendations` 新增下注確認欄位：
  - `execution_status`
  - `executed_at`
  - `execution_odds`
  - `execution_stake`
  - `execution_source`
  - `execution_slippage`
  - `execution_clv`
- 新增 `confirm_betting_recommendation()`，可按 recommendation id 確認下注時賠率及實際注碼。
- 新增 `POST /api/betting-ledger/confirm`。
- UI ticket card 會顯示下注確認狀態；可按「確認下注」把當刻最新 odds/dividend 記錄到 ledger。
- `record_betting_payload()` 會保留已確認及已對數欄位，避免 30 秒刷新重新寫入建議時覆蓋下注確認。
- 仍未完成：未有自動下注、下注單號、取消/部分成交、以及 slippage-adjusted ROI 報告。

## 2026-05-08 Execution Replay / Slippage-adjusted ROI

- `pool_replay_report()` 已加入下注時 execution replay：
  - `executed`
  - `execution_staked`
  - `execution_returned`
  - `execution_profit`
  - `execution_roi`
  - `avg_execution_clv`
  - `avg_execution_slippage`
  - `execution_max_drawdown`
- execution replay 只計 `execution_status = confirmed` 且已對數的票。
- 計算時用 `execution_stake`、`final_odds`、`outcome_win` 重算真實返還及盈虧，而不是沿用建議注碼。
- UI「回測 / 智能迭代中心」的 pool replay 會同時顯示原本 ROI 及下注時 ROI。
- Model registry promotion gate 已正式加入 execution hard blocker：
  - 原本 walk-forward gate 先判斷是否可成為升級候選。
  - 如果原本可以升級，但已確認下注樣本少於 20，會變成 `execution_unverified`。
  - 如果原本可以升級，但下注時 ROI 為負，會變成 `execution_blocked`。
  - 只有 walk-forward gate 及 execution gate 都過關，才會保留 `upgrade_candidate`。
- `model_registry_runs` 新增 `execution_confirmed`、`execution_roi`、`execution_max_drawdown`、`execution_gate`。
- 仍未完成：未有真正 `promote` 指令自動替換 model JSON；現時只是 gate/report。

## 仍未完成 / 未做事項

### 最高優先

1. 真實賽日驗證 live odds / probable dividend feed
   - MQTT / GraphQL provider 已有骨架，但需要真實賽日跑通。
   - 要記錄最後 5 分鐘、2 分鐘、30 秒 odds movement。
   - 要分辨 smart money vs public chase。

2. Final exotic dividend settlement
   - 即場投注只可用官方 probable dividends；estimated dividends 不可進入即場入飛價。
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
   - 已加入 `model-registry --promote`、`/api/model-registry/promote` 及 UI 按鈕。
   - 只有最新 registry run 是 `upgrade_candidate` 才會重訓候選版本並替換 runtime 模型檔；原模型會先備份。
   - 仍未保存溫度校準候選的執行策略，所以該類候選會被拒絕。

17. Calibration hard gate
   - 已加入 `src/racing_model/calibration_gate.py`。
   - 投注建議會按校準 gate 的 `stake_factor` 降注；樣本不足減半，明顯失準降至四分之一。
   - `promote_latest_model` 會在正式寫入模型檔前檢查候選模型校準 gate，未過關就拒絕替換。
   - 仍未做到候選模型的 out-of-sample reliability artifact，也未按彩池/場地/距離分片做校準 gate。

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

1. `candidate OOS calibration artifact + sliced calibration gate`
2. `pool takeout + pool efficiency cost table`
3. `final exotic dividend settlement`
4. `exotic betting ledger reconciliation`
5. `late market flow feature validation`

每一步都要有測試，並用 walk-forward / out-of-sample / replay 證明沒有破壞模型。
