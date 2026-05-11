# Development Log

This file records cross-device Codex handoffs, audits, fixes, pushes, and server deployments.

## 2026-05-12 - Restore Analytics Reports and Historical Backfill Discipline

Goal:

- Restore the backtest / intelligent iteration center and model version center so they do not stop at a deferred placeholder.
- Make historical HKJC backfill crawl a day once, persist it, then use the database cache for completed meetings.
- Skip date/venue combinations that do not have a matching HKJC meeting instead of repeatedly crawling every race number.
- Preserve the 2026-05-08 baseline model file in Git so deployment cannot fall back to all-zero probabilities again.

Changes:

- The analytics page still quick-loads with `/api/analytics-dashboard?fast=1`, then now fetches the real heavy reports in the background: backtest, evolution, dual-track comparison, error taxonomy, model versions, model registry, pool replay, promotion scorecard, and coverage.
- Replaced the "Walk-forward model comparison is deferred" placeholder with a loading state that gets overwritten by real `/api/model-versions` output.
- Capped immediate walk-forward comparison to a short recent rolling window by default, and reduced the browser/API epochs, so the model version center returns real out-of-sample evidence without re-fitting the whole historical universe on every page load.
- Capped browser/API backtest to a recent race window; full-history backtest remains available to CLI/offline workflows by omitting the limit.
- Capped browser/API evolution diagnostics to a recent race window so the intelligent iteration view can update without tying up the server.
- Historical date-range backfill now checks completed meetings in `races` / `runners` / `results` / `race_status` before any HKJC crawl.
- Backfill now probes only race 1 for date/venue availability and skips non-meeting days with `no_matching_hkjc_meeting`.
- Training after backfill now includes a black-box fake-ticket replay: train on prior old races, emit fake WIN and PLACE tickets for the next old race, compare with actual results, then refit the final model on all stored results.
- Added `models/baseline.json` as a tracked model artifact while leaving other generated model JSON files ignored.

Verification:

- `python -m pytest tests/test_backfill_cache.py tests/test_ui_localization.py -q`
- `python -m pytest tests/test_backfill_cache.py tests/test_race_status.py tests/test_active_race_refresh.py tests/test_model_registry.py tests/test_model_compare.py tests/test_promotion_scorecard.py -q`
- `python -m compileall src/racing_model/backfill.py src/racing_model/app_server.py`

## 2026-05-12 - Handle Official HKJC Void Races

Goal:

- Correct the 2025-11-15 Sha Tin day where Race 8 was an official HKJC void race, not an upcoming race.
- Make historical race-day loading include valid later races when the meeting has more races than previously requested.
- Prevent completed races already stored in the database from being crawled again and blocking unfinished-race refresh.
- Stop unfinished-race analysis from silently falling back to empty/untrained probabilities or development odds.

Changes:

- Added HKJC result-page void detection for races declared void by the stewards.
- Void races now keep the official runner roster and race metadata, but do not create fake finishing positions, odds, or payouts.
- Result-page runner fallback rows now carry empty localization fields so partial Chinese result-page matches cannot insert `NULL` into production runner columns.
- Historical race-day loading now skips races already marked `resulted` when the database has result rows or an official void-race note.
- Manual result refresh now returns `database_cache` for completed stored races instead of fetching HKJC again.
- Global 30-second refresh selection now ignores stale past scheduled rows, while still allowing explicitly live races to keep refreshing.
- The 30-second background loop now refreshes the next unfinished race globally when no race is actively focused.
- `auto` odds refresh now uses only official HKJC GraphQL/MQTT sources; the development snapshot-jitter provider is available only when `RACING_ODDS_PROVIDER=dev` is explicitly set.
- Prediction features, market-flow, odds history, and race-list odds counts now ignore development snapshot-jitter rows so stale dev data cannot masquerade as live official data.
- Server startup now trains and saves `models/baseline.json` from the database if the model file is missing, instead of using an all-zero model that outputs uniform 8.3% / 25.0% probabilities.
- `load_hkjc_race_day()` and manual result refresh now mark official void races as `resulted` with `official_void_race` notes.
- `refresh_race_statuses()` preserves that verified void-result state even when there are no result rows.
- Updated the coverage report to mention official void-race recognition as part of data quality/risk handling.

Verification:

- `python -m pytest tests\test_race_status.py tests\test_active_race_refresh.py tests\test_hkjc_parser.py tests\test_odds_provider.py tests\test_market_flow.py tests\test_coverage.py tests\test_fast_betting_refresh.py -q`
- `python -m compileall -q src tests`

## 2026-05-12 - Reduce Race Status Refresh Locking

Goal:

- Fix the production app hanging on loading screens when concurrent API requests and the 30-second background refresh collide on `race_status` writes.
- Keep the race status refresh behavior, but avoid rewriting every race row when the derived status has not changed.

Changes:

- Updated `refresh_race_statuses()` to skip `race_status` upserts for unchanged rows.
- This prevents read-heavy endpoints such as `/api/state`, `/api/races`, and analytics refreshes from generating hundreds of unnecessary row writes per request.
- Changed `/api/analytics-dashboard` to default to a fast/deferred payload so opening the analytics view no longer runs heavy full-history backtest, walk-forward, dual-track, registry, and pool replay reports inline.
- Updated the frontend analytics refresh to request `fast=1` and show a quick-loaded state for deferred backtest data.
- Added regression coverage that verifies a second unchanged race-status refresh performs no extra database writes.
- Added regression coverage that fast analytics does not call heavy report builders.
- Production was manually restarted first to clear the existing blocked transactions and restore service before the code fix.

