from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import racing_model.live as live_module
import racing_model.storage as storage_module
from datetime import datetime

from racing_model.live import HKJCRaceRef, before_hkjc_result_window, enrich_runners_with_horse_profiles, is_future_hkjc_race_date, refresh_hkjc_results_if_available
from racing_model.model import RankingModel
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


def test_past_race_with_official_results_stays_resulted_when_runner_scratched(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-03",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Scratched Runner Test",
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                {
                    "race_id": "HK20260506-ST-03",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "Winner",
                    "jockey": "Jockey",
                    "trainer": "Trainer",
                    "draw": 1,
                    "weight_lbs": 120,
                    "official_rating": 50,
                    "age": 4,
                    "sex": "G",
                    "running_style": "pace",
                    "gear": "",
                },
                {
                    "race_id": "HK20260506-ST-03",
                    "horse_id": "H002",
                    "horse_no": 2,
                    "horse_name": "Scratched",
                    "jockey": "Jockey",
                    "trainer": "Trainer",
                    "draw": 2,
                    "weight_lbs": 119,
                    "official_rating": 48,
                    "age": 4,
                    "sex": "G",
                    "running_style": "closer",
                    "gear": "",
                },
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                {
                    "race_id": "HK20260506-ST-03",
                    "horse_id": "H001",
                    "finish_position": 1,
                    "finish_time_sec": 70.0,
                    "margin_lengths": 0.0,
                }
            ],
        )
        conn.commit()
        refresh_race_statuses(conn)
        status = race_status(conn, "HK20260506-ST-03")

    assert status["status"] == "resulted"


def test_refresh_race_statuses_skips_unchanged_rows(tmp_path: Path) -> None:
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
                    "race_name": "Noop Status Test",
                }
            ],
        )
        refresh_race_statuses(conn)
        changes_after_insert = conn.total_changes
        refresh_race_statuses(conn)
        changes_after_noop = conn.total_changes

    assert changes_after_noop == changes_after_insert


def test_future_hkjc_race_date_accepts_hkjc_formats() -> None:
    assert is_future_hkjc_race_date("2099/01/01")
    assert is_future_hkjc_race_date("2099-01-01")


def test_same_day_sha_tin_first_race_is_not_resulted_before_post_time() -> None:
    ref = HKJCRaceRef("2026/05/09", "ST", 1)
    early = datetime(2026, 5, 9, 1, 30, tzinfo=live_module.hong_kong_tz())
    after_post = datetime(2026, 5, 9, 12, 36, tzinfo=live_module.hong_kong_tz())

    assert before_hkjc_result_window(ref, early) is True
    assert before_hkjc_result_window(ref, after_post) is False


def test_refresh_results_does_not_fetch_hkjc_before_result_window(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)

    def fail_source(*args, **kwargs):
        raise AssertionError("HKJC results should not be fetched before scheduled result window")

    monkeypatch.setattr(live_module, "before_hkjc_result_window", lambda ref: True)
    monkeypatch.setattr(live_module, "HKJCSource", fail_source)
    with connect(db_path) as conn:
        result = refresh_hkjc_results_if_available(
            conn,
            "HK20260509-ST-01",
            RankingModel.new(),
            "test-agent",
            0.0,
        )
        status = race_status(conn, "HK20260509-ST-01")

    assert result["status"] == "scheduled"
    assert status["status"] == "scheduled"
    assert status["notes"] == "race_not_due_for_official_results"


def test_runner_enrichment_uses_hkjc_horse_profile_history() -> None:
    class Source:
        def fetch_horse_profile_page(self, horse_id: str):
            assert horse_id == "J488"
            return type("Fetch", (), {"body": "profile"})()

        def parse_horse_profile(self, html: str):
            assert html == "profile"
            return {"last_six_runs": "WV-A/12/4/1/4/8"}

    rows = enrich_runners_with_horse_profiles(
        Source(),  # type: ignore[arg-type]
        [{"race_id": "HK20260509-ST-02", "horse_id": "J488", "last_six_runs": "12/4/1/4/8/6"}],
    )

    assert rows[0]["last_six_runs"] == "WV-A/12/4/1/4/8"


def test_hong_kong_timezone_falls_back_without_tzdata(monkeypatch) -> None:
    def missing_zoneinfo(name: str):
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(storage_module, "ZoneInfo", missing_zoneinfo)
    monkeypatch.setattr(live_module, "ZoneInfo", missing_zoneinfo)

    assert storage_module.is_future_race_date("2099-01-01")
    assert live_module.is_future_hkjc_race_date("2099/01/01")
