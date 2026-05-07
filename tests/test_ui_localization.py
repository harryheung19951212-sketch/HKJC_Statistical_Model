from pathlib import Path

from racing_model.app_server import api_betting, api_predictions
from racing_model.backfill import normalize_hkjc_date
from racing_model.model import RankingModel
from racing_model.storage import connect, init_db, insert_rows


ROOT = Path(__file__).resolve().parents[1]


def test_hkjc_date_normalization_accepts_date_picker_and_legacy_formats() -> None:
    assert normalize_hkjc_date("2026-05-06") == "2026/05/06"
    assert normalize_hkjc_date("2026/05/06") == "2026/05/06"
    assert normalize_hkjc_date("2026-5-6") == "2026/05/06"


def test_all_ui_date_fields_use_browser_date_picker() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<input id="race-date" type="date"' in html
    assert '<input id="backfill-start" type="date"' in html
    assert '<input id="backfill-end" type="date"' in html


def test_sidebar_ingestion_forms_are_collapsible() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<details class="sidebar-action">' in html
    assert "<summary><span>載入賽日</span><b>賽日 / 馬場</b></summary>" in html
    assert "<summary><span>歷史回填</span><b>日期範圍 / 重訓</b></summary>" in html


def test_predictions_and_betting_payloads_prefer_chinese_names(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
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
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "ENGLISH HORSE",
                    "horse_name_zh": "中文馬",
                    "jockey": "English Jockey",
                    "jockey_zh": "中文騎師",
                    "trainer": "English Trainer",
                    "trainer_zh": "中文練馬師",
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
            "odds_ticks",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "timestamp": "2026-05-06T12:00:00+00:00",
                    "win_odds": 3.0,
                    "place_odds": 1.5,
                    "source": "hkjc_mqtt",
                }
            ],
        )
        conn.commit()

        predictions = api_predictions(conn, RankingModel.new(), "HK20260506-ST-01")["predictions"]
        assert predictions[0]["display_name"] == "中文馬"
        assert predictions[0]["display_jockey"] == "中文騎師"
        assert predictions[0]["display_trainer"] == "中文練馬師"

        betting = api_betting(conn, RankingModel.new(), "HK20260506-ST-01", 10000, "aggressive")
        assert {row["horse_name"] for row in betting["decisions"]} == {"中文馬"}
        assert {row["jockey"] for row in betting["decisions"]} == {"中文騎師"}
        assert {row["trainer"] for row in betting["decisions"]} == {"中文練馬師"}
