# Codex Handoff Context

Last updated: 2026-05-07

## Project Intent

This project is not a horse-racing information portal. The goal is to build a Hong Kong racing statistical model and betting-equation engine that can:

- Predict win probability and top-3 probability with calibration.
- Compare model probabilities against HKJC market prices.
- Select the best betting pool, including exotic pools, only when there is measurable edge.
- Backtest every decision and improve only when walk-forward evidence supports the change.
- Preserve final live odds and race results as training material.
- Avoid random model iteration; every iteration must target better accuracy, ROI, calibration, or drawdown control.

Important expectation: the model should aim for long-term positive expected value, high hit rate where appropriate, and high ROI, but it must not assume guaranteed profit. HKJC pools are pari-mutuel and final dividends can move.

## Current State

The local web app runs at:

```powershell
python -m racing_model.cli serve --port 8765 --model-path models/baseline.json --odds-interval 30
```

Open:

```text
http://127.0.0.1:8765/
```

Current `.env` on the original machine uses:

```text
RACING_CODEX_CLI=codex
RACING_CODEX_MODEL=gpt-5.5
RACING_CODEX_REASONING_EFFORT=high
RACING_ODDS_PROVIDER=mqtt
```

Do not commit `.env`, local SQLite databases, raw snapshots, generated reports, or trained model JSON files. They are intentionally ignored.

## Implemented Features

- SQLite schema for races, runners, results, workouts, odds ticks, raw snapshots, and race status.
- HKJC racecard/result parsing with Traditional Chinese horse, jockey, and trainer names.
- Result parser stores final win odds and place dividends as `hkjc_results_final`.
- Official win/place odds support through provider abstraction:
  - HKJC GraphQL provider.
  - HKJC MQTT recovery/feed provider.
  - Development snapshot fallback.
- Lifecycle controller:
  - Scheduled races can refresh odds.
  - Live races freeze final live odds.
  - Resulted races preserve final odds and results for training.
  - Manual controls prevent downgrading a resulted race back to live/scheduled.
- Chinese local web app:
  - Race list by meeting/race.
  - Chinese race metadata and weather.
  - Predictions, runner detail, odds chart/history, results comparison.
  - Backtest and AI iteration center.
  - Lifecycle panel and manual one-step controller.
- Model:
  - Pairwise ranking model.
  - Independent top-3 model.
  - Win probability and top-3 probability.
  - Feature contribution explanation.
- Backtesting and model evaluation:
  - Positive EV fixed-stake backtest for win/place.
  - Place backtest avoids leaking final dividend results.
  - Walk-forward model version comparison.
  - Calibration and diagnostic summaries.
  - Persistent model registry stores out-of-sample run summaries and promotion-gate labels.
- Betting recommendation ledger:
  - Active WIN/PLACE recommendations are stored with suggested odds, probability, EV, edge and stake.
  - Reconciliation compares recommendations with final odds/results to calculate P/L, slippage and CLV reference.
- Codex CLI integration:
  - Uses local `codex` CLI instead of OpenAI API key.
  - Intended model: GPT-5.5.
  - Intended reasoning effort: high.
- Betting decision layer:
  - Fair odds, market probability, edge, expected value.
  - Fractional Kelly with conservative/standard/aggressive profiles.
  - Per-bet and per-race risk caps.
  - No-bet decisions for live/resulted races or insufficient edge.
- Exotic pool candidate layer:
  - Quinella, quinella place, exacta, trio, tierce, first four, quartet.
  - Displays model hit probability and break-even dividend.
  - Compares "position Q base" against "trio upgrade" opportunities.
  - Does not invent official exotic dividends yet.

## Current Loaded Example

The original machine has HKJC 2026-05-06 Sha Tin data loaded locally, including 9 resulted races. This data exists in ignored local DB/raw files and should be regenerated on another machine:

```powershell
racing-model init-db
racing-model backfill-hkjc --start 2026/05/06 --end 2026/05/06 --venue ST --races 10
racing-model train --model-path models/baseline.json
```

For a lightweight smoke check:

```powershell
racing-model import-sample
racing-model train --model-path models/baseline.json
python -m racing_model.cli serve --port 8765 --model-path models/baseline.json --odds-interval 30
```

## Test Commands

Run these before handing changes back:

```powershell
python -m compileall -q src dashboard tests
python tests\test_smoke.py
python tests\test_hkjc_parser.py
python tests\test_betting.py
node --check src\racing_model\web\app.js
```

## Roadmap Priority

The next Codex should avoid generic UI expansion. Prioritize the equation engine:

1. Build a feature coverage/blind-spot report for the 21 core factor groups plus extra blind spots.
2. Make every model iteration pass walk-forward gates before promotion.
3. Add official or reliable estimated exotic-pool dividends, then calculate real EV for each pool.
4. Add market-flow features from final 5 minutes, 2 minutes, and 30 seconds.
5. Add same-day track bias after each race and update later-race predictions.
6. Add error taxonomy database so every losing decision has a reason category.
7. Add exposure control across correlated bets, especially shared banker/legs across QPL/TRIO/TCE/FIRST4/QUARTET.

Current high-priority gap after the betting ledger: extend the ledger from WIN/PLACE to exotic pools once official/probable dividends are available, then make CLV/slippage part of the hard promotion gate after enough live recommendations are reconciled.

## Important Design Principle

Do not optimize for "more predictions". Optimize for fewer, stronger, verified betting opinions:

- No edge: no bet.
- Good horse but bad price: no bet.
- Good view but weak payout in one pool: test upgrade to another pool.
- Big expected value but unstable calibration: reduce stake.
- Every final decision must be auditable after the race.

