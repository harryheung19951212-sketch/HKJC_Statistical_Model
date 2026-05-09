from pathlib import Path

from racing_model.pool_replay import pool_replay_calibration, pool_replay_report
from racing_model.exotic_dividends import upsert_exotic_dividends
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
                recommendation("r2-win-lose", "WIN", 10, 0, -10, 0, final_odds=0, executed=True, execution_stake=20, execution_odds=2.6),
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
    assert summary["executed"] == 1
    assert summary["execution_staked"] == 20
    assert summary["execution_profit"] == -20
    assert summary["execution_roi"] == -1.0
    assert summary["active_markets"] == 3
    assert report["bankroll_replay"]["settled_tickets"] == 3
    assert report["bankroll_replay"]["profit"] == 12
    assert report["bankroll_replay"]["risk_audit"]["status"] == "pass"
    assert markets["WIN"]["reconciled"] == 2
    assert markets["WIN"]["roi"] == 0.5
    assert markets["WIN"]["executed"] == 1
    assert markets["WIN"]["execution_roi"] == -1.0
    assert markets["QPL"]["low_return_hits"] == 1
    assert markets["TRIO"]["pending"] == 1
    assert report["ranking"][0]["market"] == "WIN"
    assert any(row["level"] == "upgrade" for row in report["insights"])


def test_pool_replay_audits_bankroll_exposure_breaches(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        rows = [
            recommendation("r1-win-heavy", "WIN", 120, 0, -120, 0),
            recommendation("r1-place-heavy", "PLACE", 120, 0, -120, 0),
            recommendation("r1-qpl-heavy", "QPL", 130, 0, -130, 0),
            recommendation("r1-qpl-heavy-2", "QPL", 100, 0, -100, 0),
        ]
        for row in rows:
            row["horse_id"] = "1" if row["market"] in {"WIN", "PLACE"} else "1+2"
            row["horse_name"] = "Shared Horse"
        rows[-1]["horse_id"] = "1+3"
        insert_rows(conn, "betting_recommendations", rows)
        conn.commit()

        report = pool_replay_report(conn)
    finally:
        conn.close()

    replay = report["bankroll_replay"]
    audit = replay["risk_audit"]
    assert replay["max_drawdown"] == -470
    assert audit["status"] == "breached"
    assert audit["breach_count"] > 0
    assert audit["race_breaches"]
    assert audit["horse_breaches"]
    assert audit["pool_breaches"]
    assert audit["combination_breaches"]


def test_pool_replay_flags_exotic_winners_waiting_for_final_dividend(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-02",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                    "race_name": "測試賽",
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [runner("H001", 1), runner("H002", 2), runner("H003", 3), runner("H004", 4)],
        )
        insert_rows(
            conn,
            "results",
            [result("H001", 1), result("H002", 2), result("H003", 3), result("H004", 4)],
        )
        upsert_exotic_dividends(
            conn,
            "HK20260506-ST-02",
            [{"market": "TRIO", "combination": "1+2+3", "dividend": 88.0}],
            source="hkjc_results_final",
            dividend_status="final",
        )
        rows = [
            recommendation("qpl-waiting-final", "QPL", 20, None, None, None, status="pending", executed=True, execution_stake=20, execution_odds=16.0),
            recommendation("trio-ready-final", "TRIO", 10, None, None, None, status="pending", executed=True, execution_stake=10, execution_odds=80.0),
            recommendation("qpl-known-loss", "QPL", 20, None, None, None, status="pending", executed=True, execution_stake=20, execution_odds=20.0),
        ]
        rows[0]["race_id"] = rows[1]["race_id"] = rows[2]["race_id"] = "HK20260506-ST-02"
        rows[0]["horse_id"] = "1+2"
        rows[1]["horse_id"] = "1+2+3"
        rows[2]["horse_id"] = "1+4"
        insert_rows(conn, "betting_recommendations", rows)
        conn.commit()

        report = pool_replay_report(conn, race_id="HK20260506-ST-02")
    finally:
        conn.close()

    audit = report["final_dividend_audit"]
    markets = {row["market"]: row for row in report["markets"]}
    assert audit["summary"]["waiting_final_dividend"] == 1
    assert audit["summary"]["final_ready_unreconciled"] == 1
    assert audit["summary"]["known_loss_unreconciled"] == 1
    assert audit["summary"]["status"] == "waiting_final_dividend"
    assert markets["QPL"]["final_dividend_waiting_hits"] == 1
    assert markets["QPL"]["exotic_unsettled_known_losses"] == 1
    assert markets["TRIO"]["final_dividend_ready_hits"] == 1


def test_pool_replay_calibration_blocks_poor_settled_pool(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        rows = [
            recommendation(f"qpl-loss-{index}", "QPL", 20, 0, -20, 0)
            for index in range(10)
        ]
        for index, row in enumerate(rows, start=1):
            row["horse_id"] = f"{index}+{index + 1}"
        insert_rows(conn, "betting_recommendations", rows)
        conn.commit()

        report = pool_replay_report(conn)
        gate = pool_replay_calibration(report)
    finally:
        conn.close()

    qpl = gate["markets"]["QPL"]
    assert gate["status"] == "blocked"
    assert qpl["status"] == "replay_block"
    assert qpl["stake_factor"] == 0.0
    assert qpl["min_samples"] == 10
    assert "暫停真注" in qpl["reason"]


def recommendation(
    recommendation_id: str,
    market: str,
    stake: float,
    returned: float | None,
    profit: float | None,
    outcome: int | None,
    final_odds: float | None = None,
    status: str = "reconciled",
    executed: bool = False,
    execution_stake: float | None = None,
    execution_odds: float | None = None,
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
        "execution_status": "confirmed" if executed else "suggested",
        "executed_at": "2026-05-06T12:30:00+00:00" if executed else None,
        "execution_odds": execution_odds,
        "execution_stake": execution_stake,
        "execution_source": "unit_test" if executed else "",
        "execution_slippage": (execution_odds - 3.0) if execution_odds is not None else None,
        "execution_clv": None,
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
