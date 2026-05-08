from pathlib import Path

from racing_model.pool_calibration import pool_calibration_report
from racing_model.storage import connect, init_db, insert_rows


def test_pool_calibration_groups_settled_tickets_by_market(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "betting_recommendations",
            [
                recommendation("win-hit", "WIN", 0.40, 1),
                recommendation("win-miss", "WIN", 0.40, 0),
                recommendation("place-hit", "PLACE", 0.65, 1),
                recommendation("place-miss", "PLACE", 0.65, 0),
                recommendation("qpl-hit", "QPL", 0.18, 1),
                {**recommendation("pending", "WIN", 0.50, 1), "reconciliation_status": "pending"},
            ],
        )
        conn.commit()

        report = pool_calibration_report(conn)

    win = next(row for row in report["markets"] if row["market"] == "WIN")
    place = next(row for row in report["markets"] if row["market"] == "PLACE")
    qpl = next(row for row in report["markets"] if row["market"] == "QPL")
    assert report["summary"]["tickets"] == 5
    assert win["tickets"] == 2
    assert win["observed_rate"] == 0.5
    assert round(float(win["gap"]), 2) == 0.10
    assert place["worst_bin"]["label"] == "50-100%"
    assert qpl["tickets"] == 1


def recommendation(recommendation_id: str, market: str, probability: float, outcome: int) -> dict[str, object]:
    return {
        "recommendation_id": recommendation_id,
        "created_at": "2026-05-09T12:00:00+00:00",
        "updated_at": "2026-05-09T13:00:00+00:00",
        "race_id": "HK20260509-ST-01",
        "race_date": "2026-05-09",
        "market": market,
        "market_label": market,
        "horse_id": recommendation_id,
        "probability": probability,
        "recommended_stake": 10,
        "race_status_at_recommendation": "resulted",
        "action": "bet",
        "reason": "unit test",
        "reconciliation_status": "reconciled",
        "outcome_win": outcome,
        "returned": 20 if outcome else 0,
        "profit": 10 if outcome else -10,
    }
