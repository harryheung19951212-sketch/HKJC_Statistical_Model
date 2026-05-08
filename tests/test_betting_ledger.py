from pathlib import Path

from racing_model.betting_ledger import (
    betting_ledger_report,
    confirm_betting_recommendation,
    reconcile_betting_ledger,
    record_betting_payload,
)
from racing_model.exotic_dividends import upsert_exotic_dividends
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


def test_bet_time_confirmation_records_execution_odds_and_survives_refresh(tmp_path: Path) -> None:
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
            "odds_ticks",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "timestamp": "2026-05-06T12:00:00+00:00",
                    "win_odds": 4.0,
                    "place_odds": 1.4,
                    "source": "hkjc_mqtt",
                },
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "timestamp": "2026-05-06T12:01:00+00:00",
                    "win_odds": 3.6,
                    "place_odds": 1.3,
                    "source": "hkjc_mqtt",
                },
            ],
        )
        conn.commit()

        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "測試馬", 100)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        recommendation_id = payload["tickets"][0]["recommendation_id"]

        confirmed = confirm_betting_recommendation(conn, recommendation_id, execution_stake=80, source="unit_test")
        record_betting_payload(conn, race, payload, "models/baseline.json")
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    assert confirmed["status"] == "confirmed"
    item = ledger["items"][0]
    assert item["execution_status"] == "confirmed"
    assert item["execution_odds"] == 3.6
    assert item["execution_stake"] == 80
    assert item["execution_source"] == "hkjc_mqtt"
    assert item["execution_slippage"] is None
    assert ledger["summary"]["confirmed"] == 1
    assert ledger["summary"]["executed_staked"] == 80


def test_refreshing_same_ticket_updates_one_logical_recommendation(tmp_path: Path) -> None:
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
        conn.commit()

        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "測試馬", 100)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        first_id = payload["tickets"][0]["recommendation_id"]
        payload["tickets"][0]["odds"] = 4.8
        payload["tickets"][0]["expected_value"] = 0.92
        record_betting_payload(conn, race, payload, "models/baseline.json")
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    assert payload["tickets"][0]["recommendation_id"] == first_id
    assert ledger["summary"]["recommendations"] == 1
    assert ledger["items"][0]["recommended_odds"] == 4.8
    assert ledger["items"][0]["execution_status"] == "confirmed"
    assert ledger["items"][0]["execution_odds"] == 4.8


def test_confirmed_ticket_keeps_live_pool_price_until_settlement(tmp_path: Path) -> None:
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
        conn.commit()

        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "測試馬", 100)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        first_id = payload["tickets"][0]["recommendation_id"]
        payload["tickets"][0]["odds"] = 5.2
        payload["tickets"][0]["expected_value"] = 1.08
        payload["tickets"][0]["recommended_stake"] = 60
        record_betting_payload(conn, race, payload, "models/baseline.json")
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    item = ledger["items"][0]
    assert payload["tickets"][0]["recommendation_id"] == first_id
    assert item["execution_status"] == "confirmed"
    assert item["recommended_odds"] == 5.2
    assert item["execution_odds"] == 5.2
    assert item["execution_stake"] == 100
    assert "不鎖入飛賠率" in ledger["clv_note"]


