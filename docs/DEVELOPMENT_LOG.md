# Development Log

This file records cross-device Codex handoffs, audits, fixes, pushes, and server deployments.

## 2026-05-08 - Weather Forecast Fallback Before Race Day

Goal:

- Avoid the weather panel failing with raw JSON parse errors when HKO historical daily weather is unavailable for future race dates.
- Show forecast weather before race day, then switch back to current weather on the race day.

Changes:

- Added HKO nine-day forecast support via `dataType=fnd`.
- Future race dates now use forecast weather instead of historical daily observations.
- Same-day weather still tries the current observation feed first, then falls back to the same-day forecast if the current feed returns an empty/non-JSON response.
- Wrapped non-JSON HKO responses with a clearer error message, so the UI no longer shows `Expecting value: line 1 column 1`.

Verification:

- `python -m compileall -q src tests`
- `python -m pytest tests\test_weather.py -q`
- Live forecast check for `2026-05-09` Sha Tin via HKO `fnd`
- `python -m pytest -q`

## 2026-05-08 - Align Betting Minimums And Coverage Header

Goal:

- Make every betting market use a HK$10 minimum ticket amount with no hard maximum ticket size.
- Fix the coverage page header from the old 32-item wording to the current 36-item roadmap.

Changes:

- Updated TRIO, TCE, FIRST4, and QUARTET pool rules from HK$1 to HK$10 minimum units, matching WIN, PLACE, QIN, QPL, and FCT.
- Updated the coverage view heading to `36 項覆蓋狀態`.
- Added/updated tests so every pool rule has `min_unit == 10.0` and the UI no longer contains the old 32-item heading.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_betting.py tests\test_ui_localization.py tests\test_coverage.py -q`
- `python -m pytest -q`

## 2026-05-08 - Betting Tab Refreshes All Live Odds

Goal:

- Ensure every betting market shown in the betting tab uses its matching live odds/dividend and refreshes every 30 seconds.

Changes:

- Betting full refresh now calls `/api/betting` with `refresh_odds=1` as well as `refresh_exotics=1`.
- `/api/betting` refreshes WIN/PLACE odds before recalculating decisions and bypasses the short-lived prediction cache when live odds are requested.
- WIN/PLACE odds refresh and exotic dividend refresh now continue for both `scheduled` and `live` races, freezing only after a race is resulted.
- Added regression coverage for live race odds refresh, full betting cache bypass, live exotic dividend refresh, and the frontend refresh query.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_fast_betting_refresh.py tests\test_exotic_live.py tests\test_ui_localization.py -q`
- `python -m pytest -q`
- Browser check on `http://127.0.0.1:8766/`: betting panel rendered after the frontend refresh change.

## 2026-05-08 - HKJC Horse Profile Last Six Runs

Goal:

- Use HKJC horse profile pages, for example `/zh-hk/local/information/horse?HorseNo=J488`, as the source for each runner's recent six-run form.

Changes:

- Added HKJC horse profile URL/fetch support and a parser for the `馬匹近三季往績紀錄` table.
- Race-day loading now enriches each runner's `last_six_runs` from its HorseNo/Brand No profile, falling back to the racecard value only when the profile cannot be fetched or parsed.
- Added parser and race-day enrichment regression tests based on the J488 profile structure; J488 now parses as `WV-A/12/4/1/4/8`.

Verification:

- `python -m compileall -q src tests`
- `python -m pytest tests\test_hkjc_parser.py tests\test_race_status.py -q`
- Live parse check against `https://racing.hkjc.com/zh-hk/local/information/horse?HorseNo=J488`
- `python -m pytest -q`

## 2026-05-08 - Preserve Race Page State And Refresh Exotic Odds

Goal:

- Keep the user on the same page/tab after browser refresh or 30-second updates.
- Stop collapsed race-menu folders reopening during auto refresh.
- Keep exotic betting candidates tied to fresh official dividend refreshes.
- Show HKJC last-six-runs form under each horse name in the prediction table.

Changes:

- Added browser-side state persistence for selected race, top-level view, race tab, selected horse, and race-menu folder open/closed state.
- Removed automatic jumping from a resulted race to the next scheduled race during refresh/result checks.
- Renamed the top-left manual button to `刷新賠率`.
- Full betting refresh now asks `/api/betting` to synchronously refresh exotic dividends before rebuilding candidate tickets.
- Added `last_six_runs` to runner storage, HKJC racecard parsing, model prediction payloads, and the race-page prediction/detail display.
- Changed ordered exotic candidate structure to display boxed/複式 ticket counts, so TCE `1+2+3` shows six ordered tickets and does not imply a banker dragging every other horse.
- Forced race-page tabs into a four-column desktop row, with a compact two-column mobile layout.

