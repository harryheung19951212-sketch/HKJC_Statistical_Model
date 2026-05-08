import math
from pathlib import Path

from racing_model.market_flow import market_flow_report
from racing_model.storage import connect, init_db, insert_rows


def test_market_flow_report_excludes_final_odds_and_flags_steam(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "R-FLOW",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 3",
                    "prize": 1000000,
                }
            ],
        )
        insert_rows(conn, "runners", [runner("R-FLOW", "H001", 1, "紅運"), runner("R-FLOW", "H002", 2, "藍星")])
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("R-FLOW", "H001", "2026-05-06T12:00:00+08:00", 5.0, "hkjc_mqtt"),
                odds("R-FLOW", "H001", "2026-05-06T12:03:00+08:00", 4.0, "hkjc_mqtt"),
                odds("R-FLOW", "H001", "2026-05-06T12:04:45+08:00", 3.5, "hkjc_mqtt"),
                odds("R-FLOW", "H001", "2026-05-06T12:05:00+08:00", 2.0, "hkjc_results_final"),
                odds("R-FLOW", "H002", "2026-05-06T12:00:00+08:00", 4.0, "hkjc_mqtt"),
                odds("R-FLOW", "H002", "2026-05-06T12:03:00+08:00", 4.6, "hkjc_mqtt"),
                odds("R-FLOW", "H002", "2026-05-06T12:04:45+08:00", 4.9, "hkjc_mqtt"),
                odds("R-FLOW", "H002", "2026-05-06T12:05:00+08:00", 8.0, "hkjc_results_final"),
            ],
        )

        report = market_flow_report(conn, "R-FLOW")

    summary = report["summary"]
    rows = {row["horse_id"]: row for row in report["runners"]}
    assert summary["runner_count"] == 2
    assert summary["runners_with_ticks"] == 2
    assert summary["total_ticks"] == 6
    assert summary["verdict"] == "actionable_flow"
    assert rows["H001"]["latest_win_odds"] == 3.5
    assert rows["H001"]["flow_label"] == "落飛"
    assert rows["H001"]["signal_label"] == "落飛｜市場追捧"
    assert rows["H001"]["tick_status"] == "3 ticks"
    assert rows["H001"]["signal_status"] == "強落飛訊號"
    assert rows["H001"]["data_status"] == "3 ticks｜強落飛訊號"
    assert rows["H002"]["flow_label"] == "轉冷"
    assert rows["H002"]["signal_label"] == "轉冷｜市場降溫"
    assert rows["H002"]["signal_status"] == "強轉冷訊號"
    assert math.isclose(rows["H001"]["odds_delta_5m"], math.log(5.0 / 3.5), rel_tol=0.0001)
    assert any(item["level"] == "focus" for item in report["insights"])


def test_market_flow_report_marks_thin_sample(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "R-THIN",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 3",
                    "prize": 1000000,
                }
            ],
        )
        insert_rows(conn, "runners", [runner("R-THIN", "H001", 1, "紅運"), runner("R-THIN", "H002", 2, "藍星")])
        insert_rows(conn, "odds_ticks", [odds("R-THIN", "H001", "2026-05-06T12:00:00+08:00", 5.0, "hkjc_graphql")])

        report = market_flow_report(conn, "R-THIN")

    assert report["summary"]["verdict"] == "thin_sample"
    assert report["summary"]["coverage_rate"] == 0.5
    assert report["runners"][0]["tick_status"] in {"只有一口價", "無 live tick"}
    assert report["runners"][0]["signal_status"] == "資料不足，未能判斷"
    assert report["runners"][0]["signal_label"] == "資料不足｜未能判斷"


def runner(race_id: str, horse_id: str, horse_no: int, name_zh: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": f"Horse {horse_no}",
        "horse_name_zh": name_zh,
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": 120,
        "official_rating": 50,
        "age": 5,
        "sex": "G",
        "running_style": "pace",
        "gear": "",
    }


def odds(race_id: str, horse_id: str, timestamp: str, win_odds: float, source: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "timestamp": timestamp,
        "win_odds": win_odds,
        "place_odds": None,
        "source": source,
    }
