# Development Log

This file records cross-device Codex handoffs, audits, fixes, pushes, and server deployments.

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