Verification:

- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `python -m pytest tests\test_hkjc_parser.py tests\test_betting.py tests\test_ui_localization.py tests\test_fast_betting_refresh.py -q`
- `python -m pytest -q`
- Browser check on `http://127.0.0.1:8766/`: reload preserved the betting tab and collapsed race folder state.

## 2026-05-08 - Fix Zero-Padded HKJC Odds Runner Mapping

Goal:

- Fix production live odds refresh where HKJC official WIN/PLACE odds were only saved for horse 10 because GraphQL returned runner numbers as `01`-`09` while local runners used `1`-`9`.

Changes:

- Added runner-number normalization for HKJC GraphQL odds and MQTT odds payloads.
- Extracted GraphQL WIN/PLACE odds payload parsing into a testable helper.
- Added regression tests proving zero-padded official runner numbers map to all local runners.

Verification:

- `python -m pytest tests\test_odds_provider.py tests\test_feed_health.py tests\test_market_flow.py`
- `python -m pytest`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - Bet Slip Execution Optimizer

Goal:

- Turn value tickets into a practical race-level bet slip, so the system recommends a coherent set of bets instead of isolated good-looking tickets.

Changes:

- Added a `bet_slip` payload to `/api/betting` with race-level stake usage, expected profit, expected ROI, estimated at-least-one-hit probability, strategy options, ticket ranking, and execution notes.
- Every active ticket now receives slip rank, priority score, strategy, risk tier, portfolio role, hit probability, and expected profit.
- Betting ledger schema and recording now persist the bet-slip metadata for future replay and AI iteration.
- Added UI section `下注單引擎` above individual tickets, showing conservative/standard/aggressive plans and the ordered bet slip.
- Added tests proving bet-slip ranking/strategy output and ledger persistence.

Verification:

- `python -m pytest tests\test_betting.py tests\test_betting_ledger.py`
- `python -m pytest`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - Execution Value Ledger Gate

Goal:

- Make the model judge whether a recommendation was still worth betting at the actual execution odds, not only at the price seen when the ticket was generated.

Changes:

- Added cost-adjusted EV, required dividend, pool-choice rank/score/verdict, and execution value fields to the betting ledger schema.
- Betting ledger now records pool-choice context on each ticket and flags confirmed bets as `valid_execution`, `stale_price`, or `negative_ev_at_execution`.
- Ledger UI now shows required odds, actual execution odds, pool-choice score/rank, suggested EV, execution EV, and execution status.
- Pool replay and model registry now aggregate valid execution, stale-price, negative-execution, execution EV, and execution edge.
- Model promotion gate now blocks upgrades when too many confirmed bets were placed after the value had already disappeared, even if settled ROI happens to look positive.
- Added regression tests for stale execution-price blocking and ledger recording of pool-choice/execution value fields.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_model_registry.py`
- `python -m pytest`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - Pool Choice Efficiency Scorecard

Goal:

- Compare HKJC betting pools by cost-adjusted value instead of treating each ticket in isolation, especially when a low-return QPL idea may be better expressed through TRIO/TCE with smaller stake and higher payout leverage.

Changes:

- Added a `pool_choice` scorecard to betting output.
- Scores WIN, PLACE, QIN, QPL, FCT, TRIO, TCE, FIRST4, and QUARTET using probability, official/probable dividend, required dividend, takeout, ticket cost, recommended stake, and variance penalty.
- Added pool-choice recommendations that flag when QPL value should be tested against TRIO/TCE, or when the higher-variance pool should be rejected despite attractive payout.
- Added UI section `彩池選擇模型` above exotic candidates, showing the preferred pool, actionable pool count, positive cost-adjusted EV pool count, and top pool cards.
- Added tests for pool-choice payload presence and QPL-to-TRIO upgrade comparison.

Verification:

- `python -m pytest tests\test_betting.py tests\test_ui_localization.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - OOS Slice Scorecard Promotion Gate

Goal:

- Stop model iteration from promoting a candidate just because the overall out-of-sample score improved while an important race segment got worse.

