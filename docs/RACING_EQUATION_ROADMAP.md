# Racing Equation Roadmap

## Objective

Build a Hong Kong horse-racing statistical equation that improves through evidence. The target is not a dashboard. The target is a model that can identify high-quality betting opportunities, choose the best HKJC pool, control risk, and improve through post-race learning.

## 36 Racing Equation Factor Groups

### Core 21 Factor Groups

1. 賽事基本條件: 馬場、跑道、場地、距離、級別、獎金、參賽馬數、賽事時間。
2. 馬匹本身能力: 官方評分、速度、耐力、爆發、穩定、體重、健康、性格、跑法、起步、轉彎。
3. 近期狀態: 近績、狀態走勢、休息日數、試閘、晨操、上仗解讀、賽後恢復。
4. 騎師因素: 勝率、位置率、人馬配合、馬場適性、當日狀態、決策能力、動機。
5. 練馬師因素: 整體成績、近期馬房狀態、部署模式、騎練組合、新馬及轉倉馬能力。
6. 負磅與讓磅: 實際負磅、讓磅、體重比例、距離互動、班次互動。
7. 排位與檔位: 檔位、馬場檔位偏差、跑法配合、鄰近馬影響、歷史檔位統計。
8. 步速與賽事形態: 早段步速、領放壓力、跑法分布、節奏、位置預測、受阻風險。
9. 血統因素: 父系、母系、家族賽績、新馬與轉程判斷。
10. 配備與裝備: 眼罩、面箍、舌帶、鼻箍、耳塞、蹄鐵、配備變動信號。
11. 班次與評分變化: 升班、降班、評分升跌、隱藏能力。
12. 賠率與市場資訊: 獨贏、位置、異動、成交量、價值、水位抽水、熱門偏差。
13. 投注類型: WIN、PLACE、QIN、QPL、FCT、TRIO、TCE、FIRST4、QUARTET、多場投注。
14. 資料來源: 官方賽績、分段、影片、獸醫、試閘、晨操、天氣、場地、賠率、新聞。
15. 資料清洗與特徵工程: 時間、馬場、距離、班次、負磅、馬位、異常、缺失、衰減、條件匹配。
16. 模型目標設計: 勝率、上名率、排名分布、完成時間、馬匹評分、價值預測。
17. 常見模型方法: 規則、線性、樹模型、排名、貝葉斯、Elo/TrueSkill、模擬、影像、混合。
18. 評估指標: 命中率、log loss、Brier、校準、ROI、EV、CLV、回撤、Sharpe-like、分片表現。
19. 資金管理: 固定注碼、比例下注、Kelly、上限、篩選、分散、紀律。
20. 實戰風險: 樣本偏差、過擬合、市場效率、資料延遲、不可觀測因素、漂移、合規。
21. 最重要核心特徵清單: 近期速度、同場同程、距離/場地、班次、評分、負磅、檔位、步速、騎練、休息、體重、受阻、配備、試閘晨操、即時賠率。

### Advanced Live Betting / Risk Factor Groups

22. 市場效率 / 莊家抽水層: edge 必須覆蓋不同彩池成本。
23. 臨場資金流模型: 最後 5 分鐘、2 分鐘、30 秒賠率變化與 CLV。
24. 彩池選擇模型: 同一觀點選擇最佳彩池或不下注。
25. 組合派彩預測: probable/final dividend 或估算組合彩池派彩。
26. 馬匹狀態即時層: 亮相圈、出汗、步姿、入閘前狀態。
27. 賽事節奏模擬: 放頭、跟前、被困、末段追勢與交通風險。
28. 賽道偏差即日更新: 每場完結後更新內外檔、前置/後上偏差。
29. 騎師 / 練馬師意圖: 熱身、試程、試配備、搏殺、騎師安排與晨操密度。
30. 模型校準: 概率可靠度與 Kelly 安全。
31. 避免過度擬合: walk-forward、out-of-sample、分場地/距離/班次驗證。
32. 下注組合風險控制: horse/pool/combination 相關曝險及每場總風險。
33. 錯誤分析資料庫: 把每次錯誤分類到步速、狀態、賠率、場地、騎師、市場或資料。
34. 下注執行層: 建議時賠率、下注時賠率、最後賠率/派彩。
35. 多目標優化: Top1、Top3、ROI、最大回撤、穩定度、分彩池 ROI。
36. 不下注決策: 無 edge、資料差或曝險過度集中時明確跳過。

## Development Phases

### Phase 1: Coverage and Blind-Spot Report

- Add a system-level coverage score for all 36 factor groups.
- Mark each group as implemented, partial, missing, or blocked by data access.
- Show which missing factors are most likely to affect ROI.

### Phase 2: Equation Core

