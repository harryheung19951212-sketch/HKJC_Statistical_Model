from racing_model.walk_forward import rank_versions_by_multi_objective


def test_multi_objective_rank_can_choose_roi_candidate_over_tiny_log_loss_edge() -> None:
    baseline = version("baseline", log_loss=1.00, brier=0.50, top1=0.25, top3=0.55, roi=0.00, drawdown=40)
    log_loss_only = version("log_loss_only", log_loss=0.98, brier=0.49, top1=0.25, top3=0.55, roi=-0.05, drawdown=55)
    balanced = version("balanced", log_loss=0.99, brier=0.49, top1=0.29, top3=0.62, roi=0.12, drawdown=35)

    ranked = rank_versions_by_multi_objective([baseline, log_loss_only, balanced], baseline)

    assert ranked[0]["variant_id"] == "balanced"
    assert ranked[0]["multi_objective_score"] > ranked[1]["multi_objective_score"]
    assert ranked[0]["multi_objective_scorecard"]["components"]["value_roi"]["reason"] == "較 Baseline 改善"
    assert ranked[0]["multi_objective_scorecard"]["weights"]["value_roi"] > 0


def version(
    variant_id: str,
    log_loss: float,
    brier: float,
    top1: float,
    top3: float,
    roi: float,
    drawdown: float,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "label": variant_id,
        "metrics": {
            "races": 40,
            "log_loss": log_loss,
            "brier_score": brier,
            "top_pick_hit_rate": top1,
            "top3_hit_rate": top3,
            "value_roi": roi,
            "max_drawdown": drawdown,
        },
    }
