from racing_model.betting import build_betting_decisions, market_label
from racing_model.pool_rules import POOL_RULES, required_expected_value


def test_betting_decision_uses_fractional_kelly_and_race_cap() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "測試馬",
            "display_jockey": "騎師",
            "display_trainer": "練馬師",
            "win_probability": 0.35,
            "latest_win_odds": 4.0,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": 0.62,
            "place_odds": 2.2,
            "place_odds_source": "hkjc_mqtt",
        },
        *[
            {
                "horse_id": f"H00{index}",
                "horse_no": index,
                "display_name": f"Runner {index}",
                "win_probability": probability,
                "latest_win_odds": None,
                "latest_win_odds_source": None,
                "top3_probability": min(probability * 3, 0.4),
                "place_odds": None,
                "place_odds_source": None,
            }
            for index, probability in enumerate([0.20, 0.16, 0.12, 0.10, 0.07], start=2)
        ],
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    assert result["tickets"]
    assert result["total_recommended_stake"] <= result["max_race_stake"]
    assert result["risk_settings"]["base_fractional_kelly"] == 0.25
    assert result["risk_settings"]["fractional_kelly"] != 0.25
    assert result["risk_settings"]["kelly_label"] in {"正常", "降注", "保守觀望"}
    assert {ticket["market"] for ticket in result["tickets"]} == {"WIN", "PLACE"}
    assert all(ticket["action"] == "有值博" for ticket in result["tickets"])
    exotic_markets = {candidate["market"] for candidate in result["exotic_candidates"]}
    assert {"QPL", "TRIO", "QIN", "FCT", "TCE", "FIRST4", "QUARTET"}.issubset(exotic_markets)
    assert all("recommended_stake" in candidate for candidate in result["exotic_candidates"])
    assert all(candidate["minimum_ticket_cost"] > 0 for candidate in result["exotic_candidates"])
    assert all(candidate["structure_label"] for candidate in result["exotic_candidates"])
    assert all("pace_note" in candidate for candidate in result["exotic_candidates"])
    assert result["pace_map"]["race_shape"]["runner_count"] == len(predictions)
    assert "banker_leg_suggestions" not in result


def test_resulted_race_is_review_only() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "測試馬",
            "win_probability": 0.50,
            "latest_win_odds": 5.0,
            "latest_win_odds_source": "hkjc_results_final",
            "top3_probability": 0.80,
            "place_odds": 2.0,
            "place_odds_source": "hkjc_results_final",
        }
    ]

    result = build_betting_decisions(predictions, "resulted", bankroll=10000, risk_profile="aggressive")

    assert result["tickets"] == []
    assert all(decision["recommended_stake"] == 0 for decision in result["decisions"])
    assert {decision["action"] for decision in result["decisions"]} == {"只供回測"}


def test_eligible_stakes_use_minimum_ticket_but_scale_with_confidence() -> None:
    low_bankroll = build_betting_decisions(
        [
            {
                "horse_id": "H001",
                "horse_no": 1,
                "display_name": "細注馬",
                "win_probability": 0.22,
                "latest_win_odds": 6.0,
                "latest_win_odds_source": "hkjc_mqtt",
                "top3_probability": 0.35,
                "place_odds": None,
                "place_odds_source": None,
            }
        ],
        "scheduled",
        bankroll=900,
        risk_profile="standard",
        include_exotics=False,
    )
    confident = build_betting_decisions(
        [
            {
                "horse_id": "H002",
                "horse_no": 2,
                "display_name": "重注馬",
                "win_probability": 0.50,
                "latest_win_odds": 4.0,
                "latest_win_odds_source": "hkjc_mqtt",
                "top3_probability": 0.75,
                "place_odds": None,
                "place_odds_source": None,
            }
        ],
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        include_exotics=False,
    )

    assert low_bankroll["tickets"][0]["recommended_stake"] == 10.0
    assert confident["tickets"][0]["recommended_stake"] > 10.0


def test_trio_upgrade_path_compares_position_q_pairs() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    assert result["upgrade_paths"]
    top_path = result["upgrade_paths"][0]
    assert top_path["to_label"] == "單T"
    assert len(top_path["from_markets"]) == 2
    assert top_path["trio_break_even_dividend"] > 1
    trio_candidate = next(row for row in result["exotic_candidates"] if row["market"] == "TRIO")
    assert trio_candidate["structure_label"] == "不做膽腳"
    assert trio_candidate["recommended_stake"] == 0.0
    assert "banker_leg_suggestions" not in result


