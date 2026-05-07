from pathlib import Path

from racing_model.app_server import api_results
from racing_model.model import RankingModel
from racing_model.storage import connect, fetch_all, final_place_snapshot_rows, init_db, insert_rows


def test_final_place_snapshot_preserves_all_runner_place_odds(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
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
            ],
        )
        insert_rows(
            conn,
            "runners",
            [runner("H001", 1), runner("H002", 2), runner("H003", 3), runner("H004", 4)],
        )
        insert_rows(
            conn,
            "results",
            [result("H001", 1), result("H002", 2), result("H003", 3), result("H004", 4)],
        )
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", 3.0, 1.3, "hkjc_mqtt"),
                odds("H002", 5.0, 1.8, "hkjc_mqtt"),
                odds("H003", 8.0, 2.2, "hkjc_mqtt"),
                odds("H004", 20.0, 5.5, "hkjc_mqtt"),
                odds("H001", 3.0, 1.3, "hkjc_results_final"),
                odds("H002", 5.0, 1.8, "hkjc_results_final"),
                odds("H003", 8.0, 2.2, "hkjc_results_final"),
            ],
        )
        conn.commit()

        rows = final_place_snapshot_rows(conn, "HK20260506-ST-01", "2026-05-06T12:05:00+08:00")
        report = api_results(conn, RankingModel.new(), "HK20260506-ST-01")
        api_results(conn, RankingModel.new(), "HK20260506-ST-01")
        inserted = fetch_all(
            conn,
            """
            SELECT count(*) AS n
            FROM odds_ticks
            WHERE race_id = ?
              AND source = 'hkjc_final_place_snapshot'
            """,
            ("HK20260506-ST-01",),
        )[0]["n"]

    assert inserted == 1
    assert [row["horse_id"] for row in rows] == ["H004"]
    result_by_horse = {row["horse_id"]: row for row in report["results"]}
    assert result_by_horse["H004"]["final_place_odds"] == 5.5
    assert result_by_horse["H004"]["final_place_odds_source"] == "hkjc_final_place_snapshot"
    assert result_by_horse["H004"]["top3_expected_value"] is not None


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


def odds(horse_id: str, win_odds: float, place_odds: float, source: str) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "timestamp": "2026-05-06T12:00:00+08:00",
        "win_odds": win_odds,
        "place_odds": place_odds,
        "source": source,
    }