Verification:

- `python -m pytest tests\test_race_status.py tests\test_active_race_refresh.py tests\test_coverage.py -q`
- `python -m pytest tests\test_analytics_dashboard.py -q`
- `python -m compileall -q src tests`
- Production smoke check after restart: `/api/state`, `/api/races`, and `/api/lifecycle` returned in under 1 second.

## 2026-05-09 - Coverage Status Progress Score

Goal:

- Fix the misleading coverage display where the old status-only score stayed flat after real maturity improvements.
- Make the visible status score move when a tested 36-indicator development layer lands.

Changes:

- Changed `status_coverage_score` into a status progress score that blends coarse status weight with maturity progress.
- Added `coarse_status_score` as the true old coarse baseline for reference.
- Updated the coverage UI labels from `舊狀態分` to `狀態進度分` and added `粗分類基準`.
- Updated coverage tests to assert the score ordering: coverage score > status progress score > coarse baseline.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_coverage.py -q`

## 2026-05-09 - Walk-Forward Pool Choice Optimizer

Goal:

- Continue indicator 24 彩池選擇模型 after the contextual replay gate.
- Make pool-choice restrictions measurable against a baseline instead of relying only on full-sample ROI.
- Move the coverage score only for tested model-control functionality.

Changes:

- Added `pool_choice_optimizer` to pool replay reports.
- The optimizer replays each settled ticket chronologically and only uses prior settled tickets to decide whether that pool would have been pass/reduce/block at that moment.
- Each pool now reports baseline ROI, walk-forward gated ROI, ROI delta, max drawdown, ticket retention, reduced tickets, blocked tickets, and current optimizer policy.
- Betting pool-choice rows and UI cards now show optimizer status, policy, walk-forward ROI, and ROI improvement.
- Updated coverage item 24 to reflect the new walk-forward optimizer layer.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_pool_replay.py tests\test_betting.py tests\test_coverage.py -q`

## 2026-05-09 - Contextual Pool Replay Gate

Goal:

- Continue the 36-indicator buildout, mainly indicator 24 彩池選擇模型 and indicator 25 組合派彩預測.
- Stop a pool from being trusted only by global ROI when the current race context has a different replay profile.
- Make the coverage score move only when a tested modelling/control layer is actually added.

Changes:

- Added contextual pool replay segments by track, distance bucket, class, track/distance, and track/distance/class.
- Active betting now passes the current race conditions into the pool replay gate.
- When the matching context slice has enough settled tickets, the gate can block/reduce stake by that slice ROI; if the slice is under-sampled it falls back to the global pool gate.
- Pool-choice cards now show whether replay is using a current-race slice, global fallback, or global restriction.
- Updated coverage items 24 and 25, raising maturity from 52.5% overall to 52.6% overall while leaving the old status-only score at 51.8%.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_pool_replay.py tests\test_betting.py tests\test_coverage.py -q`

## 2026-05-09 - Granular Coverage Maturity Score

Goal:

- Make 方程式覆蓋率 reflect actual incremental model development.
- Avoid a flat score when a partially completed indicator gets materially stronger but is not fully complete yet.

Changes:

- Added per-indicator `maturity_score`, `maturity_percent`, and `maturity_reason`.
- `coverage_score` now uses maturity scores instead of only coarse status weights.
- Kept `status_coverage_score` so the old 51.8% status-only score remains visible for comparison.
- Updated indicators 24 彩池選擇模型 and 25 組合派彩預測 to reflect the official dividend gate, final-dividend audit/reconcile, and pool replay gate work.
- Coverage UI now shows both current score and old status score, plus each item maturity and reason.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_coverage.py -q`

## 2026-05-09 - Pool Replay Gate For Pool Choice

Goal:

- Continue indicator 24 彩池選擇模型 and indicator 25 組合派彩預測.
- Stop pool-choice from trusting a pool only because the current estimated EV is high.
- Use settled betting ledger / final dividend replay as a per-pool safety gate before live staking.

Changes:

- Added `pool_replay_calibration()` to convert pool replay into per-pool statuses: pass, no replay data, sample building, waiting final dividend, ready to reconcile, replay reduce, and replay block.
- Betting decisions now accept a `pool_replay_gate`; poor settled ROI can block a pool, unresolved final dividends can reduce stake, and every ticket/candidate carries the replay reason.
- Pool-choice scorecards now include replay status, sample size, replay ROI, stake factor, and a `replay_blocked` verdict.
- Race API caches the pool replay gate for 60 seconds so active race refreshes do not recalculate heavy replay state every 30 seconds.
- UI pool-choice cards now show replay status, replay ROI, sample count, and the reason beside official price quality.
- Updated the 36-indicator coverage report for items 24 and 25.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest -q`

## 2026-05-09 - Audit-Driven Final Dividend Reconciliation

Goal:

- Continue indicator 25 組合派彩預測 by turning final-dividend audit into an action.
- Let pool replay reconcile confirmed exotic tickets after results/final dividends become available.
- Reduce manual steps after a race finishes.

Changes:

- Added `reconcile_pool_replay_with_final_dividends()` to refresh HKJC results/final dividends for races flagged by `final_dividend_audit`, then run betting-ledger reconciliation.
- `/api/pool-replay/reconcile` now returns before/after final-dividend audit summaries, refresh targets, refresh outcomes, and the updated pool replay.
- Global update now immediately reconciles the race ledger and pool replay after a race is marked resulted and final odds/dividends are imported.
- The pool replay UI status message now reports how many races were refreshed and how many tickets are still waiting for final dividends.
- Updated the 36-indicator coverage report for item 25.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_active_race_refresh.py tests\test_pool_replay.py tests\test_betting_ledger.py -q`

