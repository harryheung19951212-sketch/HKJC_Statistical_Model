from pathlib import Path

from racing_model.backfill import data_quality_report, iter_dates
from racing_model.features import build_race_features, build_training_races
from racing_model.evolution import evaluate_model_evolution
from racing_model.model import RankingModel
from racing_model.storage import connect, import_csv, init_db
from racing_model.walk_forward import run_walk_forward_versions


def test_sample_pipeline(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    sample_dir = Path("data/sample")
    with connect(db_path) as conn:
        for table, filename in {
            "races": "races.csv",
            "runners": "runners.csv",
            "results": "results.csv",
            "workouts": "workouts.csv",
            "odds_ticks": "odds.csv",
        }.items():
            import_csv(conn, table, sample_dir / filename)
        conn.commit()

        races = build_training_races(conn)
        model = RankingModel.new()
        model.fit(races, epochs=5)
        predictions = model.predict_race(build_race_features(conn, "R20260506-S1"))
        evolution = evaluate_model_evolution(conn, model)
        walk_forward = run_walk_forward_versions(conn, epochs=2)
        quality = data_quality_report(conn)

    assert predictions
    assert abs(sum(float(row["win_probability"]) for row in predictions) - 1.0) < 0.00001
    assert all("top3_probability" in row for row in predictions)
    assert all(row["top3_model_source"] == "independent_top3_model" for row in predictions)
    assert abs(sum(float(row["top3_probability"]) for row in predictions) - min(3, len(predictions))) < 0.00001
    assert evolution["metrics"]["races"] == 2
    assert evolution["calibration"]
    assert evolution["ideas"]
    assert walk_forward["summary"]["folds"] == 1
    assert walk_forward["versions"]
    assert quality["totals"]["races"] == 2
    assert quality["quality_score"] > 0
    assert iter_dates("2026/05/06", "2026-05-07") == ["2026/05/06", "2026/05/07"]
