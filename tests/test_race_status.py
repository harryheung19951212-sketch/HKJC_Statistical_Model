from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import racing_model.live as live_module
import racing_model.storage as storage_module
from racing_model.live import is_future_hkjc_race_date
from racing_model.storage import connect, init_db, insert_rows, race_status, refresh_race_statuses


def test_future_race_date_stays_scheduled_even_if_bad_results_exist(tmp_path: Path) -> None:
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
                    "race_name": "Future Test",
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                {
                    "race_id": "HK20990101-ST-01",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "Future Horse",
                    "jockey": "Jockey",
                    "trainer": "Trainer",
                    "draw": 1,
                    "weight_lbs": 120,
                    "official_rating": 50,
                    "age": 4,
                    "sex": "G",
                    "running_style": "pace",
                    "gear": "",
                }
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                {
                    "race_id": "HK20990101-ST-01",
                    "horse_id": "H001",
                    "finish_position": 1,
                    "finish_time_sec": 70.0,
                    "margin_lengths": 0.0,
                }
            ],
        )
        conn.commit()
        refresh_race_statuses(conn)
        status = race_status(conn, "HK20990101-ST-01")

    assert status["status"] == "scheduled"


def test_future_hkjc_race_date_accepts_hkjc_formats() -> None:
    assert is_future_hkjc_race_date("2099/01/01")
    assert is_future_hkjc_race_date("2099-01-01")


def test_hong_kong_timezone_falls_back_without_tzdata(monkeypatch) -> None:
    def missing_zoneinfo(name: str):
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(storage_module, "ZoneInfo", missing_zoneinfo)
    monkeypatch.setattr(live_module, "ZoneInfo", missing_zoneinfo)

    assert storage_module.is_future_race_date("2099-01-01")
    assert live_module.is_future_hkjc_race_date("2099/01/01")