## 2026-05-09 - Final Dividend Audit For Pool Replay

Goal:

- Continue indicator 25 組合派彩預測 by making pending exotic settlements explainable.
- Show which combination tickets are waiting for final dividends before their ROI is trusted.
- Help pool-choice replay avoid counting unresolved winning exotic tickets as missing or losing data.

Changes:

- Added `final_dividend_audit` to `pool_replay_report`.
- The audit splits pending confirmed exotic tickets into no results, known losses ready to settle, winning tickets waiting for final dividend, and winning tickets whose final dividend is already available.
- Pool-level replay rows now expose final-dividend waiting hits, final-ready hits, and known-loss unsettled exotic tickets.
- The analytics UI now shows final dividend wait counts in the pool replay summary and per-pool cards.
- Updated the 36-indicator coverage report for items 24 and 25.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_pool_replay.py tests\test_betting_ledger.py tests\test_betting_settlement.py -q`

## 2026-05-09 - Official Exotic Dividend Gate For Pool Choice

Goal:

- Strengthen indicator 24 彩池選擇模型 and indicator 25 組合派彩預測.
- Prevent unverified or estimated exotic dividends from becoming real betting tickets.
- Make pool-choice ranking prefer pools with official HKJC probable dividend coverage.

Changes:

- Added exotic dividend quality labels: official probable, final result, estimated, unverified, and missing.
- Exotic tickets now require HKJC GraphQL/MQTT `probable` dividends before they can pass the betting gate; final result dividends remain replay/settlement only, and unverified/manual prices are watch-only.
- Pool-choice scoring now includes official price quality and official dividend coverage, with a separate `need_official_dividend` verdict for positive-EV pools that still lack official live prices.
- The live betting UI now shows dividend quality, official coverage, and the price-gate reason on pool-choice and exotic candidate cards.
- Updated the 36-indicator coverage report for items 24 and 25.

Verification:

- `py -3.12 -m compileall -q src dashboard tests`
- `node --check src\racing_model\web\app.js`
- `$env:PYTHONPATH='src'; py -3.12 -m pytest tests\test_betting.py tests\test_exotic_dividends.py tests\test_exotic_live.py -q`
## 2026-05-09 - Keep Partial Official Results Resulted

Goal:

- Fix same-day races that have official HKJC result rows but fewer result rows than runners, such as scratched/non-result runners, being downgraded back to scheduled.
- Restore valid payout reconciliation tickets that were cleared from settlement only when they still pass the live model gate at the latest official pool price.

Changes:

- Updated race status refresh logic so a non-future race with any official result rows remains `resulted`.
- Updated odds refresh status inference to keep races with result rows as `resulted` even when result count is below runner count.
- Added regression coverage for a past race with one official result and one scratched runner.
- Updated the coverage report source list for the lifecycle/feed-health item to include `src/racing_model/storage.py`.
- Production maintenance: restored 3 gate-passing race 9 cleared tickets at latest official pool prices; kept 1 race 9 ticket blocked because latest WIN price 6.20 was below required 6.21.
- Verified race 10 and race 11 across conservative, standard, and aggressive risk profiles currently return `no_edge`, so no ticket was forced before the 5-minute training-fill window.

Verification:

- `python -m pytest tests\test_race_status.py tests\test_active_race_refresh.py tests\test_betting_ledger.py tests\test_fast_betting_refresh.py tests\test_betting_settlement.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`

## 2026-05-09 - Refresh Codex Handoff Notes

Goal:

- Make the latest betting, settlement, production cleanup, and deployment context easy for another Codex agent to pick up.

Changes:

- Updated `docs/HANDOFF_CONTEXT.md` with a clean ASCII latest-handoff section.
- Documented the current branch, production app path, recent betting-ledger behavior changes, 30-second live price refresh rule, model-gated execution, pre-post training-fill rule, production settlement cleanup, relevant files, verification commands, and deployment pattern.

Verification:

- Documentation-only change; reviewed with `git diff`.

## 2026-05-09 - Clear Current Settlement Tickets

Goal:

- Clear the current payout reconciliation ticket list on production before the next betting-model run.
- Preserve model recommendation rows for audit instead of deleting the underlying analysis records.

Changes:

- Backed up the production `betting_recommendations` table to `/opt/hkjc-model/data/backups/betting_recommendations_clear_20260509_070310.sql`.
- Reset all production rows with `execution_status='confirmed'` back to `suggested`.
- Cleared execution, live odds, stake, settlement, return, profit/loss, CLV, slippage, and reconciliation fields for those rows.
- Marked cleared rows with `execution_value_status='cleared_from_settlement'` so the manual maintenance action remains traceable.

Verification:

- Production confirmed ticket count: `194 -> 0`.
- `curl http://127.0.0.1:8765/api/state` on the server returned `ok`.

## 2026-05-09 - Pre-Post Training Fill Tickets

Goal:

- Preserve the strict model gate in normal betting decisions, but guarantee enough pre-race execution samples for training when the race is about to start.
- Avoid selecting high-odds/low-hit-rate tickets just because the payout is large.

Changes:

- Added a pre-post training-fill rule: during the final 5 minutes before estimated post time, if confirmed settlement tickets for the race are fewer than 5, the ledger fills to 5 using the closest-to-gate candidates.
- Training fill requires at least 2 exotic-pool tickets when available.
- Supplemental candidates are selected from WIN/PLACE decisions and exotic decisions, using a score dominated by win/top-3/hit probability; EV, edge, price gap, and official source are secondary and capped.
- Fill tickets use minimum ticket cost when the model did not assign a stake, are marked with `execution_value_status='pre_post_training_fill'`, and remain visible as confirmed training samples for settlement replay.
- The rule is active only inside the 5-minute pre-post window; outside that window, positive-stake rows still need the normal execution model gate.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_fast_betting_refresh.py tests\test_betting.py tests\test_betting_settlement.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Model-Gated Ticket Execution

Goal:

- Stop treating every positive-stake recommendation as an executed ticket.
- Make payout reconciliation replay only tickets that passed the model's execution gate after the 36-factor analysis, pool-choice, edge, EV, live price, calibration, and exposure checks.

Changes:

- Added an execution model gate before auto-confirming tickets in `betting_recommendations`.
- Positive-stake rows can now remain `suggested` when the model action is not `有值博`, live official odds/dividend is missing, latest pool price is below required dividend, EV/edge is not positive, calibration blocks, exposure blocks, or pool-choice verdict is not actionable.
- Manual confirmation now also goes through the execution model gate; stale or non-official prices are blocked instead of being forced into settlement.
- Suggested exotic tickets can upgrade to confirmed on a later 30-second refresh once official probable dividends arrive and all gate checks pass.
- Payout reconciliation continues to use only `execution_status='confirmed'`, so stored suggestions are audit material rather than simulated bets.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_fast_betting_refresh.py tests\test_betting_settlement.py -q`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Live Pool Price Refresh For Open Tickets

Goal:

- Ensure same-day betting advice never treats bet-time odds/dividends as locked or estimated.
- Keep payout reconciliation, the betting-slip engine, pool-choice scoring, and exotic candidates tied to the latest official pool price refreshed every 30 seconds.

Changes:

- Added `refresh_open_betting_prices()` so `/api/betting` refreshes every open confirmed ticket from the latest official WIN/PLACE tick or probable exotic dividend before building settlement output.
- Same-ticket refresh now preserves the ticket state and stake, but updates `execution_odds`, execution value status, and slippage from the latest pool price instead of keeping the older execution price.
- WIN/PLACE staking now requires official live HKJC odds sources; non-live or estimated prices produce no active ticket.
- Exotic dividend lookup ignores `estimated` rows for live betting and waits for official probable dividends.
- `/api/betting` now returns `ledger_price_refresh` for observability.
- Reworded UI labels from `現時估算` / `官方/估算` to `最新彩池` / `官方即時`.
- Updated coverage items 23, 25, and 34 to record the 30-second live pool-price rule.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_fast_betting_refresh.py tests\test_ui_localization.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Settlement Requires Executed Tickets

Goal:

- Keep payout reconciliation limited to tickets that were actually simulated as bought.
- Remove the confusing and invalid `未入飛` concept from the payout reconciliation view.

Changes:

- `betting_settlement_payload` now filters out every row whose `execution_status` is not `confirmed`.
- Settlement summary reports ignored unexecuted suggestions internally, but they are not shown as tickets and do not contribute to stake, pending count, or P/L.
- Removed the settlement `已入飛 / 未入飛` filter group because settlement now only contains executed tickets.
- Updated coverage item 34 to state that settlement replay only uses confirmed execution rows.

Verification:

- `python -m pytest tests\test_betting_settlement.py tests\test_fast_betting_refresh.py tests\test_ui_localization.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Settlement Display Filters

Goal:

- Make payout reconciliation easier to inspect after the physical-ticket dedupe by adding display filters.

Changes:

- Added settlement filters for result status, execution status, and betting market.
- The settlement summary now shows both total tickets and the filtered visible count.
- Filter choices persist in browser local storage and re-render immediately without another API call.
- Added UI styling for the filter controls and an empty-filter message.

Verification:

