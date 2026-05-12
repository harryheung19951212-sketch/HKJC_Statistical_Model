from pathlib import Path

from racing_model.backfill import align_resulted_race_data, data_quality_report
from racing_model.storage import connect, init_db, insert_rows


def test_align_resulted_race_data_prunes_non_starters_and_reuses_chinese_names(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "R1",
                    "date": "2026-05-09",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Test",
                },
                {
                    "race_id": "R0",
                    "date": "2026-05-01",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Known names",
                },
            ],
        )
        runner_defaults = {
            "last_six_runs": "",
            "draw": 1,
            "weight_lbs": 120,
            "official_rating": 60,
            "age": 4,
            "sex": "G",
            "running_style": "midpack",
        }
        insert_rows(
            conn,
            "runners",
            [
                {
                    **runner_defaults,
                    "race_id": "R0",
                    "horse_id": "H1",
                    "horse_no": 1,
                    "horse_name": "KNOWN HORSE",
                    "horse_name_zh": "識跑馬",
                    "jockey": "J Doe",
                    "jockey_zh": "杜騎師",
                    "trainer": "T Stable",
                    "trainer_zh": "田練馬",
                },
                {
                    **runner_defaults,
                    "race_id": "R1",
                    "horse_id": "H1",
                    "horse_no": 1,
                    "horse_name": "KNOWN HORSE",
                    "horse_name_zh": "",
                    "jockey": "J Doe",
                    "jockey_zh": "",
                    "trainer": "T Stable",
                    "trainer_zh": "",
                },
                {
                    **runner_defaults,
                    "race_id": "R1",
                    "horse_id": "H2",
                    "horse_no": 2,
                    "horse_name": "WRONG STARTER",
                    "horse_name_zh": "",
                    "jockey": "X Rider",
                    "jockey_zh": "",
                    "trainer": "X Trainer",
                    "trainer_zh": "",
                },
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                {
                    "race_id": "R1",
                    "horse_id": "H1",
                    "finish_position": 1,
                    "finish_time_sec": 70.0,
                    "margin_lengths": 0,
                }
            ],
        )
        insert_rows(
            conn,
            "odds_ticks",
            [
                {"race_id": "R1", "horse_id": "H1", "timestamp": "2026-05-09T12:00:00Z", "win_odds": 2.0, "place_odds": 1.1, "source": "test"},
                {"race_id": "R1", "horse_id": "H2", "timestamp": "2026-05-09T12:00:00Z", "win_odds": 9.9, "place_odds": 2.8, "source": "test"},
            ],
        )
        before = data_quality_report(conn)
        alignment = align_resulted_race_data(conn)
        after = data_quality_report(conn)

        rows = conn.execute("SELECT * FROM runners WHERE race_id = 'R1' ORDER BY horse_id").fetchall()
        odds = conn.execute("SELECT * FROM odds_ticks WHERE race_id = 'R1' ORDER BY horse_id").fetchall()

    assert before["quality_score"] < after["quality_score"]
    assert alignment["pruned_unmatched_resulted_runners"] == 1
    assert alignment["deleted_unmatched_odds_ticks"] == 1
    assert alignment["filled_horse_chinese_names"] == 1
    assert alignment["filled_jockey_chinese_names"] == 1
    assert alignment["filled_trainer_chinese_names"] == 1
    assert [row["horse_id"] for row in rows] == ["H1"]
    assert rows[0]["horse_name_zh"] == "識跑馬"
    assert rows[0]["jockey_zh"] == "杜騎師"
    assert rows[0]["trainer_zh"] == "田練馬"
    assert [row["horse_id"] for row in odds] == ["H1"]
