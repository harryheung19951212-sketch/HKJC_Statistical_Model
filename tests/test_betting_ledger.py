from pathlib import Path

from racing_model.betting_ledger import betting_ledger_report, reconcile_betting_ledger, record_betting_payload
from racing_model.storage import connect, init_db, insert_rows


def test_betting_ledger_records_active_ticket_once_and_reconciles(tmp_path: Path) -> None:
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
            "results",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "finish_position": 1,
                    "finish_time_sec": 70.1,
                    "margin_lengths": 0,
                    "sectional_400_sec": None,
                    "sectional_800_sec": None,
                    "comment": "",
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
                    "win_odds": 3.5,
                    "place_odds": 1.4,
                    "source": "hkjc_results_final",
                }
            ],
        )
        conn.commit()

        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = {
            "race_status": "scheduled",
            "risk_profile": "standard",
            "bankroll": 10000,
            "tickets": [
                {
                    "market": "WIN",
                    "market_label": "獨贏",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "測試馬",
                    "model_rank": 1,
                    "probability": 0.4,
                    "odds": 4.0,
                    "odds_source": "hkjc_mqtt",
                    "fair_odds": 2.5,
                    "market_probability": 0.25,
                    "edge": 0.15,
                    "expected_value": 0.6,
                    "recommended_stake": 100,
                    "action": "有值博",
                    "reason": "符合 Kelly 下注條件",
                }
            ],
        }

        first = record_betting_payload(conn, race, payload, "models/baseline.json")
        second = record_betting_payload(conn, race, payload, "models/baseline.json")
        before = betting_ledger_report(conn, "HK20260506-ST-01")
        reconciled = reconcile_betting_ledger(conn, "HK20260506-ST-01")
        after = betting_ledger_report(conn, "HK20260506-ST-01")

    assert first["recorded"] == 1
    assert second["recorded"] == 1
    assert before["summary"]["recommendations"] == 1
    assert reconciled["updated"] == 1
    assert after["summary"]["reconciled"] == 1
    assert after["summary"]["profit"] == 250
    assert after["items"][0]["clv"] > 0