- `python -m pytest tests\test_ui_localization.py tests\test_betting_settlement.py tests\test_fast_betting_refresh.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Global Logical Ticket Dedupe Hardening

Goal:

- Fix duplicate payout-reconciliation tickets globally, including Sha Tin race 2 `QPL 2+7`.
- Enforce the betting rule that the same logical ticket may only refresh or add stake; it must not duplicate or reduce the recorded stake.

Changes:

- Betting ledger recording now looks up existing tickets by logical key: race, market, horse/combination, risk profile, and model path.
- If an existing logical ticket has an older/legacy recommendation id, refreshes now reuse that row instead of inserting a second ticket.
- Refreshes no longer reduce the stored recommended stake or execution stake; lower later recommendations preserve the higher existing stake, while higher later recommendations are treated as add-stake.
- Added a database unique index on the logical ticket key as a final duplicate-prevention guard.
- Updated the 36-factor coverage report item 32 to include the storage-level ledger guard.
- Corrected the logical ticket key after production review: risk profile and model path do not define a separate physical ticket; only race, market, and horse/combination do.
- Settlement display dedupe now uses the same physical-ticket key, so `standard` and `aggressive` versions of the same ticket do not both appear in payout reconciliation.

Production data maintenance:

- Backed up production `betting_recommendations` before changing data.
- Removed all remaining duplicate logical ticket rows across production, not just selected races.
- For each duplicate group, kept one row and preserved the highest stake so cleanup acts like same-ticket add-stake rather than stake reduction.
- Re-ran the production cleanup with the corrected physical-ticket key, removing standard/aggressive duplicates such as Sha Tin race 2 `QPL 2+7`.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_betting_settlement.py tests\test_fast_betting_refresh.py -q`
- `python -m pytest tests\test_db_migrate.py tests\test_smoke.py -q`
- `python -m pytest tests\test_betting_ledger.py tests\test_betting_settlement.py tests\test_fast_betting_refresh.py tests\test_db_migrate.py tests\test_smoke.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- Production PostgreSQL duplicate-group verification query.

## 2026-05-09 - Production Race Status And Ticket Ledger Cleanup

Goal:

- Restore Sha Tin races 1, 3, and 4 on May 9 to unfinished status after premature result imports.
- Remove duplicate payout-reconciliation tickets while keeping one logical ticket per race/market/selection/risk/model key.

Production data maintenance:

- Backed up the affected production PostgreSQL tables before changing data.
- Set `HK20260509-ST-01`, `HK20260509-ST-03`, and `HK20260509-ST-04` back to `scheduled`.
- Deleted 42 premature result rows, 118 premature final odds/place snapshot rows, and 27 premature final exotic-dividend rows for those races.
- Deleted 63 duplicate betting recommendation rows, keeping the confirmed/latest row for each logical ticket.
- Verified the three races now have zero result rows, zero final odds/dividend rows, and zero duplicate logical ticket groups.

Verification:

- Production PostgreSQL verification query after the transaction.
- Production app container restarted successfully.

## 2026-05-09 - Ticket Dedupe And Official Result Gate

Goal:

- Stop duplicate betting tickets in payout reconciliation, while still allowing the same logical ticket to be refreshed or topped up.
- Prevent a race from becoming `resulted` before HKJC has actually published results; date/time alone must not settle a race.

Changes:

- Re-recording the same logical ticket now keeps one ledger row. If the stake increases, it is marked `同飛加注`; if it only refreshes odds/recommendation data, it is marked `同飛刷新`.
- Existing auto-confirmed tickets no longer get duplicated or silently treated as a new bet; lower refreshed recommended stake does not reduce the already-recorded execution stake.
- HKJC result refresh now has a result-window guard. For example, Sha Tin race 1 on May 9 is kept `scheduled` at 01:30 and will not fetch/import results before the first-race result window.
- Manual `mark-resulted` now goes through HKJC result refresh instead of directly marking the race as resulted.
- Updated the coverage / blind-spot report for items 19, 20, and 32.

Verification:

- `python -m pytest tests\test_betting_ledger.py tests\test_betting_settlement.py tests\test_fast_betting_refresh.py -q`
- `python -m pytest tests\test_race_status.py tests\test_active_race_refresh.py -q`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Bankroll Replay Risk Audit

Goal:

- Continue the 36-factor equation work by connecting correlated-exposure controls to bankroll replay, so stake discipline can be checked from recorded betting recommendations instead of only at ticket-generation time.

Changes:

- `pool_replay_report` now includes `bankroll_replay` with starting/ending bankroll, settled ticket count, staked/returned/profit, ROI, max drawdown, drawdown percentage, and the latest equity curve.
- Added a bankroll risk audit for race/day/horse/pool/combination exposure caps, including breach counts and top breaches.
- The analytics UI now shows bankroll drawdown, daily max exposure, and risk-control breach count inside the pool replay panel.
- Updated the coverage / blind-spot report for items 18, 19, 30, and 32, including fixing the stale note that candidate OOS reliability artifact was still missing.

Verification:

- `python -m pytest tests\test_pool_replay.py tests\test_betting.py -q`
- `node --check src\racing_model\web\app.js`

## 2026-05-09 - Coverage Reporting Discipline

Goal:

- Make equation progress visible after every development step by requiring the 36-factor coverage / blind-spot report to be updated alongside the development log.

Changes:

- Updated `docs/HANDOFF_CONTEXT.md` with a hard rule: every feature, fix, deletion, model/data/betting/UI behavior change must update the relevant `src/racing_model/coverage.py` item and blind-spot notes.
- Updated `README.md` so `/api/coverage` remains the canonical progress map and every future change must keep it current.
- This is a process/documentation change only; no model score changed.

Verification:

- `python -m pytest tests\test_coverage.py -q`

## 2026-05-09 - Walk Forward Experiment Manifest

Goal:

- Continue factor 31 `避免過度擬合` by saving a reproducible experiment manifest and ablation trail inside every walk-forward/model-registry report.

Changes:

- Walk-forward reports now include `experiment_manifest` with the tested variants, feature lists, removed/added features, temperature settings, compact metrics, baseline deltas, and promotion gate inputs.
- Model registry reports now expose `latest_experiment_manifest`, so saved OOS evaluations carry their experiment trail instead of only the final best score.
- The registry UI now displays the manifest coverage, ablation count, best variant, and top ablation deltas.
- Updated factor 31 coverage notes to mark experiment manifests / ablation trails as supported, with commit-level notes and data-version hashes left as the next gap.

Verification:

- `python -m pytest tests\test_model_registry.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-09 - Temperature Calibrated Model Promotion

