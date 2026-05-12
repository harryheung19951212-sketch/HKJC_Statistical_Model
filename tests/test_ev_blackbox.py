from pathlib import Path

from racing_model.ev_blackbox import eligible_resulted_race_ids, run_ev_blackbox_training
from racing_model.storage import connect, init_db, insert_rows


def test_ev_blackbox_training_writes_holdout_report(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        seed_ev_training_rows(conn)
        report = run_ev_blackbox_training(
            conn,
            output_dir=tmp_path,
            holdout_date="2026-05-09",
            trials=3,
            seed=7,
            min_epochs=2,
            max_epochs=3,
            min_validation_races=1,
        )

    with connect(db_path) as conn:
        assert len(eligible_resulted_race_ids(conn)) == 4
    assert report["sample"]["holdout_races"] == 1
    assert report["sample"]["train_races"] >= 1
    assert report["guardrails"]["holdout_used_for_selection"] is False
    assert report["best_candidate"]["policy"]["min_win_ev"] >= 0
    assert report["holdout_win_replay"]["summary"]["race_count"] == 1
    assert Path(report["model_path"]).exists()
    assert Path(report["report_path"]).exists()


def seed_ev_training_rows(conn) -> None:
    races = []
    runners = []
    results = []
    odds = []
    for race_index, race_date in enumerate(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-09"], start=1):
        race_id = f"R{race_index}"
        races.append(
            {
                "race_id": race_id,
                "date": race_date,
                "track": "Sha Tin",
                "course": "Turf",
                "distance_m": 1200,
                "going": "Good",
                "class_rating": "Class 4",
                "prize": 1000000,
                "race_name": f"Race {race_index}",
            }
        )
        for horse_no in range(1, 4):
            horse_id = f"H{horse_no}"
            runners.append(
                {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "horse_no": horse_no,
                    "horse_name": f"Horse {horse_no}",
                    "horse_name_zh": f"馬{horse_no}",
                    "last_six_runs": "123456",
                    "jockey": "Jockey",
                    "jockey_zh": "騎師",
                    "trainer": "Trainer",
                    "trainer_zh": "練馬師",
                    "draw": horse_no,
                    "weight_lbs": 118 + horse_no,
                    "body_weight_lbs": 1000 + horse_no,
                    "official_rating": 70 - horse_no,
                    "age": 4,
                    "sex": "G",
                    "running_style": "midpack",
                    "gear": "",
                }
            )
            finish = horse_no
            results.append(
                {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "finish_position": finish,
                    "finish_time_sec": 70 + finish,
                    "margin_lengths": finish - 1,
                }
            )
            odds.append(
                {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "timestamp": f"{race_date}T08:00:00+08:00",
                    "win_odds": 2.0 + horse_no,
                    "place_odds": 1.1 + horse_no * 0.1,
                    "source": "hkjc_results_final",
                }
            )
    insert_rows(conn, "races", races)
    insert_rows(conn, "runners", runners)
    insert_rows(conn, "results", results)
    insert_rows(conn, "odds_ticks", odds)
    conn.commit()
