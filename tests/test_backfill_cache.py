from pathlib import Path

from racing_model.backfill import black_box_fake_ticket_replay, load_hkjc_date_range
from racing_model.features import FEATURE_NAMES, RunnerFeatures
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


def test_backfill_skips_completed_meeting_from_database(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "date": "2026/05/06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "Cached Race",
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                runner("HK20260506-ST-01", "H001", 1),
                runner("HK20260506-ST-01", "H002", 2),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("HK20260506-ST-01", "H001", 1),
                result("HK20260506-ST-01", "H002", 2),
            ],
        )
        upsert_race_status(conn, "HK20260506-ST-01", "resulted")
        conn.commit()

        def fail_loader(*args, **kwargs):
            raise AssertionError("completed historical races must not be crawled again")

        monkeypatch.setattr("racing_model.backfill.load_hkjc_race_day", fail_loader)

        payload = load_hkjc_date_range(conn, "2026-05-06", "2026-05-06", "ST", 1, "test-agent", 0.0)

    assert payload["skipped_completed"] == 1
    assert payload["skipped_days"] == 1
    assert payload["day_results"][0]["skip_reason"] == "database_completed"


def test_backfill_skips_date_without_matching_hkjc_meeting(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        class EmptySource:
            def __init__(self, _client):
                pass

            def fetch_racecard_page(self, *_args):
                return type("Response", (), {"body": ""})()

            def parse_racecard(self, *_args):
                return {"races": [], "runners": []}

            def fetch_results_page(self, *_args):
                return type("Response", (), {"body": ""})()

            def fetch_chinese_results_page(self, *_args):
                return type("Response", (), {"body": ""})()

            def parse_results(self, *_args):
                return {"races": [], "runners": [], "results": []}

        def fail_loader(*args, **kwargs):
            raise AssertionError("non-meeting days should be skipped after a one-race probe")

        monkeypatch.setattr("racing_model.backfill.HKJCSource", EmptySource)
        monkeypatch.setattr("racing_model.backfill.load_hkjc_race_day", fail_loader)

        payload = load_hkjc_date_range(conn, "2026-05-07", "2026-05-07", "ST", 10, "test-agent", 0.0)

    assert payload["no_meeting_days"] == 1
    assert payload["skipped_days"] == 1
    assert payload["day_results"][0]["skip_reason"] == "no_matching_hkjc_meeting"


def test_black_box_replay_emits_fake_tickets_against_old_results() -> None:
    race_a = [
        feature_runner("R1", "H001", 1, 95.0, 2.0, 1.2),
        feature_runner("R1", "H002", 2, 80.0, 4.0, 1.8),
    ]
    race_b = [
        feature_runner("R2", "H003", 1, 94.0, 2.1, 1.3),
        feature_runner("R2", "H004", 2, 75.0, 5.0, 2.0),
    ]

    replay = black_box_fake_ticket_replay([race_a, race_b], epochs=8, max_races=2)

    assert replay["status"] == "ok"
    assert replay["replayed_races"] == 1
    assert replay["fake_tickets"] == 2
    assert replay["win_hit_rate"] is not None
    assert replay["place_hit_rate"] is not None


def runner(race_id: str, horse_id: str, horse_no: int) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": horse_id,
        "last_six_runs": "123456",
        "horse_name_zh": horse_id,
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": 120,
        "body_weight_lbs": 1100,
        "official_rating": 60,
        "age": 4,
        "sex": "G",
        "sire": "Sire",
        "dam": "Dam",
        "running_style": "mid",
        "gear": "",
    }


def result(race_id: str, horse_id: str, finish: int) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": finish,
        "finish_time_sec": 70.0 + finish,
        "margin_lengths": float(finish - 1),
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "running_positions": "",
        "comment": "",
    }


def feature_runner(
    race_id: str,
    horse_id: str,
    finish: int,
    rating: float,
    win_odds: float,
    place_odds: float,
) -> RunnerFeatures:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["official_rating"] = rating
    return RunnerFeatures(
        race_id=race_id,
        horse_id=horse_id,
        horse_no=finish,
        horse_name=horse_id,
        last_six_runs="",
        horse_name_zh="",
        running_style="mid",
        gear="",
        body_weight_lbs=None,
        jockey="Jockey",
        jockey_zh="",
        trainer="Trainer",
        trainer_zh="",
        draw=finish,
        features=features,
        latest_win_odds=win_odds,
        latest_win_odds_source="unit_test",
        latest_place_odds=place_odds,
        latest_place_odds_source="unit_test",
        finish_position=finish,
    )