def test_exotic_candidate_structure_does_not_drag_too_many_legs() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.30, 0.22, 0.16, 0.12, 0.08, 0.06, 0.04, 0.02], start=1)
    ]
    dividends = {
        ("FIRST4", "1+2+3+4"): {
            "dividend": 40.0,
            "dividend_status": "probable",
            "source": "manual_test",
        }
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    first4 = next(row for row in result["exotic_candidates"] if row["market"] == "FIRST4" and row["combination_key"] == "1+2+3+4")
    assert first4["structure_label"] == "複式"
    assert first4["bankers"] == []
    assert len(first4["legs"]) == 4
    assert first4["combination_count"] == 1
    assert first4["minimum_ticket_cost"] == 10.0
    assert first4["recommended_stake"] >= 0.0


def test_trio_exact_three_selection_is_box_not_banker_leg() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.42, 0.20, 0.16, 0.10, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("TRIO", "1+2+3"): {
            "dividend": 120.0,
            "dividend_status": "probable",
            "source": "manual_test",
        }
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    trio = next(row for row in result["exotic_candidates"] if row["market"] == "TRIO" and row["combination_key"] == "1+2+3")
    assert trio["structure_label"] == "複式"
    assert trio["bankers"] == []
    assert trio["leg_ids"] == ["H001", "H002", "H003"]
    assert trio["combination_count"] == 1


def test_exotic_dividend_turns_candidate_into_ev_ticket() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("QPL", "1+2"): {
            "dividend": 20.0,
            "dividend_status": "probable",
            "source": "manual_test",
        }
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    qpl = next(ticket for ticket in result["tickets"] if ticket["market"] == "QPL")
    assert qpl["horse_id"] == "1+2"
    assert qpl["expected_value"] > 0
    assert qpl["cost_adjusted_expected_value"] < qpl["expected_value"]
    assert qpl["required_dividend"] > qpl["break_even_dividend"]
    assert qpl["pool_rule"]["takeout_rate"] > 0
    assert qpl["recommended_stake"] > 0
    qpl_candidate = next(row for row in result["exotic_candidates"] if row["market"] == "QPL" and row["combination_key"] == "1+2")
    assert qpl_candidate["recommended_stake"] == qpl["recommended_stake"]
    assert qpl_candidate["stake_action"] == "有值博"
    assert qpl_candidate["structure_label"] == "複式"
    assert qpl_candidate["per_combination_stake"] == qpl_candidate["recommended_stake"]
    assert result["pool_choice"]["summary"]["actionable_markets"] >= 1
    qpl_pool = next(row for row in result["pool_choice"]["markets"] if row["market"] == "QPL")
    assert qpl_pool["best_cost_adjusted_expected_value"] > 0
    assert qpl_pool["takeout_rate"] > 0


def test_ordered_exotic_box_counts_all_permutation_tickets() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"Runner {index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("TCE", "1>2>3"): {
            "dividend": 120.0,
            "dividend_status": "probable",
            "source": "manual_test",
        }
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    tce = next(row for row in result["exotic_candidates"] if row["market"] == "TCE" and row["combination_key"] == "1>2>3")
    assert tce["structure_label"] == "複式"
    assert tce["combination_count"] == 6
    assert tce["bankers"] == []
    assert tce["pace_order_fit"] is not None


def test_pool_choice_prefers_higher_ev_upgrade_pool_when_available() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"Runner {index}",
            "win_probability": probability,
            "latest_win_odds": 4.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.6,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("QPL", "1+2"): {
            "dividend": 8.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
        ("TRIO", "1+2+3"): {
            "dividend": 80.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    pool_choice = result["pool_choice"]
    trio = next(row for row in pool_choice["markets"] if row["market"] == "TRIO")
    qpl = next(row for row in pool_choice["markets"] if row["market"] == "QPL")
    assert trio["best_cost_adjusted_expected_value"] > qpl["best_cost_adjusted_expected_value"]
    assert pool_choice["summary"]["best_market"] in {"TRIO", "QPL"}
    assert any("位置Q" in item["title"] for item in pool_choice["recommendations"])


def test_pool_choice_uses_readable_win_place_labels_and_actionable_count() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "測試馬",
            "win_probability": 0.5,
            "latest_win_odds": 4.0,
            "latest_win_odds_source": "hkjc_graphql",
            "top3_probability": 0.7,
            "place_odds": 2.2,
            "place_odds_source": "hkjc_graphql",
        }
    ]

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        include_exotics=False,
    )
    win = next(row for row in result["pool_choice"]["markets"] if row["market"] == "WIN")
    place = next(row for row in result["pool_choice"]["markets"] if row["market"] == "PLACE")

    assert market_label("WIN") == "獨贏"
    assert market_label("PLACE") == "位置"
    assert win["market_label"] == "獨贏"
    assert place["market_label"] == "位置"
    assert win["actionable_count"] == 1
    assert place["actionable_count"] == 1


