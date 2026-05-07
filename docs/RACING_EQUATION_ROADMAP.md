# Racing Equation Roadmap

## Objective

Build a Hong Kong horse-racing statistical equation that improves through evidence. The target is not a dashboard. The target is a model that can identify high-quality betting opportunities, choose the best HKJC pool, control risk, and improve through post-race learning.

## The Original 21 Core Factor Groups

1. Horse baseline ability: rating, weight, age, sex, class history, raw performance.
2. Recent condition: recent form, layoffs, improvement/regression, recency pattern.
3. Distance suitability: same-distance record, stretch-out, cut-back, sprint/mile/staying fit.
4. Going and surface suitability: turf, all-weather, good, yielding, soft, wet.
5. Track and course: Sha Tin, Happy Valley, A/B/C courses, all-weather layouts.
6. Draw effect: inside/outside draw by distance, track, field size, and rail position.
7. Weight and claim: carried weight, apprentice claim, weight changes.
8. Jockey factor: long-term win rate, recent form, trainer pairing, horse familiarity.
9. Trainer factor: win rate, recent stable form, placement pattern, change of stable.
10. Owner/stable intent: entries, jockey booking, class placement, repeated runs, target signals.
11. Trackwork and trials: morning work, barrier trials, recency, rank, visual quality when available.
12. Gear changes: first time, removed, reapplied, combined gear effect.
13. Pace shape: leader/pace/stalker/closer map, pace pressure, likely race flow.
14. Race strength: class depth, opponent quality, pace mix, field quality.
15. Pre-race odds: win/place market probabilities, favorite pressure, public sentiment.
16. Late odds movement: final 5 min, 2 min, 30 sec, smart-money and public-money patterns.
17. Results and sectionals: finish time, margins, sectionals, running position, turn/straight splits.
18. Same-day track bias: rail, inside/outside, front-runner/closer bias, race-by-race update.
19. Weather and environment: temperature, humidity, rain, wind, track deterioration.
20. Historical backtesting: track/distance/class/pool ROI and hit-rate slices.
21. Model explainability and calibration: reliability curves, feature contribution, error diagnostics.

## Extra Blind Spots Beyond 21

22. Pool takeout and market efficiency: edge must exceed pool cost.
23. Pool selection: same racing opinion may belong in WIN, PLA, QIN, QPL, TRIO, TCE, FIRST4, or QUARTET.
24. Exotic dividend prediction: probable dividend or final dividend estimate for combination pools.
25. Correlated exposure: avoid over-staking the same banker or same legs across multiple pools.
26. No-bet discipline: skipping weak races is part of the equation.
27. Probability calibration first: a 30% prediction should win around 30% long-term.
28. Walk-forward promotion gates: no model upgrade without out-of-sample proof.
29. Error taxonomy: classify losses into pace, bias, condition, market, trip, data, or model errors.
30. Live paddock/visual condition: sweat, gait, temperament, parade behavior, gate behavior.
31. Execution slippage: suggested odds, bet-time odds, and final odds must be recorded separately.
32. Multi-objective optimization: hit rate, ROI, drawdown, volatility, calibration, and pool-specific performance.

## Development Phases

### Phase 1: Coverage and Blind-Spot Report

- Add a system-level coverage score for all 32 factor groups.
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
- It covers 21 core factor groups plus 11 blind spots.
- Each item reports status, data sources, supported files, current support, gaps, next steps, model risk, and validation gate.
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

1. Add pool takeout and market-efficiency cost tables.
2. Add final exotic dividend settlement.
3. Extend betting ledger reconciliation to exotic pools.
4. Validate live late-market-flow features from real race-day ticks.
5. Turn calibration and promotion gates into hard blockers for staking/model replacement.

Every change must include tests and must be promoted only with walk-forward / out-of-sample evidence.