Changes:

- Added a `walk_forward_oos_slice_scorecard` candidate artifact to every walk-forward/model-registry report.
- Added per-variant OOS calibration bins and slice scorecards across track, course, distance bucket, class, field size, and market-favourite bucket.
- Added a slice OOS hard gate that compares the candidate against baseline and blocks promotion when important slices regress in log loss, Brier score, or top-pick hit rate.
- Added `slice_blocked` and `slice_unverified` model-registry promotion states.
- Updated the model-registry UI to show the latest slice OOS gate, blocked slice count, and the worst blocking slices.
- Added regression tests proving the slice gate blocks an otherwise better-looking candidate when a key segment deteriorates.

Verification:

- `python -m pytest tests\test_model_registry.py tests\test_smoke.py tests\test_ui_localization.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - Sync Remote Latest And Fix Ledger Slippage Precision

Goal:

- Pull the latest `codex/horse-racing-model` work from the other development machine, audit the new model-gating and market-flow changes, and make the local workspace ready to continue.

Changes:

- Fast-forwarded local branch to remote commit `4e877e9 Add calibration hard gate`.
- Installed local `pytest` runner for proper cross-device verification.
- Fixed betting ledger slippage calculations so execution/final odds deltas are rounded consistently instead of storing floating-point artifacts such as `-0.3999999999999999`.
- Fixed deterministic active-race clock handling so focused scheduled races remain refreshable in tests while production still uses monotonic TTL expiry.
- Fixed short Chinese HKJC racecard token parsing so Chinese horse, jockey, and trainer names merge correctly.
- Restored the sample smoke dataset to two coherent races by adding a second race result block and removing an unused unresulted sample race.

Verification:

- `python -m pytest`
- `python -m pytest tests\test_betting.py tests\test_market_flow.py tests\test_model_registry.py tests\test_pool_replay.py tests\test_betting_ledger.py tests\test_betting_settlement.py tests\test_coverage.py tests\test_ui_localization.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`

## 2026-05-08 - Clarify Odds Tick And Exotic Dividend Counts

Goal:

- Make the race-header count clear so it is not mistaken for total betting odds or projected payout.

Changes:

- Renamed the top-right `賠率/派彩` metric to `賠率tick / 組合派彩`.
- Renamed the manual refresh button to `刷新賠率tick / 組合派彩`.
- Updated the related error status wording.
- Widened the race-header metric boxes so the clearer label does not feel cramped.

Verification:

- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `python tests\test_smoke.py`

## 2026-05-08 - Market Flow Per-Runner Signal Status

Goal:

- Remove the repeated `tick足夠，未見大異動` wording from every market-flow runner card and make each card analytically useful.

Changes:

- Added per-runner `tick_status` and `signal_status`.
- `data_status` now combines sample and signal, such as `3 ticks｜強落飛訊號`, instead of only saying the tick stream is monitorable.
- UI now shows `樣本`, `訊號強度`, and `訊號狀態`.
- Signal status differentiates:
  - strong / light `落飛`;
  - strong / light `轉冷`;
  - narrow movement without a signal;
  - insufficient data.

Verification:

- `python tests\test_market_flow.py`
- `python tests\test_late_market_flow.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `Get-ChildItem tests -Filter 'test_*.py' | ForEach-Object { python $_.FullName }`

## 2026-05-08 - Stable Race Header Refresh

Goal:

- Stop the race page header from collapsing during the 30-second refresh cycle.

Changes:

- `renderRaceHeader()` now supports preserve mode and no longer clears the top `賠率錄影` line when the refreshed race-list payload has no notes.
- `renderWeather()` preserves the previous weather/error line while deferred weather data is loading.
- `renderPredictionPolicy()` preserves the previous policy line when a refresh payload does not yet include policy data.
- Added fixed minimum heights to the race header and its metadata/status lines to reduce layout shift.

Verification:

- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `python tests\test_smoke.py`
- `python tests\test_market_flow.py`
- `Get-ChildItem tests -Filter 'test_*.py' | ForEach-Object { python $_.FullName }`

## 2026-05-08 - Market Flow Signal Labels

Goal:

- Make `臨場資金流 / 賠率異動` readable by separating market signal direction from data quality.

Changes:

- Added per-runner market-flow fields: `signal_label`, `signal_description`, `signal_action`, and `data_status`.
- UI now shows a dedicated `訊號` block:
  - `落飛｜市場追捧` for green cards;
  - `轉冷｜市場降溫` for red cards;
  - `平穩｜未有明顯異動` for neutral cards.
- Replaced the ambiguous `質素：可監控` display with `資料狀態`, such as `tick足夠，有明顯異動` or `tick足夠，未見大異動`.

Verification:

- `python tests\test_market_flow.py`
- `python tests\test_late_market_flow.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `Get-ChildItem tests -Filter 'test_*.py' | ForEach-Object { python $_.FullName }`

## 2026-05-08 - Candidate-Level Exotic Ticket Structure

Goal:

- Fix the combination betting UX so structure advice lives inside each `組合投注候選`, not in a separate `膽 / 腳建議` panel.
- Prevent cost-heavy broad cover suggestions such as `1 膽拖 7 腳`.

Changes:

- Removed the standalone `banker_leg_suggestions` API payload and UI section.
- Each exotic candidate now carries `structure_label`, `structure`, `bankers`, `legs`, `combination_count`, `minimum_ticket_cost`, `recommended_stake`, and `per_combination_stake`.
- Candidate structure is conservative:
  - no official/probable dividend or no cost-adjusted edge => `不做膽腳` and `$0`;
  - clear single-banker edge inside a 3- or 4-runner candidate => `膽拖腳`, capped to that candidate only;
  - otherwise => `複式` without adding extra legs.
- UI now shows the candidate structure and stake in each `組合投注候選` card.

Verification:

- `python tests\test_betting.py`
- `python tests\test_betting_ledger.py`
- `python tests\test_betting_settlement.py`
- `node --check src\racing_model\web\app.js`
- `python -m compileall -q src dashboard tests`
- `Get-ChildItem tests -Filter 'test_*.py' | ForEach-Object { python $_.FullName }`

## 2026-05-08 - Betting Settlement And Banker-Leg Tickets

Goal:

- Make the live betting panel usable after a race has paid out, and add banker/leg structures for exotic tickets instead of only showing flat combination rankings.
- Ensure every displayed betting ticket has an explicit stake calculation, even when the recommendation is `$0 / 不下注`.

Changes:

- `/api/betting` now attaches a `settlement` payload from the betting ledger and reconciles resulted races before returning the payload.
- The betting panel shows settled tickets as `中`, `唔中`, or `待派彩`, including stake, returned amount, profit, final odds, and CLV.
- WIN/PLACE winning tickets now wait for final odds before settlement; losing tickets can still settle from the official result.
- Added `banker_leg_suggestions` to betting output for QPL, TRIO, TCE, and FIRST4.
- Banker/leg suggestions include banker runners, leg runners, full-leg cover flag, combination count, and model-use notes.
- WIN/PLACE, exotic candidates, and banker/leg structures now expose recommended stake and minimum ticket cost.
- Exotic candidates inherit the scaled Kelly stake from the matched betting decision when final/probable dividend and edge are sufficient; otherwise they show `$0` with a no-bet reason.
- Banker/leg structures show total recommended stake, per-combination estimate, combination count, and minimum cost.
- UI renders a `膽 / 腳建議` section and keeps the existing combination candidate section.

Verification:

- `python tests\test_betting.py`
- `python tests\test_betting_settlement.py`
- `python tests\test_betting_ledger.py`
- `node --check src\racing_model\web\app.js`

## 2026-05-08 - Late Market Flow Report Layer

Goal:

- Turn the existing late odds movement features into a race-level analysis layer for the betting model, covering live odds flow, tick coverage, steam/drift signals, and data-readiness warnings.

Changes:

- Added `market_flow_report()` and `/api/market-flow`.
- The report excludes final/result odds from live-flow calculations so closing prices do not leak into pre-race signals.
- Added UI panel `臨場資金流 / 賠率異動` under race situation charts.
- The panel shows coverage rate, live tick count, source list, strongest steam/drift runners, and Chinese model-use warnings.
- Added tests for actionable steam/drift detection and thin-sample detection.

Verification:

- `python tests\test_market_flow.py`
- `python tests\test_late_market_flow.py`
- `python -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-08 - Exotic Ledger Final Dividend Settlement Gate

Goal:

- Prevent combination-pool ROI from being polluted by settling winning exotic tickets before official final dividends are available.

Changes:

