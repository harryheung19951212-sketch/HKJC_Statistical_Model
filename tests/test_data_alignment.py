from pathlib import Path

from racing_model.backfill import align_resulted_race_data, data_quality_report
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


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


def test_align_resulted_race_data_leaves_unfinished_races_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20990101-ST-01",
                    "date": "2099-01-01",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Future",
                },
                {
                    "race_id": "HK20260501-ST-01",
                    "date": "2026-05-01",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Known",
                },
            ],
        )
        defaults = {
            "last_six_runs": "",
            "draw": 1,
            "weight_lbs": 120,
            "official_rating": 60,
            "age": 4,
            "sex": "G",
            "running_style": "midpack",
            "jockey": "J Doe",
            "trainer": "T Stable",
        }
        insert_rows(
            conn,
            "runners",
            [
                {
                    **defaults,
                    "race_id": "HK20260501-ST-01",
                    "horse_id": "H1",
                    "horse_no": 1,
                    "horse_name": "KNOWN",
                    "horse_name_zh": "識跑馬",
                    "jockey_zh": "杜騎師",
                    "trainer_zh": "田練馬",
                },
                {
                    **defaults,
                    "race_id": "HK20990101-ST-01",
                    "horse_id": "H1",
                    "horse_no": 1,
                    "horse_name": "KNOWN",
                    "horse_name_zh": "",
                    "jockey_zh": "",
                    "trainer_zh": "",
                },
                {
                    **defaults,
                    "race_id": "HK20990101-ST-01",
                    "horse_id": "H2",
                    "horse_no": 2,
                    "horse_name": "FUTURE STARTER",
                    "horse_name_zh": "",
                    "jockey_zh": "",
                    "trainer_zh": "",
                },
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                {
                    "race_id": "HK20990101-ST-01",
                    "horse_id": "H1",
                    "finish_position": 1,
                    "finish_time_sec": 70.0,
                    "margin_lengths": 0,
                }
            ],
        )
        upsert_race_status(conn, "HK20990101-ST-01", "scheduled")
        conn.commit()

        alignment = align_resulted_race_data(conn)
        rows = conn.execute("SELECT horse_id, horse_name_zh FROM runners WHERE race_id = 'HK20990101-ST-01' ORDER BY horse_id").fetchall()

    assert alignment["pruned_unmatched_resulted_runners"] == 0
    assert alignment["filled_horse_chinese_names"] == 0
    assert [(row["horse_id"], row["horse_name_zh"]) for row in rows] == [("H1", ""), ("H2", "")]


def test_data_quality_ignores_scheduled_and_void_missing_results(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20990101-ST-01",
                    "date": "2099-01-01",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Scheduled",
                },
                {
                    "race_id": "HK20251115-ST-08",
                    "date": "2025-11-15",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "VOID",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Void",
                },
            ],
        )
        upsert_race_status(conn, "HK20990101-ST-01", "scheduled")
        upsert_race_status(conn, "HK20251115-ST-08", "resulted", notes="official_void_race")
        conn.commit()

        quality = data_quality_report(conn)

    issue_counts = {issue["id"]: issue["count"] for issue in quality["issues"]}
    assert issue_counts["missing_results"] == 0
    assert issue_counts["missing_odds"] == 0
