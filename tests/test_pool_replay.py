from pathlib import Path

from racing_model.pool_replay import pool_replay_report
from racing_model.storage import connect, init_db, insert_rows


def test_pool_replay_groups_settled_tickets_by_market(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        insert_rows(
            conn,
            "betting_recommendations",
            [
                recommendation("r1-win-hit", "WIN", 10, 30, 20, 1, final_odds=3.0),
                recommendation("r2-win-lose", "WIN", 10, 0, -10, 0, final_odds=0),
                recommendation("r3-qpl-thin", "QPL", 10, 12, 2, 1, final_odds=1.2),
                recommendation("r4-trio-pending", "TRIO", 5, None, None, None, status="pending"),
            ],
        )
        conn.commit()

        report = pool_replay_report(conn)
    finally:
        conn.close()

    summary = report["summary"]
    markets = {row["market"]: row for row in report["markets"]}
    assert summary["tickets"] == 4
    assert summary["reconciled"] == 3
    assert summary["active_markets"] == 3
    assert markets["WIN"]["reconciled"] == 2
    assert markets["WIN"]["roi"] == 0.5
    assert markets["QPL"]["low_return_hits"] == 1
    assert markets["TRIO"]["pending"] == 1
    assert report["ranking"][0]["market"] == "WIN"
    assert any(row["level"] == "upgrade" for row in report["insights"])


def recommendation(
    recommendation_id: str,
    market: str,
    stake: float,
    returned: float | None,
    profit: float | None,
    outcome: int | None,
    final_odds: float | None = None,
    status: str = "reconciled",
) -> dict[str, object]:
    return {
        "recommendation_id": recommendation_id,
        "created_at": f"2026-05-06T12:0{len(recommendation_id)}:00+00:00",
        "updated_at": f"2026-05-06T12:0{len(recommendation_id)}:00+00:00",
        "source": "test",
        "model_path": "models/test.json",
        "race_id": "HK20260506-ST-01",
        "race_date": "2026-05-06",
        "market": market,
        "market_label": market,
        "horse_id": "1+2" if market in {"QPL", "TRIO"} else recommendation_id,
        "horse_no": 1 if market in {"WIN", "PLACE"} else None,
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
        "recommended_stake": stake,
        "race_status_at_recommendation": "scheduled",
        "action": "bet",
        "reason": "test",
        "final_odds": final_odds,
        "finish_position": 1 if outcome else 4,
        "outcome_win": outcome,
        "returned": returned,
        "profit": profit,
        "clv": 0.0 if status == "reconciled" else None,
        "slippage": 0.0 if status == "reconciled" else None,
        "reconciled_at": "2026-05-06T13:00:00+00:00" if status == "reconciled" else None,
        "reconciliation_status": status,
    }
