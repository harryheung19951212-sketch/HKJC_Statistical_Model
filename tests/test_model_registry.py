import json
import tempfile
from pathlib import Path

from racing_model.model import RankingModel
from racing_model.model_registry import (
    apply_execution_gate,
    execution_gate_report,
    model_registry_report,
    promote_latest_model,
    run_and_record_model_registry,
    statistical_promotion_gate,
)
from racing_model.storage import connect, import_csv, init_db, insert_rows
from racing_model.walk_forward import build_oos_calibration_gate


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
    assert recorded["run"]["race_count"] >= 1
    assert registry["summary"]["run_count"] == 1
    assert registry["runs"][0]["promotion_gate_label"] == "樣本不足"
    assert "report_json" not in registry["runs"][0]
    assert registry["latest_report"]["summary"]["folds"] == recorded["run"]["folds"]
    assert registry["runs"][0]["execution_gate"] == "unverified"
    assert registry["runs"][0]["execution_gate_label"] == "下注時樣本不足"
    assert registry["clv_status"]
    assert recorded["report"]["candidate_artifact"]["artifact_type"] == "walk_forward_candidate_oos_evidence"
    assert recorded["report"]["candidate_calibration_artifact"]["artifact_type"] == "walk_forward_oos_candidate_reliability"
    assert recorded["report"]["experiment_manifest"]["artifact_type"] == "walk_forward_experiment_manifest"
    assert recorded["report"]["experiment_manifest"]["variants"]
    assert recorded["report"]["experiment_manifest"]["ablation_trail"]
    assert recorded["report"]["candidate_calibration_gate"]["gate"]
    assert recorded["report"]["oos_gate"]["gate"]
    assert recorded["report"]["versions"][0]["slice_scorecard"]
    assert recorded["report"]["versions"][0]["calibration_bins"]
    assert registry["latest_oos_gate"]["gate"]
    assert registry["latest_candidate_calibration_gate"]["gate"]
    assert registry["latest_experiment_manifest"]["artifact_type"] == "walk_forward_experiment_manifest"
    assert registry["latest_experiment_manifest"]["ablation_trail"][0]["deltas_vs_baseline"]


def test_slice_oos_gate_blocks_regressed_upgrade_candidate() -> None:
    summary = {"folds": 30}
    baseline = version_row(
        "baseline",
        log_loss=1.20,
        roi=0.02,
        drawdown=20,
        slice_log_loss=1.10,
        slice_brier=0.60,
        slice_top_pick=0.45,
    )
    candidate = version_row(
        "no_market",
        log_loss=1.10,
        roi=0.03,
        drawdown=18,
        slice_log_loss=1.35,
        slice_brier=0.72,
        slice_top_pick=0.20,
    )

    gate = statistical_promotion_gate(summary, candidate, baseline)

    assert gate == "slice_blocked"


def test_candidate_oos_calibration_blocks_upgrade_candidate() -> None:
    summary = {"folds": 30}
    baseline = version_row(
        "baseline",
        log_loss=1.20,
        roi=0.02,
        drawdown=20,
        slice_log_loss=1.10,
        slice_brier=0.60,
        slice_top_pick=0.45,
    )
    candidate = version_row(
        "no_market",
        log_loss=1.10,
        roi=0.03,
        drawdown=18,
        slice_log_loss=1.08,
        slice_brier=0.58,
        slice_top_pick=0.45,
        calibration_gap=-0.22,
    )

    gate = statistical_promotion_gate(summary, candidate, baseline)
    calibration_gate = build_oos_calibration_gate(candidate)

    assert calibration_gate["gate"] == "blocked"
    assert gate == "calibration_blocked"


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


