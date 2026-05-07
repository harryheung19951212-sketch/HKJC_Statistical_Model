from pathlib import Path

from racing_model.feed_health import odds_feed_health
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


def test_odds_feed_health_reports_missing_place_ticks(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(conn, "races", [race()])
        insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2), runner("H003", 3)])
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", 3.0, 1.3, "hkjc_graphql", "2099-01-01T12:00:00+08:00"),
                odds("H002", 5.5, None, "hkjc_graphql", "2099-01-01T12:00:00+08:00"),
                odds("H003", 8.0, 2.4, "dev_snapshot_jitter"),
            ],
        )
        upsert_race_status(conn, "HK20260506-ST-01", "scheduled")
        conn.commit()

        report = odds_feed_health(conn, "HK20260506-ST-01", 30)

    assert report["status"] == "partial"
    assert report["runner_count"] == 3
    assert report["win_covered"] == 2
    assert report["place_covered"] == 1
    by_horse = {row["horse_id"]: row for row in report["runners"]}
    assert by_horse["H002"]["missing_place_tick"] is True
    assert by_horse["H003"]["missing_win_tick"] is True
    assert report["source_counts"] == {"hkjc_graphql": 2}


def test_odds_feed_health_marks_resulted_race_training_ready(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(conn, "races", [race()])
        insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2)])
        insert_rows(conn, "results", [result("H001", 1), result("H002", 2)])
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", 3.0, 1.3, "hkjc_final_place_snapshot"),
                odds("H002", 5.5, 1.8, "hkjc_final_place_snapshot"),
            ],
        )
        upsert_race_status(conn, "HK20260506-ST-01", "resulted")
        conn.commit()

        report = odds_feed_health(conn, "HK20260506-ST-01", 30)

    assert report["status"] == "training_ready"
    assert report["training_ready"] is True
    assert report["place_odds_completeness"]["complete"] is True


def race() -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "date": "2026-05-06",
        "track": "Sha Tin",
        "course": "Turf",
        "distance_m": 1200,
        "going": "Good",
        "class_rating": "Class 4",
        "prize": 1000000,
        "race_name": "Test",
    }


def runner(horse_id: str, horse_no: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": f"Horse {horse_no}",
        "horse_name_zh": f"馬{horse_no}",
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": 120,
        "official_rating": 50,
        "age": 4,
        "sex": "G",
        "running_style": "unknown",
        "gear": "",
    }


def result(horse_id: str, position: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 70 + position,
        "margin_lengths": position - 1,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }


def odds(
    horse_id: str,
    win_odds: float,
    place_odds: float | None,
    source: str,
    timestamp: str = "2026-05-06T12:00:00+08:00",
) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "timestamp": timestamp,
        "win_odds": win_odds,
        "place_odds": place_odds,
        "source": source,
    }