- Build a unified race equation output per horse:
  - Win probability.
  - Top-3 probability.
  - Rank confidence.
  - Fair odds.
  - Feature explanations.
  - Calibration status.
- Add promotion gates based on Brier score, log loss, top-pick hit rate, top-3 hit rate, and ROI.

### Phase 3: All HKJC Pool Decision Layer

- Support WIN, PLA, QIN, QPL, FCT, TRIO, TCE, FIRST4, and QUARTET.
- For each pool calculate:
  - Hit probability.
  - Break-even dividend.
  - Official/probable dividend when available.
  - EV.
  - Suggested stake.
  - Exposure relationship to other tickets.

### Phase 4: Live Sniper Layer

- Use live odds at 30-second intervals now; upgrade to real MQTT live handling on a race day.
- Record last live odds for completed/in-running races.
- Add final 5/2/0.5 minute money-flow features.
- After each race, update same-day track bias and next-race assumptions.

### Phase 5: Self-Improving Loop

- After each race:
  - Pull result.
  - Freeze final odds.
  - Backtest all recommended decisions.
  - Classify error causes.
  - Generate Codex CLI improvement proposal.
- Promote changes only if they pass walk-forward gates.

### Phase 6: Risk and Bankroll

- Fractional Kelly.
- Daily, race, pool, and horse exposure caps.
- Correlation-aware stake reduction.
- Mandatory no-bet output when there is no edge.

### Phase 7: Advanced Signals

- Same-day track-bias learning.
- Trainer/jockey intent model.
- Paddock and visual-condition input.
- Exotic probable-dividend model.
- Final output: "betting opinion -> best pool -> risk-adjusted stake -> post-race learning".

## Current Implementation Status

Phase 1 is implemented:

- `/api/coverage` exists.
- It now tracks the full 36 factor groups requested for the racing equation.
- Groups 1-21 are core equation factors; groups 22-36 are advanced live-betting, risk, settlement, and self-improvement factors.
- Each item reports status, data sources, supported files, current support, gaps, next steps, model risk, and validation gate.
- Each item includes expandable sub-items so the UI can show the detailed coverage target, not just the headline.
- The UI has a top-level "方程式覆蓋率 / 盲點報告" page.
- Coverage report content is localized to Chinese as much as practical.
- Coverage items are collapsible so users click a main item to see detailed gaps.

The web app now has top-level pages:

- 賽事分析
- 方程式覆蓋率 / 盲點報告
- 回測 / 智能迭代中心

Background live refresh is active-race-only to reduce server load. The manual "全域更新" button remains global and processes the next scheduled race once.

## Current Next Best Step

Do not add more generic UI first. The next work should move the equation closer to real betting EV:

1. Add correlated exposure control for horse/pool/combination tickets.
2. Add final exotic dividend settlement and full exotic ledger reconciliation.
3. Validate live late-market-flow features from real race-day ticks.
4. Turn calibration and promotion gates into hard blockers for staking/model replacement.
5. Build the 36-factor scorecard into every model iteration report.

Every change must include tests and must be promoted only with walk-forward / out-of-sample evidence.

## 2026-05-07 Update

Roadmap item 22 is now partially implemented:

- `src/racing_model/pool_rules.py` defines HKJC pool payout/takeout assumptions, minimum units, and market-efficiency buffers.
- Betting decisions now expose cost-adjusted EV, required EV, required edge, required dividend, and pool rule metadata.
- WIN/PLACE and exotic recommendations must clear pool-cost gates before stake allocation.

Current next best step:

1. Add pool-specific replay reports that compare raw EV, cost-adjusted EV, hit rate, ROI, and drawdown.
2. Validate pool payout/takeout assumptions against official pages and real settlement data on each race day.
3. Add final exotic dividend settlement coverage for every supported pool.
4. Validate live late-market-flow features from real race-day ticks.
5. Turn calibration and promotion gates into hard blockers for staking/model replacement.

## 2026-05-09 Update

Roadmap item 35 is now further implemented:

- Walk-forward model comparison now assigns every candidate a multi-objective OOS score.
- The score uses Log Loss, Brier, Top1 hit rate, Top3 hit rate, value ROI, and maximum drawdown.
- Candidate ordering, model registry promotion gate, experiment manifest, promotion scorecard, and UI now expose/use the multi-objective score.
- The promotion gate can now block a candidate with `multi_objective_blocked` when total score is not enough or ROI / Top3 / risk deteriorates.
- Coverage item 35 has been updated so the 方程式覆蓋率 / 盲點報告 score moves with this real model-layer progress.

Remaining work for item 35:

1. Feed the multi-objective score into candidate feature-search automation.
2. Add pool-level ROI / volatility as weighted objectives.
3. Let stake strategy tuning optimize the same objective set.
4. Keep walk-forward / OOS gates as hard blockers before model promotion.