def test_ledger_records_pool_choice_and_flags_stale_execution_price(tmp_path: Path) -> None:
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
        conn.commit()

        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "Pool Choice", 100)
        payload["tickets"][0]["required_dividend"] = 3.8
        payload["tickets"][0]["cost_adjusted_expected_value"] = 0.55
        payload["tickets"][0]["minimum_ticket_cost"] = 10
        payload["tickets"][0]["slip_strategy"] = "standard"
        payload["tickets"][0]["slip_rank"] = 1
        payload["tickets"][0]["slip_priority_score"] = 55.5
        payload["tickets"][0]["risk_tier"] = "standard"
        payload["tickets"][0]["portfolio_role"] = "value"
        payload["tickets"][0]["expected_profit"] = 28.0
        payload["tickets"][0]["hit_probability"] = 0.4
        payload["pool_choice"] = {
            "markets": [
                {
                    "market": "WIN",
                    "choice_score": 42.5,
                    "verdict": "actionable",
                    "best_required_dividend": 3.8,
                    "best_minimum_ticket_cost": 10,
                }
            ]
        }
        record_betting_payload(conn, race, payload, "models/baseline.json")
        recommendation_id = payload["tickets"][0]["recommendation_id"]
        confirmed = confirm_betting_recommendation(
            conn,
            recommendation_id,
            execution_odds=3.2,
            execution_stake=100,
            source="unit_test",
        )
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    item = ledger["items"][0]
    assert confirmed["status"] == "confirmed"
    assert item["pool_choice_score"] == 42.5
    assert item["pool_choice_rank"] == 1
    assert item["pool_choice_verdict"] == "actionable"
    assert item["required_dividend"] == 3.8
    assert item["slip_strategy"] == "standard"
    assert item["slip_rank"] == 1
    assert item["slip_priority_score"] == 55.5
    assert item["risk_tier"] == "standard"
    assert item["portfolio_role"] == "value"
    assert item["expected_profit"] == 28.0
    assert item["hit_probability"] == 0.4
    assert item["execution_value_status"] == "stale_price"
    assert item["execution_expected_value_at_bet"] == 0.28
    assert item["execution_edge_at_bet"] > 0


def test_winning_win_ticket_waits_for_final_odds(tmp_path: Path) -> None:
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
        insert_rows(conn, "results", [simple_result("HK20260506-ST-01", "H001", 1)])
        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "測試馬", 100)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        reconciled = reconcile_betting_ledger(conn, "HK20260506-ST-01")
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    assert reconciled["updated"] == 0
    assert reconciled["pending"] == 1
    assert ledger["items"][0]["reconciliation_status"] == "pending"


def test_losing_win_ticket_settles_without_final_odds(tmp_path: Path) -> None:
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
        insert_rows(conn, "results", [simple_result("HK20260506-ST-01", "H001", 4)])
        race = {"race_id": "HK20260506-ST-01", "date": "2026/05/06"}
        payload = simple_win_payload("H001", 1, "測試馬", 100)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        reconciled = reconcile_betting_ledger(conn, "HK20260506-ST-01")
        ledger = betting_ledger_report(conn, "HK20260506-ST-01")

    assert reconciled["updated"] == 1
    assert ledger["items"][0]["outcome_win"] == 0
    assert ledger["items"][0]["profit"] == -100


def test_betting_ledger_reconciles_exotic_ticket(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-02",
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
                runner("H001", 1),
                runner("H002", 2),
                runner("H003", 3),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("H001", 1),
                result("H002", 2),
                result("H003", 3),
            ],
        )
        upsert_exotic_dividends(
            conn,
            "HK20260506-ST-02",
            [{"market": "QPL", "combination": "1+2", "dividend": 18.0, "dividend_status": "final"}],
            source="manual_test",
            dividend_status="final",
        )

        race = {"race_id": "HK20260506-ST-02", "date": "2026/05/06"}
        payload = {
            "race_status": "scheduled",
            "risk_profile": "standard",
            "bankroll": 10000,
            "tickets": [
                {
                    "market": "QPL",
                    "market_label": "位置Q",
                    "horse_id": "1+2",
                    "horse_no": None,
                    "horse_name": "1 + 2",
                    "model_rank": 1,
                    "probability": 0.12,
                    "odds": 20.0,
                    "odds_source": "manual_test",
                    "fair_odds": 8.33,
                    "market_probability": 0.05,
                    "edge": 0.07,
                    "expected_value": 1.4,
                    "recommended_stake": 50,
                    "action": "有值博",
                    "reason": "符合 Kelly 下注條件",
                }
            ],
        }
        record_betting_payload(conn, race, payload, "models/baseline.json")
        reconcile_betting_ledger(conn, "HK20260506-ST-02")
        ledger = betting_ledger_report(conn, "HK20260506-ST-02")

    assert ledger["summary"]["reconciled"] == 1
    assert ledger["summary"]["profit"] == 850
    assert ledger["items"][0]["outcome_win"] == 1