Goal:

- Continue factor 31 `避免過度擬合` by making conservative temperature-calibrated candidates executable after promotion, instead of blocking them because the model file could not save the strategy.

Changes:

- `RankingModel` now saves and loads a `temperature` field, with legacy model files defaulting to `1.0`.
- Race prediction now applies saved win-score temperature before softmax, so promoted calibrated models use the same confidence strategy as walk-forward evaluation.
- Walk-forward variants now build models with their own temperature directly, and registry promotion can persist the `conservative_calibrated` variant when gates allow it.
- Updated factor 31 coverage notes to mark temperature-calibration serialization as supported and keep experiment manifests / ablation trails as the next open gap.

Verification:

- `python -m pytest tests\test_model.py tests\test_model_registry.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `python -m pytest -q`

## 2026-05-09 - Candidate OOS Reliability Artifact

Goal:

- Continue factor 31 `避免過度擬合` by saving candidate-model reliability evidence inside each walk-forward registry run, not only recalculating calibration for the currently deployed model.

Changes:

- Walk-forward model comparison now emits `candidate_calibration_gate` and `candidate_calibration_artifact` for the best OOS variant.
- The model registry promotion gate now blocks or holds a candidate when its own OOS reliability bins are overconfident, under-sampled, or missing.
- The registry UI now shows the latest candidate calibration gate, calibration points, eligible bins, blocked bins, and worst failed bins.
- Updated factor 31 coverage notes to mark candidate OOS reliability artifacts as supported while keeping temperature-strategy serialization and experiment manifests as open gaps.

Verification:

- `python -m pytest tests\test_model_registry.py tests\test_promotion_scorecard.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-09 - Pool Calibration Gate V1

Goal:

- Continue factor 30 `模型校準` by separating calibration by betting pool, so WIN, PLACE, and exotic combination tickets are not judged only by one overall win-probability reliability curve.

Changes:

- Added `pool_calibration.py` to build reliability bins from reconciled `betting_recommendations` by market.
- `evaluate_model_evolution` now exposes `pool_calibration`, and `calibration_gate` can block or discount stakes when a qualified pool market is badly miscalibrated.
- Added a model analytics UI section for `彩池校準`, showing settled ticket counts, overall calibration gap, and worst probability bin per pool.
- Updated factor 30 coverage notes to include per-pool calibration support and the remaining candidate OOS artifact / exotic-structure calibration work.

Verification:

- `python -m pytest tests\test_pool_calibration.py tests\test_calibration_gate.py tests\test_model_registry.py tests\test_betting.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-09 - Sliced Calibration Gate V1

Goal:

- Continue the 36-factor equation work by making item 30 `模型校準` less superficial: not just overall reliability bins, but track/course/distance/class/field-size/going/market-favourite slices.

Changes:

- `evaluate_model_evolution` now exports `calibration_slices` with reliability bins and worst-bin gaps per race segment.
- `calibration_gate` now blocks or discounts stakes when a qualified slice is badly miscalibrated, even if the overall bins look acceptable.
- The model analytics UI now shows the worst sliced calibration gaps beside the normal win-probability bins.
- Updated coverage notes for factor 30 to reflect sliced calibration support and the remaining WIN/PLACE/exotic-pool calibration gap.

Verification:

- `python -m pytest tests\test_calibration_gate.py tests\test_model_registry.py tests\test_betting.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-09 - Pedigree Factor Coverage V1

Goal:

- Continue the 36-factor equation work by turning the missing bloodline factor into a testable model signal.

Changes:

- Added runner-level `sire` and `dam` storage columns with schema migration support.
- HKJC declaration parsing now extracts sire and dam from the official `declaration_all.asp` rows.
- Added pedigree proxy features for related-horse distance fit, surface fit, and sparse-data debut risk.
- Prediction payloads and runner detail UI now expose the pedigree signals.
- Updated 36-factor coverage item 9 from missing to partial.

Verification:

- `python -m pytest tests\test_pedigree.py tests\test_hkjc_parser.py tests\test_coverage.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`

## 2026-05-08 - Declaration Rows Override Stale Racecard Rows

Goal:

- Make the official HKJC declaration page authoritative for current body weights when the newer racecard page returns a mismatched race.

Changes:

- Added a declaration merge guard: when racecard horse IDs and `declaration_all.asp` horse IDs do not overlap, runner completion now uses the declaration rows directly.
- Kept existing English horse names during completion when declaration rows only provide Chinese names.
- Added regression coverage for mismatched racecard/declaration horse IDs.

Verification:

- `python -m pytest tests\test_hkjc_parser.py -q`
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-08 - HKJC Declaration Body Weights

Goal:

- Use HKJC's official all-race declaration page as the primary same-day body-weight source.
- Stop relying only on racecard/results parsers or historical fallback when current declaration weights are already public.

Changes:

- Added CP950-aware fetching for legacy HKJC pages.
- Added `declaration_all.asp?RaceDate=...&RaceNo=...` support and a parser for the JavaScript `Rec[...]` declaration rows.
- Declaration parsing now extracts horse number, brand number, Chinese horse/jockey/trainer names, draw, carried weight, declared/race-day body weight, age, sex, gear, and last-six-runs where provided.
- Race-day loading and runner-completion flows now merge declaration rows into runner records, using race-day weight first and declared weight as fallback.