def test_bet_slip_engine_ranks_tickets_and_builds_strategy_options() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"Runner {index}",
            "win_probability": probability,
            "latest_win_odds": 4.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.6 + index * 0.05,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("QPL", "1+2"): {
            "dividend": 16.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
        ("TRIO", "1+2+3"): {
            "dividend": 80.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    slip = result["bet_slip"]
    assert slip["summary"]["status"] == "actionable"
    assert slip["summary"]["ticket_count"] == len(result["tickets"])
    assert slip["summary"]["expected_profit"] > 0
    assert slip["summary"]["at_least_one_hit_probability"] > 0
    assert {row["strategy"] for row in slip["strategies"]} == {"conservative", "standard", "aggressive"}
    assert next(row for row in slip["strategies"] if row["strategy"] == "standard")["is_current"] is True
    assert [row["slip_rank"] for row in slip["tickets"]] == list(range(1, len(slip["tickets"]) + 1))
    assert all(row["portfolio_role"] for row in slip["tickets"])
    assert all("slip_rank" in ticket for ticket in result["tickets"])
    assert any(row["portfolio_role"] == "leverage" for row in slip["tickets"])


def test_correlated_exposure_reduces_shared_horse_and_leg_stakes() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 8.0,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 3.0,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("QPL", "1+2"): {
            "dividend": 30.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
        ("QIN", "1+2"): {
            "dividend": 40.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
        ("FCT", "1>2"): {
            "dividend": 40.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
        ("TRIO", "1+2+3"): {
            "dividend": 80.0,
            "dividend_status": "probable",
            "source": "manual_test",
        },
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    report = result["exposure_report"]
    assert result["exposure_adjusted"] is True
    assert report["summary"]["adjusted_count"] > 0
    assert report["summary"]["breach_count"] == 0
    assert any("同馬曝險" in row["reason"] for row in report["adjusted_tickets"])
    assert max(row["stake"] for row in report["after"]["horse"]) <= report["caps"]["horse"]
    qpl = next(ticket for ticket in result["tickets"] if ticket["market"] == "QPL" and ticket["horse_id"] == "1+2")
    assert qpl["exposure_action"] == "降注"
    assert qpl["recommended_stake"] > 0
    qpl_candidate = next(row for row in result["exotic_candidates"] if row["market"] == "QPL" and row["combination_key"] == "1+2")
    assert qpl_candidate["exposure_action"] == "降注"
    assert qpl_candidate["exposure_reason"]


def test_pool_cost_gate_rejects_small_nominal_edge() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "Cost Gate",
            "win_probability": 0.30,
            "latest_win_odds": 3.52,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": 0.50,
            "place_odds": 1.95,
            "place_odds_source": "hkjc_mqtt",
        }
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")
    win = next(row for row in result["decisions"] if row["market"] == "WIN")

    assert win["expected_value"] > 0
    assert win["expected_value"] < win["required_expected_value"]
    assert win["recommended_stake"] == 0
    assert win["cost_adjusted_expected_value"] == win["expected_value"] - POOL_RULES["WIN"].efficiency_buffer


def test_calibration_gate_reduces_recommended_stakes() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "校準馬",
            "win_probability": 0.35,
            "latest_win_odds": 4.0,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": 0.62,
            "place_odds": 2.2,
            "place_odds_source": "hkjc_mqtt",
        }
    ]

    full = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard", include_exotics=False)
    gated = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        include_exotics=False,
        calibration_gate={
            "status": "blocked",
            "label": "校準未過關",
            "message": "測試校準偏差，注碼降至四分之一。",
            "stake_factor": 0.25,
            "promote_allowed": False,
        },
    )

    assert gated["calibration_gate"]["status"] == "blocked"
    assert gated["calibration_adjustments"]
    assert gated["total_recommended_stake"] < full["total_recommended_stake"]
    assert all(ticket["calibration_stake_factor"] == 0.25 for ticket in gated["tickets"])
    assert all("校準偏差" in ticket["reason"] for ticket in gated["tickets"])


def test_pool_rules_cover_all_betting_markets() -> None:
    assert set(POOL_RULES) == {"WIN", "PLACE", "QIN", "QPL", "FCT", "TRIO", "TCE", "FIRST4", "QUARTET"}
    assert POOL_RULES["WIN"].payout_rate == 0.825
    assert POOL_RULES["FCT"].payout_rate == 0.805
    assert POOL_RULES["TRIO"].payout_rate == 0.770
    assert POOL_RULES["TCE"].payout_rate == 0.750
    assert all(rule.min_unit == 10.0 for rule in POOL_RULES.values())
    assert required_expected_value("QUARTET", 0.05) > required_expected_value("WIN", 0.05)