- Updated exotic betting-ledger reconciliation so winning QIN/QPL/FCT/TRIO/TCE/FIRST4/QUARTET tickets require `dividend_status='final'` before they are marked reconciled.
- Losing exotic tickets can still settle from the official race result without final dividend, because payout is zero and P/L is known.
- `final_exotic_dividend()` no longer falls back to probable dividends for settlement.
- Added tests for:
  - winning exotic ticket with only probable dividend remains pending;
  - losing exotic ticket settles without final dividend;
  - existing final-dividend exotic win still reconciles correctly.

Verification:

- `python tests\test_betting_ledger.py`
- `python tests\test_pool_replay.py`
- `python -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-08 - GitHub Sync And Production Redeploy

Goal:

- Confirm the latest performance-optimized racing model build is recorded, uploaded to GitHub, and redeployed to production for cross-device continuation.

Status:

- Local branch: `codex/horse-racing-model`.
- Base optimized commit before this handoff: `2b4086c`.
- Worktree was clean before adding this handoff note.
- The latest optimized build includes adaptive prediction policy, fast race dashboard loading, deferred betting/exotic/feed/secondary panels, improved table readability, and analytics dashboard consolidation.

Deployment note:

- This entry exists so another Codex instance can see that the optimized build was explicitly checked, synced, and redeployed after the May 7 optimization pass.

## 2026-05-07 - System Performance And Prediction Optimization Pass

Goal:

- Make the race page faster, easier to read, and more accurate under the currently available data.

Changes:

- Added `src/racing_model/adaptive.py`.
- Added adaptive prediction policy selection from dual-track backtest:
  - `baseline` when sample is insufficient or mixed.
  - `ability` when pure-ability track leads.
  - `market_blend` when market-fusion track leads.
- In `market_blend`, model probabilities are blended with normalized HKJC implied probabilities, then EV and value gaps are recalculated.
- Cold dashboard requests now use a fast policy default and refresh the formal dual-track policy in a background thread, so policy calibration does not block the race page.
- Background policy refresh is deliberately delayed for 2 seconds after a cold request so it does not compete with the first dashboard response.
- Added `/api/race-dashboard` to return race state, race list, predictions, betting, ledger, feed health, model comparison, odds history, results, and weather in one response.
- Added `/api/analytics-dashboard` for the analytics view so the frontend no longer chains many sequential API calls.
- Dashboard betting now returns fast WIN/PLACE decisions first and defers heavier exotic/all-pool calculation to a background frontend refresh.
- Odds-feed health is also deferred to a background panel refresh so slow feed-audit queries do not block the main prediction table.
- Model comparison, odds history, results, and weather are now rendered as loading placeholders and fetched concurrently after the main dashboard response.
- Betting recommendations and betting ledger are also deferred; the first dashboard response is now focused on race list plus adaptive predictions.
- Updated the race page to use the dashboard payload and show the active prediction policy.
- Cleaned key visible Chinese labels in the race sidebar/status areas and improved table readability with sticky headers, zebra rows, hover state, and tabular numerals.
- Added `tests/test_adaptive.py`.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- Direct execution of adaptive and pool-replay test functions because local Python does not have `pytest` installed.
- Local smoke: cold `/api/race-dashboard` returned in about `1871ms` before betting deferral; production was further optimized by deferring betting and ledger from the first response.
- Local smoke: `/api/analytics-dashboard` returned successfully; analytics remains intentionally lazy-loaded.

## 2026-05-07 - Pool-Specific Replay Settlement Report

Goal:

- Compare HKJC betting pools by actual settled performance instead of looking only at individual tickets.

Changes:

- Added `src/racing_model/pool_replay.py`.
- Added `/api/pool-replay` and `/api/pool-replay/reconcile`.
- The report groups saved betting recommendations by WIN, PLACE, QIN, QPL, FCT, TRIO, TCE, FIRST4, and QUARTET.
- Each pool now reports tickets, settlement rate, hit rate, ROI, profit, average expected value, cost-adjusted EV, average final dividend, average CLV, low-return hits, and max drawdown.
- Added an analytics UI panel, `投注方法回測`, with a global settlement button and pool-level cards.
- Added `tests/test_pool_replay.py`.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- Direct execution of pool-replay test functions because local Python does not have `pytest` installed.

## 2026-05-07 - Dual Track Backtest Report

Goal:

- Turn the single-race pure-ability vs market-fusion comparison into a cross-race backtest so model iteration can be judged by measurable accuracy gains.

Changes:

- Added `dual_model_backtest()` in `src/racing_model/model_compare.py`.
- Added `/api/model-comparison-backtest`.
- The report compares pure ability and market-fusion tracks across resulted races using top-pick win rate, top-pick top-3 rate, average winner rank, Brier score, log loss, agreement rate, and disagreement edge.
- Added a `雙軌模型回測` panel in the analytics view with summary metrics, recommendation text, and recent race-level cases.
- Extended `tests/test_model_compare.py` to cover the new cross-race backtest.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- Direct execution of model-comparison test functions because local Python does not have `pytest` installed.
- Local smoke: `/api/model-comparison-backtest` returned `races=9`, `ability_win=0.0`, `market_win=0.5555555555555556`, `verdict=market_leads`.

## 2026-05-07 - Sync Remote Progress And Runtime Audit

Source branch:

- `origin/codex/horse-racing-model`
- Synced from local `a8e25ed` to remote `09d0536`

Remote progress reviewed:

- Equation coverage report and `/api/coverage`.
- Same-day track bias features.
- Late market flow features.
- Persistent error taxonomy and `/api/error-taxonomy`.
- Betting recommendation ledger and reconciliation.
- Out-of-sample model registry.
- Exotic dividend EV support and live exotic dividend refresh.
- Active-race-only live refresh.
- Race menu grouping by status/date.
- Top-level UI views for race analysis, equation coverage, and analytics.
- Production Docker deployment with PostgreSQL.
- Deployment docs and production env example.

Security review:

- No root password committed.
- No API key/token committed.
- `.env`, `data/racing.db`, `data/raw`, `data/hkjc`, `reports`, `models/*.json`, caches, and generated logs remain ignored.
- Production examples use placeholders such as `POSTGRES_PASSWORD=change-me-before-deploy`.

Local verification:

- `python -m compileall -q src dashboard tests`
- All `tests/test_*.py`
- `node --check src/racing_model/web/app.js`
- Local API smoke:
  - `/api/state`
  - `/api/coverage`
  - `/api/error-taxonomy`
  - `/api/betting-ledger`

Fix applied locally during audit:

- `src/racing_model/storage.py`
- `src/racing_model/live.py`

The remote update used `ZoneInfo("Asia/Hong_Kong")`. On Windows Python without the `tzdata` package, local API requests crashed with `ZoneInfoNotFoundError`. Added a fallback to fixed UTC+8 so local Windows development and production Linux both work.

Deployment expectation:

- Every feature/fix/removal must be committed and pushed to GitHub from the local repo.
- Every feature/fix/removal must then be deployed to the production server.
- The server is deployment-only; it should not be treated as the source of truth.

## 2026-05-07 - Pool Cost And Market Efficiency Gate

Goal:

- Advance roadmap item 22, "Pool takeout and market efficiency".
- Prevent nominal positive EV recommendations from passing unless they clear pool-specific cost and noise gates.

Implementation:

- Added `src/racing_model/pool_rules.py`.
- Added HKJC pool payout/takeout assumptions for WIN, PLACE, QIN, QPL, FCT, TRIO, TCE, FIRST4, and QUARTET.
- Added pool minimum units and market-efficiency buffers.
- Extended betting decisions with `pool_rule`, `required_expected_value`, `required_edge`, `required_dividend`, and `cost_adjusted_expected_value`.
- Updated WIN/PLACE and exotic ticket eligibility to require pool cost gates before any stake is recommended.
- Updated the betting UI to display pool-cost model count, cost-adjusted EV, required dividend, and takeout/noise buffer.
- Updated coverage report item 22 from missing to partial.

Verification:

- Added betting tests for pool rules and cost-gate rejection.
- Full local verification and server deployment required before handoff.

## 2026-05-07 - Final Place Odds For All Runners

Goal:

- Preserve every runner's final place odds for completed races, not only the three placed horses.
- Ensure losing runners become usable negative samples for place EV, calibration, replay, and future training.

Implementation:

- Added `hkjc_final_place_snapshot` rows from the last live HKJC place odds tick when official result dividends only list placed horses.
- Added `final_place_snapshot_rows()` and `freeze_final_place_snapshots()` in `src/racing_model/storage.py`.
- Called the freeze step when auto-importing completed HKJC results and when loading historical race days.
- Updated `/api/results` to fill `final_place_odds`, `final_place_odds_source`, and `top3_expected_value` from the latest official/live place odds source for every runner.
- Updated the results table to show position odds and position expected value for all runners.

Verification:

- Added `tests/test_final_place_snapshots.py`.

## 2026-05-07 - Official Final Place Odds Backfill

Goal:

- Backfill completed-race place odds for every runner from official HKJC WIN/PLA odds sources where available.
- Avoid estimated or synthetic place odds for completed races, because those rows would pollute model training and place-EV calibration.

Implementation:

- Added `hkjc_final_place_backfill` as an official final-place source.
- Added official-only `backfill_final_place_odds()` and `build_official_odds_provider()` in `src/racing_model/odds.py`; the chain never falls back to development snapshot odds.
- Updated the HKJC GraphQL query to match the betting SPA whitelist shape and decode gzip responses.
- Added `/api/backfill-final-place-odds` and wired `/api/refresh-results` / lifecycle steps to attempt official final-place backfill after results are imported.
- Added `backfill-final-place-odds` CLI command for one race or all resulted races.
- Added `/api/results` place-odds completeness metadata and UI summary text such as `位置賠率 3/14`.
- Fixed MQTT callback handling when the broker rejects empty credentials, preventing background callback exceptions.

Reality check:

- For `HK20260506-ST-01`, HKJC GraphQL currently returns no historical WIN/PLA oddsNodes, so no missing runner place odds can be filled from the official source after the fact.
- The system now records this as source unavailable instead of estimating values. Future race days with live GraphQL/MQTT ticks will preserve all runners automatically.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- Direct execution of `tests/test_final_place_snapshots.py` test functions because local Python does not have `pytest` installed.
- `python -m racing_model.cli backfill-final-place-odds --race-id HK20260506-ST-01` confirmed official historical oddsNodes are unavailable for that old race and left missing rows unfilled.

## 2026-05-07 - Live Odds Recorder Health Center

Goal:

- Make WIN/PLA tick completeness visible while races are still scheduled/live.
- Prevent silent training-data gaps by showing which runners have no official WIN or PLA ticks before the race is frozen.

Implementation:

- Added `src/racing_model/feed_health.py`.
- Added `/api/odds-feed?race_id=...` with per-race and per-runner official tick coverage.
- The report separates official/live sources from development fallback rows, counts source usage, detects stale feeds, and marks resulted races as training-ready only when final place odds are complete.
- Added a top-level UI panel, `賠率錄影健康`, showing WIN/PLA coverage, official/live tick counts, latest official timestamp, training readiness, and each runner's missing tick status.
- Added `tests/test_feed_health.py`.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- Direct execution of feed-health and final-place test functions because local Python does not have `pytest` installed.

## 2026-05-07 - Dual Track Model Comparison

Goal:

- Compare pure ability ranking against the current market-fusion ranking on the same race screen.
- Make it visible when market odds or late-money features are pulling the model away from fundamentals.

Implementation:

- Added `src/racing_model/model_compare.py`.
- Added `/api/model-comparison?race_id=...`.
- The ability track masks `market_implied` and all late-flow features while keeping horse ability, form, draw, rider/trainer, workout, pace, and same-day track-bias features.
- Added a `雙軌模型對照` UI panel showing pure ability top pick, market-fusion top pick, whether they agree, maximum rank swing, and each runner's rank/probability under both tracks.
- Added `tests/test_model_compare.py`.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- Direct execution of model-comparison, feed-health, and final-place test functions because local Python does not have `pytest` installed.

## 2026-05-08 - Persistent Independent Race Watch

Goal:

- Keep an unopened/scheduled independent race page awake until the race status changes, instead of letting the active race expire after a short TTL.
- Continue checking live races for HKJC results so the system can automatically flip a race to completed and preserve final odds/training material.

Implementation:

- Changed `AppState.focus_race()` / `active_race()` so active race tracking is persistent and `expires_in_seconds` is reported as `null`.
- Updated lifecycle selection to keep processing active races in `scheduled` or `live` status, and to stop tracking only after the race is detected as `resulted`.
- Updated `/api/watch-race` so completed races are not kept as active live-refresh targets.
- Removed browser-tab visibility sleep for the race page: the frontend pauses visible repaint while hidden, but the server keeps refreshing the watched race.
- Added regression coverage for non-expiring active races and live-race result detection.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`

## 2026-05-08 - Betting Slip Dividend/Label Cleanup

Goal:

- Remove duplicate-looking settled tickets from the race page settlement panel.
- Fix garbled win/place labels and actionable counting in the pool-choice model.
- Pull official HKJC exotic odds/dividends into combination candidates instead of leaving all candidate dividends blank when HKJC has already published them.

Implementation:

- Changed the HKJC exotic dividend GraphQL provider to use the same whitelisted query shape and browser-compatible headers as the working WIN/PLA provider.
- Added gzip response handling and fixed MQTT exotic connect-result parsing to avoid callback errors.
- Added an opportunistic exotic-dividend refresh inside `/api/betting` when a scheduled race has no local exotic dividend rows yet.
- Fixed `market_label("WIN")` / `market_label("PLACE")` and the `有值博` actionable check used by the pool-choice scorecard.
- Deduped settlement display by logical race/market/horse/risk/model key, keeping the latest ticket while retaining the raw ticket count for diagnostics.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`

## 2026-05-08 - Fast Race Screen Betting Refresh

Goal:

- Show useful race and betting information immediately instead of making the page wait for heavy HKJC exotic dividend refreshes.
- Keep every odds-dependent block inside the live betting recommendation area refreshed on the 30-second cycle.

Implementation:

- Added a fast betting preview to `/api/race-dashboard`; it reuses the already-computed predictions and returns WIN/PLACE decisions immediately while complex exotic tickets continue loading in the background.
- Changed missing exotic-dividend handling in `/api/betting` from synchronous HKJC fetch to a per-race background refresh job, so opening a race page does not block on three HKJC exotic pool requests.
- Added an `exotic_refresh` payload so the UI can state when official combination odds are syncing in the background.
- Added frontend protection against overlapping betting refreshes; if a 30-second refresh arrives while betting is still updating, the next update is queued and applied afterward.
- The existing 30-second race refresh now refreshes the fast betting preview from latest WIN/PLA odds and then replaces it with the full betting engine output once ready.
- Changed live `/api/betting` ledger recording to async mode by default, annotating deterministic recommendation IDs immediately while writing the training/ledger row in the background.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`

## 2026-05-08 - Cached Prediction Reuse For Live Betting

Goal:

- Avoid recomputing the full adaptive race prediction every time the live betting recommendation panel refreshes.
- Keep the 30-second betting cycle fast while still using the latest dashboard WIN/PLACE odds snapshot for every odds-dependent betting block.

Implementation:

- Added a short-lived per-race prediction cache in `AppState`.
- `/api/race-dashboard` now stores the predictions it already computed for the selected race.
- `/api/betting` reuses that recent prediction snapshot within the odds-refresh window, then only falls back to full adaptive prediction when the cache is stale or missing.
- API JSON responses now use compact encoding and gzip when the client supports it, reducing heavy dashboard/betting payload transfer time during 30-second live refreshes.
- Added regression coverage proving that full betting refresh uses the dashboard prediction cache instead of rerunning adaptive prediction.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests/test_fast_betting_refresh.py -q`

## 2026-05-08 - Exotic Dividend Training And Race Page Cleanup

Goal:

- Stop losing training value from Tierce/Quartet and other exotic bets.
- Treat model-generated betting tickets as executed suggestions instead of requiring a manual UI confirmation.
- Stop repeated 30-second refreshes from creating duplicate-looking betting ledger tickets.
- Split the independent race screen into clearer tabs so the race page is not one long wall of panels.

Implementation:

- Result-page parsing now imports final exotic dividends from the HKJC dividend table, including TCE/三重彩 and QUARTET/四重彩, into `exotic_dividends` as `hkjc_results_final` training rows.
- HKJC exotic live parsing now also reads nested banker odds when GraphQL supplies them.
- Betting recommendation IDs now use a stable logical key per race/market/selection/risk/model, so odds refreshes update one recommendation instead of creating repeated tickets.
- Active tickets are automatically marked as executed at the recommendation odds and stake; the UI no longer asks the user to confirm bets manually.
- Betting ledger and pool replay dedupe older repeated rows before display/backtest, preventing refresh noise from polluting model iteration.
- The race page now has tabs for overview, betting, results/odds, and diagnostics.
- 30-second dashboard fast previews no longer replace the full betting engine while the full panel is already visible, preventing the betting section from shrinking during refresh.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`
