from pathlib import Path

from racing_model.model_registry import (
    apply_execution_gate,
    execution_gate_report,
    model_registry_report,
    run_and_record_model_registry,
)
from racing_model.storage import connect, import_csv, init_db, insert_rows


def test_model_registry_records_walk_forward_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    sample_dir = Path("data/sample")
    with connect(db_path) as conn:
        for table, filename in {
            "races": "races.csv",
            "runners": "runners.csv",
            "results": "results.csv",
            "workouts": "workouts.csv",
            "odds_ticks": "odds.csv",
        }.items():
            import_csv(conn, table, sample_dir / filename)
        conn.commit()

        recorded = run_and_record_model_registry(conn, "models/baseline.json", epochs=2)
        registry = model_registry_report(conn)

    assert recorded["status"] == "recorded"
    assert recorded["run"]["promotion_gate"] == "sample_insufficient"
    assert recorded["run"]["folds"] == 1
    assert registry["summary"]["run_count"] == 1
    assert registry["runs"][0]["promotion_gate_label"] == "樣本不足"
    assert "report_json" not in registry["runs"][0]
    assert registry["latest_report"]["summary"]["folds"] == 1
    assert registry["runs"][0]["execution_gate"] == "unverified"
    assert registry["runs"][0]["execution_gate_label"] == "下注時樣本不足"
    assert registry["clv_status"]


def test_execution_gate_blocks_or_holds_upgrade_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "betting_recommendations",
            [
                executed_recommendation(f"r{index}", profit=-10, final_odds=0)
                for index in range(20)
            ],
        )
        conn.commit()
        gate = execution_gate_report(conn, min_confirmed=20)

    assert gate["gate"] == "blocked"
    assert gate["confirmed"] == 20
    assert gate["execution_roi"] == -1.0
    assert apply_execution_gate("upgrade_candidate", gate) == "execution_blocked"
    assert apply_execution_gate("hold_baseline", gate) == "hold_baseline"


def test_execution_gate_requires_confirmed_sample_before_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        gate = execution_gate_report(conn, min_confirmed=20)

    assert gate["gate"] == "unverified"
    assert apply_execution_gate("upgrade_candidate", gate) == "execution_unverified"


def executed_recommendation(recommendation_id: str, profit: float, final_odds: float) -> dict[str, object]:
    return {
        "recommendation_id": recommendation_id,
        "created_at": "2026-05-06T12:00:00+00:00",
        "updated_at": "2026-05-06T13:00:00+00:00",
        "source": "test",
        "model_path": "models/test.json",
        "race_id": "HK20260506-ST-01",
        "race_date": "2026-05-06",
        "market": "WIN",
        "market_label": "獨贏",
        "horse_id": recommendation_id,
        "horse_no": 1,
        "horse_name": "測試馬",
        "model_rank": 1,
        "risk_profile": "standard",
        "bankroll": 10000,
        "probability": 0.3,
        "recommended_odds": 3.0,
        "odds_source": "test",
        "fair_odds": 3.3,
        "market_probability": 0.33,
        "edge": 0.02,
        "expected_value": 0.12,
        "recommended_stake": 10,
        "race_status_at_recommendation": "scheduled",
        "action": "bet",
        "reason": "test",
        "execution_status": "confirmed",
        "executed_at": "2026-05-06T12:30:00+00:00",
        "execution_odds": 2.5,
        "execution_stake": 10,
        "execution_source": "unit_test",
        "execution_slippage": -0.5,
        "execution_clv": None,
        "final_odds": final_odds,
        "finish_position": 4,
        "outcome_win": 0,
        "returned": 0,
        "profit": profit,
        "clv": 0.0,
        "slippage": 0.0,
        "reconciled_at": "2026-05-06T13:00:00+00:00",
        "reconciliation_status": "reconciled",
    }
