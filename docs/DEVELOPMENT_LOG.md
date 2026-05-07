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