def test_execution_gate_blocks_stale_execution_prices_even_with_profit(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        rows = [
            executed_recommendation(
                f"stale-{index}",
                profit=20,
                final_odds=3.0,
                outcome_win=1,
                execution_value_status="stale_price",
            )
            for index in range(6)
        ]
        rows.extend(
            executed_recommendation(
                f"valid-{index}",
                profit=20,
                final_odds=3.0,
                outcome_win=1,
                execution_value_status="valid_execution",
            )
            for index in range(14)
        )
        insert_rows(conn, "betting_recommendations", rows)
        conn.commit()
        gate = execution_gate_report(conn, min_confirmed=20)

    assert gate["gate"] == "blocked"
    assert gate["execution_roi"] == 2.0
    assert gate["stale_price"] == 6
    assert gate["invalid_execution_rate"] == 0.3


def test_execution_gate_requires_confirmed_sample_before_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        gate = execution_gate_report(conn, min_confirmed=20)

    assert gate["gate"] == "unverified"
    assert apply_execution_gate("upgrade_candidate", gate) == "execution_unverified"


def test_promote_latest_model_refuses_without_upgrade_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        no_run = promote_latest_model(conn, tmp_path / "baseline.json", epochs=1)
        insert_rows(conn, "model_registry_runs", [registry_row("sample_insufficient")])
        conn.commit()
        held = promote_latest_model(conn, tmp_path / "baseline.json", epochs=1)

    assert no_run["status"] == "refused"
    assert no_run["promotion_gate"] == "no_data"
    assert held["status"] == "refused"
    assert held["promotion_gate"] == "sample_insufficient"


def test_promote_latest_model_trains_candidate_and_backups_current_file(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    model_path = tmp_path / "baseline.json"
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
        RankingModel.new().save(model_path)
        insert_rows(conn, "model_registry_runs", [registry_row("upgrade_candidate", best_variant_id="no_market")])
        conn.commit()

        result = promote_latest_model(conn, model_path, epochs=1, enforce_calibration=False)

    promoted = RankingModel.load(model_path)
    assert result["status"] == "promoted"
    assert result["variant_id"] == "no_market"
    assert result["trained_races"] > 0
    assert model_path.exists()
    assert result["backup_path"]
    assert Path(str(result["backup_path"])).exists()
    assert "market_implied" not in promoted.feature_names
    assert promoted.temperature == 1.0


def test_promote_latest_model_refuses_when_calibration_gate_is_unverified(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    model_path = tmp_path / "baseline.json"
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
        RankingModel.new().save(model_path)
        insert_rows(conn, "model_registry_runs", [registry_row("upgrade_candidate", best_variant_id="no_market")])
        conn.commit()

        result = promote_latest_model(conn, model_path, epochs=1)

    assert result["status"] == "refused"
    assert result["calibration_gate"]["status"] == "unverified"
    assert result["calibration_gate"]["promote_allowed"] is False


def test_promote_latest_model_persists_calibrated_temperature(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    model_path = tmp_path / "baseline.json"
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
        RankingModel.new().save(model_path)
        insert_rows(
            conn,
            "model_registry_runs",
            [
                registry_row(
                    "upgrade_candidate",
                    best_variant_id="conservative_calibrated",
                    best_label="Conservative",
                )
            ],
        )
        conn.commit()

        result = promote_latest_model(conn, model_path, epochs=1, enforce_calibration=False)

    promoted = RankingModel.load(model_path)
    assert result["status"] == "promoted"
    assert result["variant_id"] == "conservative_calibrated"
    assert result["temperature"] == 1.35
    assert promoted.temperature == 1.35


def registry_row(gate: str, best_variant_id: str = "no_market", best_label: str = "No Market") -> dict[str, object]:
    report = {
        "summary": {
            "race_count": 40,
            "folds": 30,
            "best_variant_id": best_variant_id,
            "best_label": best_label,
            "recommendation": "候選版本通過 gate，可替換模型。",
        },
        "versions": [],
    }
    return {
        "run_id": f"run-{gate}-{best_variant_id}",
        "created_at": "2026-05-08T12:00:00+00:00",
        "trigger": "test",
        "model_path": "models/baseline.json",
        "min_train_races": 1,
        "epochs": 1,
        "min_expected_value": 0.05,
        "stake": 10,
        "race_count": 40,
        "folds": 30,
        "best_variant_id": best_variant_id,
        "best_label": best_label,
        "baseline_log_loss": 1.4,
        "best_log_loss": 1.2,
        "baseline_value_roi": 0.02,
        "best_value_roi": 0.04,
        "best_top_pick_hit_rate": 0.3,
        "best_max_drawdown": 20,
        "execution_confirmed": 20,
        "execution_roi": 0.05,
        "execution_max_drawdown": 10,
        "execution_gate": "pass",
        "promotion_gate": gate,
        "recommendation": "候選版本通過 gate，可替換模型。",
        "report_json": json.dumps(report, ensure_ascii=False),
    }


def version_row(
    variant_id: str,
    log_loss: float,
    roi: float,
    drawdown: float,
    slice_log_loss: float,
    slice_brier: float,
    slice_top_pick: float,
    calibration_gap: float = 0.02,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "label": variant_id,
        "metrics": {
            "log_loss": log_loss,
            "value_roi": roi,
            "max_drawdown": drawdown,
            "top_pick_hit_rate": 0.35,
        },
        "slice_scorecard": [
            {
                "slice_id": "distance:短途 <=1200米",
                "dimension": "distance",
                "dimension_label": "途程",
                "value": "短途 <=1200米",
                "label": "途程：短途 <=1200米",
                "races": 8,
                "log_loss": slice_log_loss,
                "brier_score": slice_brier,
                "top_pick_hit_rate": slice_top_pick,
                "value_roi": roi,
            }
        ],
        "calibration_bins": [
            {
                "label": "20-30%",
                "count": 32,
                "avg_prediction": 0.26,
                "observed_rate": 0.26 + calibration_gap,
                "gap": calibration_gap,
            }
        ],
    }


def executed_recommendation(
    recommendation_id: str,
    profit: float,
    final_odds: float,
    outcome_win: int = 0,
    execution_value_status: str = "valid_execution",
) -> dict[str, object]:
    returned = 10 * final_odds if outcome_win and final_odds else 0
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
        "execution_value_status": execution_value_status,
        "execution_value_message": "unit test execution value status",
        "execution_edge_at_bet": 0.05 if execution_value_status == "valid_execution" else -0.02,
        "execution_expected_value_at_bet": 0.2 if execution_value_status == "valid_execution" else -0.05,
        "final_odds": final_odds,
        "finish_position": 1 if outcome_win else 4,
        "outcome_win": outcome_win,
        "returned": returned,
        "profit": profit,
        "clv": 0.0,
        "slippage": 0.0,
        "reconciled_at": "2026-05-06T13:00:00+00:00",
        "reconciliation_status": "reconciled",
    }


if __name__ == "__main__":
    direct_tests = [
        test_model_registry_records_walk_forward_gate,
        test_execution_gate_blocks_or_holds_upgrade_candidates,
        test_execution_gate_blocks_stale_execution_prices_even_with_profit,
        test_execution_gate_requires_confirmed_sample_before_upgrade,
        test_promote_latest_model_refuses_without_upgrade_gate,
        test_promote_latest_model_trains_candidate_and_backups_current_file,
        test_promote_latest_model_refuses_when_calibration_gate_is_unverified,
        test_promote_latest_model_persists_calibrated_temperature,
    ]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        for index, test in enumerate(direct_tests):
            case = base / f"case-{index}"
            case.mkdir()
            test(case)