def test_winning_exotic_ticket_waits_for_final_dividend(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-02",
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
        insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2), runner("H003", 3)])
        insert_rows(conn, "results", [result("H001", 1), result("H002", 2), result("H003", 3)])
        upsert_exotic_dividends(
            conn,
            "HK20260506-ST-02",
            [{"market": "QPL", "combination": "1+2", "dividend": 18.0, "dividend_status": "probable"}],
            source="manual_test",
            dividend_status="probable",
        )

        race = {"race_id": "HK20260506-ST-02", "date": "2026/05/06"}
        payload = exotic_payload("QPL", "位置Q", "1+2", 20.0, 50)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        result_summary = reconcile_betting_ledger(conn, "HK20260506-ST-02")
        ledger = betting_ledger_report(conn, "HK20260506-ST-02")

    assert result_summary["pending"] == 1
    assert result_summary["updated"] == 0
    assert ledger["summary"]["reconciled"] == 0
    assert ledger["items"][0]["reconciliation_status"] == "pending"


def test_losing_exotic_ticket_can_settle_without_final_dividend(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-02",
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
        insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2), runner("H003", 3)])
        insert_rows(conn, "results", [result("H001", 1), result("H002", 2), result("H003", 4)])

        race = {"race_id": "HK20260506-ST-02", "date": "2026/05/06"}
        payload = exotic_payload("QPL", "位置Q", "1+3", 20.0, 50)
        record_betting_payload(conn, race, payload, "models/baseline.json")
        result_summary = reconcile_betting_ledger(conn, "HK20260506-ST-02")
        ledger = betting_ledger_report(conn, "HK20260506-ST-02")

    assert result_summary["updated"] == 1
    assert ledger["summary"]["reconciled"] == 1
    assert ledger["summary"]["profit"] == -50
    assert ledger["items"][0]["outcome_win"] == 0


def runner(horse_id: str, horse_no: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-02",
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
        "running_style": "pace",
        "gear": "",
    }


def result(horse_id: str, position: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-02",
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 70.0 + position,
        "margin_lengths": position - 1,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }


def simple_result(race_id: str, horse_id: str, position: int) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 70.0 + position,
        "margin_lengths": max(position - 1, 0),
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }


def simple_win_payload(horse_id: str, horse_no: int, horse_name: str, stake: float) -> dict[str, object]:
    return {
        "race_status": "scheduled",
        "risk_profile": "standard",
        "bankroll": 10000,
        "tickets": [
            {
                "market": "WIN",
                "market_label": "獨贏",
                "horse_id": horse_id,
                "horse_no": horse_no,
                "horse_name": horse_name,
                "model_rank": 1,
                "probability": 0.4,
                "odds": 4.0,
                "odds_source": "hkjc_mqtt",
                "fair_odds": 2.5,
                "market_probability": 0.25,
                "edge": 0.15,
                "expected_value": 0.6,
                "recommended_stake": stake,
                "action": "有值博",
                "reason": "符合 Kelly 下注條件",
            }
        ],
    }


def exotic_payload(market: str, market_label: str, combination: str, odds: float, stake: float) -> dict[str, object]:
    return {
        "race_status": "scheduled",
        "risk_profile": "standard",
        "bankroll": 10000,
        "tickets": [
            {
                "market": market,
                "market_label": market_label,
                "horse_id": combination,
                "horse_no": None,
                "horse_name": combination.replace("+", " + "),
                "model_rank": 1,
                "probability": 0.12,
                "odds": odds,
                "odds_source": "manual_test",
                "fair_odds": 8.33,
                "market_probability": 1 / odds,
                "edge": 0.07,
                "expected_value": 1.4,
                "recommended_stake": stake,
                "action": "有值博",
                "reason": "符合 Kelly 下注條件",
            }
        ],
    }
