# Racing Model

Horse-racing statistical model and local decision console for Hong Kong racing.
The goal is not a general information portal; the goal is a transparent racing
equation that can predict probabilities, compare them against HKJC markets,
select value bets, backtest every decision, and improve only when validation
evidence supports the change.

For handoff to another Codex session, read:

- `docs/HANDOFF_CONTEXT.md`
- `docs/RACING_EQUATION_ROADMAP.md`
- `docs/feature_roadmap.md`

## What This Version Does

- Reads race, runner, result, workout, and odds CSV files into SQLite.
- Builds race-level features for every horse.
- Trains a pure-Python pairwise logistic ranking model.
- Predicts win probability, implied market probability, and value gap.
- Backtests simple positive-EV betting strategies.
- Generates Codex CLI-assisted model iteration notes without requiring an OpenAI API key.
- Compares model versions with walk-forward validation.
- Supports a Chinese local web console with race navigation, lifecycle control, predictions, results, odds history, and weather.
- Preserves final live odds for resulted or in-running races.
- Calculates win/place fair odds, edge, expected value, fractional Kelly, and risk-capped stake suggestions.
- Builds exotic-pool candidates for quinella, quinella place, exacta, trio, tierce, first four, and quartet.
- Includes a polite scraper framework for public pages, with rate limiting and raw snapshot storage.

This is deliberately built as a transparent MVP. It avoids automatic betting and keeps data access configurable so you can respect each data provider's terms.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Load sample data:

```powershell
racing-model init-db
racing-model import-sample
```

Train and predict:

```powershell
racing-model train --model-path models/baseline.json
racing-model predict --race-id R20260506-S1 --model-path models/baseline.json
```

Backtest:

```powershell
racing-model backtest --model-path models/baseline.json
```

Walk-forward model version comparison:

```powershell
racing-model walk-forward --epochs 80
```

Historical HKJC backfill and data quality checks:

```powershell
racing-model backfill-hkjc --start 2026/05/06 --end 2026/05/06 --venue ST --races 10
racing-model data-quality
racing-model repair-data
```

Optional GPT report through API key:

```powershell
racing-model gpt-report --race-id R20260506-S1 --model-path models/baseline.json
```

Dashboard:

```powershell
racing-model export-html --race-id R20260506-S1 --model-path models/baseline.json --out reports/race_R20260506-S1.html
```

The static HTML report can be opened directly in your browser. If you want the optional Streamlit dashboard:

```powershell
pip install -e ".[dashboard]"
streamlit run dashboard/app.py
```

Local app console, with race navigation and 30-second odds refresh:

```powershell
python -m racing_model.cli serve --port 8765 --model-path models/baseline.json --odds-interval 30
```

Open `http://127.0.0.1:8765`.

Codex CLI model-iteration mode can run without an OpenAI API key when local
Codex CLI is installed and configured:

```powershell
RACING_CODEX_CLI=codex
RACING_CODEX_MODEL=gpt-5.5
RACING_CODEX_REASONING_EFFORT=high
RACING_CODEX_TIMEOUT_SECONDS=180
```

Odds provider mode:

```powershell
# auto: try HKJC GraphQL first, then fallback to development snapshots
RACING_ODDS_PROVIDER=auto

# hkjc: require HKJC GraphQL to work, fail if blocked
RACING_ODDS_PROVIDER=hkjc

# mqtt: use HKJC no-login MQTT push/recovery feed
RACING_ODDS_PROVIDER=mqtt

# dev: use local development odds movement
RACING_ODDS_PROVIDER=dev
```

The `auto` mode tries HKJC GraphQL first, then HKJC MQTT push/recovery,
then development snapshots. The console displays the active odds source in
each race header.

Optional integrations:

```powershell
pip install -e ".[gpt]"
```

HKJC public-page snapshots use the Python standard library, so no scraper extra is required.

## HKJC Snapshot Fetch

Fetch and parse a racecard snapshot into CSV files:

```powershell
python -m racing_model.cli fetch-hkjc racecard --date 2026/05/06 --venue ST --race-no 1 --out-dir data/hkjc/20260506_ST_R01
python -m racing_model.cli import-csv --dir data/hkjc/20260506_ST_R01
```

Results and trackwork use the same shape:

```powershell
python -m racing_model.cli fetch-hkjc results --date 2026/05/06 --venue ST --race-no 1 --out-dir data/hkjc/20260506_ST_R01
python -m racing_model.cli fetch-hkjc trackwork --date 2026/05/06 --venue ST --race-no 1 --out-dir data/hkjc/20260506_ST_R01
```

Use low frequency and keep the saved `data/raw/` snapshots for auditing. Do not automate betting.

## CSV Inputs

Place CSV files under `data/import/` or pass paths to `racing-model import-csv`.

Expected columns:

- `races.csv`: `race_id,date,track,course,distance_m,going,class_rating,prize`
- `runners.csv`: `race_id,horse_id,horse_name,jockey,trainer,draw,weight_lbs,official_rating,age,sex,running_style,gear`
- `results.csv`: `race_id,horse_id,finish_position,finish_time_sec,margin_lengths,sectional_400_sec,sectional_800_sec,comment`
- `workouts.csv`: `horse_id,date,track,work_type,distance_m,time_sec,rank,notes`
- `odds.csv`: `race_id,horse_id,timestamp,win_odds,place_odds,source`

## Architecture

- `src/racing_model/storage.py`: SQLite schema and repositories.
- `src/racing_model/features.py`: feature engineering.
- `src/racing_model/model.py`: pairwise ranking model.
- `src/racing_model/backtest.py`: walk-forward style strategy tests.
- `src/racing_model/gpt_report.py`: OpenAI Responses API report layer.
- `src/racing_model/scrapers/`: data-source adapters and polite HTTP client.
- `src/racing_model/html_report.py`: dependency-free static report exporter.
- `src/racing_model/app_server.py`: dependency-free local web app and JSON API.
- `src/racing_model/odds.py`: odds refresh provider interface and development provider.
- `dashboard/app.py`: Streamlit dashboard.

## Next Development Steps

1. Build `/api/coverage` and a UI panel for the 21 core factor groups plus the extra blind spots in `docs/RACING_EQUATION_ROADMAP.md`.
2. Add official/probable exotic-pool dividend ingestion so exotic candidates can become real EV decisions.
3. Add late market-flow features from final 5 minutes, 2 minutes, and 30 seconds.
4. Add same-day track-bias learning after each completed race.
5. Add error taxonomy and model promotion gates so Codex CLI iterations only advance when walk-forward results improve.