Verification:

- Live parser check against `https://www.hkjc.com/chinese/racing/declaration_all.asp?RaceNo=1`: 10 runners parsed for `HK20260509-ST-01`, including L245 body weight 936.
- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`

## 2026-05-08 - Ledger Stake Refresh And Full Ticket Display

Goal:

- Make the payout reconciliation panel visibly match the stake total shown in the betting header.
- Keep HK$10 as a minimum ticket amount, not a fixed ticket amount.

Changes:

- Removed the 10-ticket frontend display limit from both `派彩對數` and betting ledger lists, so all saved tickets are visible instead of only the first HK$100-looking block.
- Auto-recorded tickets now refresh `execution_stake` from the latest `recommended_stake`; only manually confirmed or already reconciled tickets keep their historical execution stake.
- Added regression coverage for auto-recorded stake refresh and full-ticket display.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_betting_ledger.py tests\test_betting.py tests\test_fast_betting_refresh.py tests\test_ui_localization.py -q`
- `python -m pytest -q`

## 2026-05-08 - Betting Header Stake Alignment

Goal:

- Make the betting header use one clear stake accounting rule.
- Remove the confusing `新建議` header metric.
- Make the displayed race cap follow the actual recommended/recorded stake total when that total is higher than the base risk cap.

Changes:

- `建議總注` now uses the payout reconciliation ticket total whenever ledger items exist; otherwise it uses the current bet slip total.
- `本場上限` now displays `max(base risk cap, 建議總注)`, so a larger valid ticket total does not appear to break the shown cap.
- Removed the `新建議` stat from the betting header.
- Added regression tests for the display-cap rule and the removed header label.
- Removed the old whole-race stake scaling pass; correlated horse/pool/combination exposure controls still apply, but the header cap follows the final ticket total when that total is larger than the base cap.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_betting.py tests\test_fast_betting_refresh.py tests\test_ui_localization.py -q`
- `python -m pytest -q`

## 2026-05-08 - Betting Stake And Horse Context Repair

Goal:

- Restore horse context visibility in the race prediction table when HKJC runner rows are missing current body weight or detailed past-performance diagnostics.
- Align the betting header's recommended stake with the payout reconciliation ledger.
- Let qualified betting and exotic-pool tickets enter the ledger at the HK$10 minimum while still scaling up with confidence.

Changes:

- Runner completion/result refresh now fills `last_six_runs` and `body_weight_lbs` when HKJC racecard/results data provides them.
- Prediction features now fall back to the latest historical body weight and use a non-leaking last-six-runs diagnostic fallback for trip luck and ability issue scores when detailed past results are unavailable.
- Eligible tickets now keep at least the market minimum ticket cost instead of being rounded down to zero, while high-confidence tickets can still size above HK$10 through Kelly and risk caps.
- Betting header `本場上限` now shows the actual race risk cap; `建議總注` follows the payout reconciliation stake total when ledger tickets exist.
- Pool/exotic tickets that clear EV, cost, source, calibration, and exposure gates are included in recorded betting tickets for payout reconciliation.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_trip_diagnostics.py tests\test_betting.py tests\test_fast_betting_refresh.py -q`
- `python -m pytest -q`

## 2026-05-08 - Adjusted Speed Figure V1

Goal:

- Start roadmap item 15 with a standardized speed feature that gives every runner a race-adjusted ability baseline before market and exotic signals.
- Avoid leaking current-race results into pre-race predictions.

Changes:

- Added `adjusted_speed_figure` to the core model feature set.
- Speed figures are computed from prior results only, using race winner time, beaten lengths converted to seconds, distance, going, course, class, weight, and recency/condition weighting.
- Prediction payloads now expose `adjusted_speed_figure` for runner inspection.
- Updated coverage item 15 to record Adjusted Speed Figure v1 support.

Verification:

- `python -m compileall -q src tests`
- `python -m pytest tests\test_speed_figure.py tests\test_smoke.py tests\test_model_compare.py tests\test_coverage.py -q`
- `node --check src\racing_model\web\app.js`
- `python -m pytest -q`
- Local API check: `/api/predictions` includes `adjusted_speed_figure` in each prediction row.

## 2026-05-08 - Live Stake Summary And Adaptive Kelly

Goal:

- Make the instant betting summary show actual placed exposure from the betting ledger, not only the newly generated recommendation total.
- Stop showing every standard race as a fixed 25% Kelly setting.

Changes:

- `/api/betting` now returns `placed_summary`, `display_max_race_stake`, and `display_total_recommended_stake` from confirmed ledger stake.
- The betting header now uses the confirmed placed stake for `本場上限` and `建議總注`, while keeping new model output under `新建議`.
- Kelly sizing now has an effective dynamic fractional Kelly based on live odds coverage and best available EV, with the original profile fraction retained as `base_fractional_kelly`.
- The UI displays the effective Kelly plus a label such as `正常`, `降注`, or `保守觀望`.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_betting.py tests\test_fast_betting_refresh.py tests\test_betting_settlement.py tests\test_ui_localization.py -q`

## 2026-05-08 - Keep Pool Prices Live Until Settlement

Goal:

- Match HKJC pool betting: placing a ticket does not lock odds/dividends; prices keep moving until the race closes and final dividends are reconciled.
- Fix exact-size Trio and First 4 candidates so they remain boxed combinations, not banker-leg structures.

Changes:

- Betting ledger refresh now keeps confirmed tickets' stake/confirmed state but updates their indicative odds from the latest non-final pool tick or probable dividend.
- Final dividends/results no longer overwrite the live indicative ticket price; they are used only by settlement reconciliation.
- UI labels changed from `下注時` to `現時估算` where the value is still moving with the pool.
- Exact 3-runner `TRIO` and exact 4-runner `FIRST4` candidates are forced to `複式` with no banker.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_betting.py tests\test_betting_ledger.py tests\test_ui_localization.py -q`
- `python -m pytest -q`
- Local API check on `http://127.0.0.1:8766/`: betting ledger note states pool prices are not locked; TRIO/FIRST4 candidates do not show bankers when no value/edge structure is active.

