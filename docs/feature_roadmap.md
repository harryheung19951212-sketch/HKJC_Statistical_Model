# Feature Roadmap

This maps the 21 major racing-model factor groups into concrete data features.

## v1 Implemented

1. Race conditions
   - Track, course, distance, going, class, prize.
2. Horse ability
   - Official rating, recent speed, recent form.
3. Recent condition
   - Workout score from latest workout/trial rows.
4. Jockey
   - Historical win rate from imported results.
5. Trainer
   - Historical win rate from imported results.
6. Weight
   - Actual carried pounds.
7. Draw
   - Inside/outside draw flags.
8. Pace
   - Basic leader/pace/closer pressure estimate.
9. Gear
   - Stored in runner data, ready for feature expansion.
10. Market
   - Latest win odds, implied probability, expected value.
11. Backtesting
   - Positive-EV fixed-stake strategy.
12. GPT analysis
   - Codex CLI report layer over model output.
13. Model version center
   - Walk-forward comparison for baseline, no-market, market-only, and conservative calibrated versions.
   - Version ranking by out-of-sample log loss, Brier score, top-pick hit rate, and value ROI.
14. Historical backfill center
   - Batch HKJC date-range imports.
   - Progress tracking, automatic retraining, walk-forward refresh, and data quality checks.
15. Data repair center
   - Repair orphan results and odds by creating conservative runner placeholders from result rows.
   - Retrain the baseline model and refresh walk-forward comparisons after repair.

## v2 Data Adapters

1. HKJC race card adapter
   - Parse current runners, jockeys, trainers, weight, draw, gear.
2. HKJC results adapter
   - Parse finish positions, times, margins, sectionals, comments.
3. Trackwork and trials adapter
   - Parse trackwork summaries and barrier trial tables.
4. Odds snapshot adapter
   - Capture time-stamped odds movement.
5. Weather and going adapter
   - Store rainfall, temperature, humidity, wind, and official going changes.

## v3 Model Improvements

1. Speed figures
   - Track/day variant correction.
   - Distance normalization.
   - Weight-to-length adjustment.
2. Trip notes
   - Slow start, blocked run, wide trip, checked, eased, strong close.
3. Ranking model
   - Replace baseline with LightGBM LambdaRank/CatBoost ranking.
4. Probability calibration
   - Brier score, log loss, reliability curves.
   - Promotion gates based on walk-forward results.
5. Exotic bet simulation
   - Quinella, quinella place, exacta, trio, tierce, first four.
6. Bankroll analytics
   - Flat stake, fractional Kelly, drawdown, exposure caps.

## v4 Human-in-the-loop Signals

1. Morning trackwork visual grades.
2. Trial video notes.
3. Paddock condition notes.
4. Vet-report severity labels.
5. Trainer-intent/news summaries.

## Guardrails

- No automatic betting in this project version.
- All data access must respect source terms, robots policies, account rules, and rate limits.
- Raw snapshots are saved for auditability.
- GPT output is explanatory and advisory; numerical probabilities come from the model layer.
