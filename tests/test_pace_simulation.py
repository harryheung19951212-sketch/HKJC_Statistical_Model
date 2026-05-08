from racing_model.pace import annotate_predictions_with_pace, build_pace_map, exotic_pace_payload


def test_pace_map_projects_positions_and_race_shape() -> None:
    predictions = [
        runner("H001", 1, "leader", 0.32),
        runner("H002", 8, "pace", 0.22),
        runner("H003", 3, "closer", 0.20),
        runner("H004", 6, "midfield", 0.14),
        runner("H005", 10, "leader", 0.08),
        runner("H006", 2, "closer", 0.04),
    ]

    pace_map = annotate_predictions_with_pace(predictions)

    assert pace_map["race_shape"]["shape"] == "fast"
    assert pace_map["race_shape"]["leader_count"] == 3
    assert predictions[0]["pace_projected_position"] <= 2
    assert predictions[2]["pace_role"] in {"midfield", "closer"}
    assert "pace_note" in predictions[0]
    assert predictions[0]["pace_shape"] == "fast"


def test_inside_midpack_runner_gets_traffic_risk() -> None:
    predictions = [
        runner("H001", 9, "leader", 0.26),
        runner("H002", 8, "pace", 0.23),
        runner("H003", 1, "closer", 0.21),
        runner("H004", 2, "midfield", 0.16),
        runner("H005", 5, "closer", 0.09),
        runner("H006", 6, "midfield", 0.05),
    ]

    annotate_predictions_with_pace(predictions)
    inside_closer = next(row for row in predictions if row["horse_id"] == "H003")

    assert inside_closer["traffic_risk"] >= 0.34
    assert inside_closer["traffic_risk_label"] in {"中", "高"}


def test_exotic_pace_payload_scores_order_fit() -> None:
    predictions = [
        runner("H001", 1, "leader", 0.34),
        runner("H002", 3, "midfield", 0.24),
        runner("H003", 6, "closer", 0.18),
    ]
    annotate_predictions_with_pace(predictions)
    runner_by_id = {row["horse_id"]: row for row in predictions}

    good_order = exotic_pace_payload(["H001", "H002", "H003"], runner_by_id, True)
    poor_order = exotic_pace_payload(["H003", "H002", "H001"], runner_by_id, True)

    assert good_order["pace_order_fit"] > poor_order["pace_order_fit"]
    assert good_order["pace_fit_score"] > poor_order["pace_fit_score"]
    assert "節奏" in good_order["pace_note"]


def test_pace_map_uses_historical_profile_signals() -> None:
    predictions = [
        runner("H001", 1, "unknown", 0.20) | {
            "early_speed_profile": 0.92,
            "pace_advantage_score": 0.18,
            "distance_pace_fit": 0.12,
        },
        runner("H002", 8, "leader", 0.22) | {
            "early_speed_profile": 0.30,
            "traffic_risk_score": 0.45,
            "pace_fade_score": 0.40,
        },
        runner("H003", 4, "closer", 0.18) | {
            "early_speed_profile": 0.12,
            "closing_gain_score": 0.55,
        },
    ]

    annotate_predictions_with_pace(predictions)
    early_profile = next(row for row in predictions if row["horse_id"] == "H001")
    risky_leader = next(row for row in predictions if row["horse_id"] == "H002")

    assert early_profile["pace_projected_position"] == 1
    assert early_profile["pace_v2_advantage_signal"] > 0
    assert risky_leader["traffic_risk"] >= 0.34


def test_build_pace_map_handles_empty_predictions() -> None:
    assert build_pace_map([])["race_shape"]["shape"] == "unknown"


def runner(horse_id: str, draw: int, style: str, win_probability: float) -> dict[str, object]:
    return {
        "horse_id": horse_id,
        "horse_no": int(horse_id[-1]),
        "display_name": f"Runner {horse_id}",
        "draw": draw,
        "running_style": style,
        "win_probability": win_probability,
        "top3_probability": min(win_probability * 2.2, 0.75),
        "latest_win_odds": 1 / max(win_probability, 0.01),
        "latest_win_odds_source": "hkjc_graphql",
        "place_odds": 2.0,
        "place_odds_source": "hkjc_graphql",
    }