## 2026-05-08 - Race Pace Simulation MVP

Goal:

- Start item 27 of the 36-item model roadmap: simulate race pace, projected position, and traffic risk before wiring deeper replay calibration.
- Surface the pace signal in predictions and exotic betting candidates without changing the core probability model yet.

Changes:

- Added `src/racing_model/pace.py` with a deterministic pace map for early-speed score, projected position, pace role, traffic risk, wide risk, finishing kick, and pace advantage.
- Prediction payloads now include pace fields and a race-level `pace_map`.
- Betting payloads now annotate WIN/PLACE decisions and exotic candidates with pace notes, pace fit, ordered-combination fit, risk score, and race shape.
- The betting UI now shows pace notes plus `節奏吻合` / `節奏風險` for exotic candidates.
- Updated the coverage report so item 27 `賽事節奏模擬` is now partial instead of missing.

Verification:

- `python -m compileall -q src tests`
- `node --check src\racing_model\web\app.js`
- `python -m pytest tests\test_pace_simulation.py tests\test_betting.py tests\test_coverage.py tests\test_ui_localization.py -q`
- `python -m pytest -q`
- Local API check on `http://127.0.0.1:8766/`: `/api/predictions` returned `pace_map`; `/api/betting` returned exotic `pace_note` and `pace_fit_score`.

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

## 2026-05-08 - Promotion Scorecard v1

Goal:

- Develop the next model-system indicator: a unified upgrade scorecard for deciding whether a candidate model is safe to promote.
- Make the scorecard visible in analytics without rerunning expensive walk-forward work twice.

Implementation:

- Added `promotion_scorecard.py`, combining walk-forward Top1/Top3, log loss, Brier, calibration, ROI, execution ROI, max drawdown, Sharpe-like return-to-drawdown, pool replay, and slice OOS gates.
- Added `/api/promotion-scorecard` and embedded `promotion_scorecard` inside `/api/analytics-dashboard` by reusing the existing `model_versions` and `pool_replay` payloads.
- Added an analytics UI panel showing the overall promotion gate, section gates, key metric deltas, and the highest-priority slice scorecard rows.
- Updated the 36-indicator coverage report for item 18 and item 35 to reflect Promotion Scorecard v1.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`
- Local smoke test against `/api/promotion-scorecard` and `/api/analytics-dashboard?include_coverage=0`

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

## 2026-05-08 - Trip Attribution And Horse Context Signals

Goal:

- Strengthen the 36-indicator model coverage with horse-level context beyond finishing position.
- Separate recent-form outcomes into trip/luck problems, ability concerns, closing strength, pace fade, distance-fit signals, body-weight movement, health stability, gear changes, and opponent strength.

Implementation:

- Added `trip_diagnostics.py` to analyse the last six runs using HKJC running positions, result comments, body weight, gear, class, prize money, and field strength.
- Stored declared body weight on runners and along-the-run positions on results, with database migration support.
- Extended HKJC racecard/result parsing so imported rows carry body weight and route position sequences such as `7/7/5/1`.
- Added new model features for body-weight change/trend, health, gear changes, trip luck, ability issue, closing gain, pace fade, distance stretch suitability, and opponent strength.
- Exposed the new signals in prediction payloads, runner detail, prediction table horse context, result API rows, and the 36-indicator coverage report.
- Added regression tests for parser extraction, trip attribution, model payload fields, UI labels, and coverage mapping.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`

## 2026-05-08 - Pace Profile And Race Shape V2

Goal:

- Turn the previous post-race trip attribution into pre-race pace and race-shape signals.
- Cover indicator 8, indicator 27, and the class/rating part of indicator 11 with model-ready features and betting candidate sorting.

Implementation:

- Added `pace_profile.py` to analyse the last six runs for early speed, midrace move, turn position, late gain, front-run fade, traffic history, distance-pace fit, hidden ability, class change, and rating change.
- Added race-level pace projection features for projected position, traffic risk, pace advantage, and race-shape pressure.
- Extended the feature matrix, prediction payload, runner detail UI, prediction table, and exotic candidate cards with the new pace/class signals.
- Upgraded `pace.py` so the betting pace map uses historical pace profiles in addition to running style, draw, market/model probability, same-day bias, and trip diagnostics.
- Changed exotic candidate ordering and pool-choice scoring to use pace-adjusted probability, pace fit, pace edge, and pace risk.
- Updated the 36-indicator coverage report for pace/race-shape, class-rating change, core feature coverage, and simulation blind spots.

Verification:

- `python -m compileall -q src dashboard tests`
- `node --check src/racing_model/web/app.js`
- `python -m pytest tests`
