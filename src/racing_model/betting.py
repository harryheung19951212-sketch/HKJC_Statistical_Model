from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, permutations
from math import factorial
from typing import Any

from .pool_rules import (
    all_pool_rules_payload,
    cost_adjusted_expected_value,
    pool_rule_payload,
    required_dividend,
    required_edge,
    required_expected_value,
    round_stake_to_unit,
)
from .pace import annotate_predictions_with_pace, exotic_pace_payload


LIVE_ODDS_SOURCES = {"hkjc_graphql", "hkjc_mqtt"}
FINAL_DIVIDEND_SOURCES = {"hkjc_results_final"}

EXOTIC_PRODUCTS = {
    "QIN": {"label": "連贏", "size": 2, "ordered": False, "top_k": 2},
    "QPL": {"label": "位置Q", "size": 2, "ordered": False, "top_k": 3},
    "FCT": {"label": "二重彩", "size": 2, "ordered": True, "top_k": 2},
    "TRIO": {"label": "單T", "size": 3, "ordered": False, "top_k": 3},
    "TCE": {"label": "三重彩", "size": 3, "ordered": True, "top_k": 3},
    "FIRST4": {"label": "四連環", "size": 4, "ordered": False, "top_k": 4},
    "QUARTET": {"label": "四重彩", "size": 4, "ordered": True, "top_k": 4},
}


@dataclass(frozen=True)
class RiskProfile:
    name: str
    fractional_kelly: float
    max_bet_fraction: float
    max_race_fraction: float
    min_expected_value: float
    min_edge: float


RISK_PROFILES = {
    "conservative": RiskProfile("conservative", 0.125, 0.005, 0.015, 0.08, 0.03),
    "standard": RiskProfile("standard", 0.25, 0.01, 0.03, 0.05, 0.02),
    "aggressive": RiskProfile("aggressive", 0.50, 0.02, 0.06, 0.03, 0.01),
}


def build_betting_decisions(
    predictions: list[dict[str, Any]],
    race_status: str,
    bankroll: float = 10_000.0,
    risk_profile: str = "standard",
    exotic_dividends: dict[tuple[str, str], dict[str, Any]] | None = None,
    include_exotics: bool = True,
    calibration_gate: dict[str, Any] | None = None,
    pool_replay_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = RISK_PROFILES.get(risk_profile, RISK_PROFILES["standard"])
    bankroll = max(float(bankroll or 0), 0.0)
    pace_map = annotate_predictions_with_pace(predictions)
    kelly_context = adaptive_kelly_context(predictions, profile, race_status, calibration_gate)
    effective_fractional_kelly = float(kelly_context["fractional_kelly"])
    decisions = []
    for rank, prediction in enumerate(predictions, start=1):
        decisions.append(
            build_market_decision(
                prediction,
                rank,
                market="WIN",
                probability_key="win_probability",
                odds_key="latest_win_odds",
                source_key="latest_win_odds_source",
                race_status=race_status,
                bankroll=bankroll,
                profile=profile,
                fractional_kelly=effective_fractional_kelly,
            )
        )
        decisions.append(
            build_market_decision(
                prediction,
                rank,
                market="PLACE",
                probability_key="top3_probability",
                odds_key="place_odds",
                source_key="place_odds_source",
                race_status=race_status,
                bankroll=bankroll,
                profile=profile,
                fractional_kelly=effective_fractional_kelly,
            )
        )

    exotic_candidates = build_exotic_candidates(predictions, race_status, exotic_dividends=exotic_dividends) if include_exotics else []
    exotic_decisions = build_exotic_decisions(exotic_candidates, race_status, bankroll, profile, effective_fractional_kelly) if include_exotics else []
    calibration_adjustments = apply_calibration_stake_gate([*decisions, *exotic_decisions], bankroll, calibration_gate)
    pool_replay_adjustments = apply_pool_replay_stake_gate([*decisions, *exotic_decisions], bankroll, pool_replay_gate)
    active = [decision for decision in [*decisions, *exotic_decisions] if decision["recommended_stake"] > 0]
    max_race_stake = round(bankroll * profile.max_race_fraction, 2)
    raw_total = sum(float(item["recommended_stake"]) for item in active)
    scale = 1.0

    all_decisions = [*decisions, *exotic_decisions]
    exposure_report = apply_correlated_exposure_controls(all_decisions, bankroll, profile)
    annotate_exotic_candidate_stakes(exotic_candidates, exotic_decisions)
    decisions.sort(
        key=lambda item: (
            float(item["recommended_stake"]),
            float(item["expected_value"] or -99),
            float(item["probability"] or 0),
        ),
        reverse=True,
    )
    tickets = [decision for decision in all_decisions if decision["recommended_stake"] > 0]
    pool_choice = build_pool_choice_scorecard(all_decisions, exotic_candidates, tickets, pool_replay_gate=pool_replay_gate)
    bet_slip = build_bet_slip(tickets, pool_choice, exposure_report, bankroll, profile, race_status)
    return {
        "race_status": race_status,
        "bankroll": bankroll,
        "risk_profile": profile.name,
        "risk_settings": {
            "fractional_kelly": effective_fractional_kelly,
            "base_fractional_kelly": profile.fractional_kelly,
            "kelly_modifier": kelly_context["modifier"],
            "kelly_label": kelly_context["label"],
            "kelly_reasons": kelly_context["reasons"],
            "max_bet_fraction": profile.max_bet_fraction,
            "max_race_fraction": profile.max_race_fraction,
            "min_expected_value": profile.min_expected_value,
            "min_edge": profile.min_edge,
            "calibration_stake_factor": calibration_factor(calibration_gate),
        },
        "calibration_gate": calibration_gate or default_calibration_gate(),
        "calibration_adjustments": calibration_adjustments,
        "pool_replay_gate": pool_replay_gate or default_pool_replay_gate(),
        "pool_replay_adjustments": pool_replay_adjustments,
        "pool_rules": all_pool_rules_payload(),
        "max_race_stake": max_race_stake,
        "total_recommended_stake": round(sum(float(item["recommended_stake"]) for item in tickets), 1),
        "stake_scaled": scale < 1.0,
        "exposure_adjusted": bool(exposure_report["adjusted_tickets"]),
        "exposure_report": exposure_report,
        "tickets": tickets,
        "decisions": decisions,
        "exotic_decisions": exotic_decisions,
        "exotic_candidates": exotic_candidates,
        "pool_choice": pool_choice,
        "bet_slip": bet_slip,
        "pace_map": pace_map,
        "upgrade_paths": build_upgrade_paths(exotic_candidates),
        "exotics_deferred": not include_exotics,
    }


def apply_calibration_stake_gate(
    decisions: list[dict[str, Any]],
    bankroll: float,
    gate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    factor = calibration_factor(gate)
    if factor >= 0.999:
        return []
    adjustments = []
    for decision in decisions:
        original_stake = float(decision.get("recommended_stake") or 0.0)
        decision["calibration_gate_status"] = (gate or {}).get("status", "pass")
        decision["calibration_gate_label"] = (gate or {}).get("label", "校準通過")
        decision["calibration_stake_factor"] = factor
        if original_stake <= 0:
            continue
        market = str(decision.get("market") or "WIN")
        new_stake = round_stake_to_unit(original_stake * factor, market)
        decision["recommended_stake"] = new_stake
        decision["stake_fraction"] = round(new_stake / bankroll, 6) if bankroll else 0.0
        decision["reason"] = f"{decision.get('reason') or '符合條件'}；{(gate or {}).get('message', '校準 gate 降注')}"
        if new_stake <= 0:
            decision["action"] = "觀望"
        adjustments.append(
            {
                "market": decision.get("market"),
                "horse_id": decision.get("horse_id"),
                "horse_name": decision.get("horse_name"),
                "original_stake": round(original_stake, 1),
                "adjusted_stake": round(new_stake, 1),
                "factor": factor,
                "reason": (gate or {}).get("message", "校準 gate 降注"),
            }
        )
    return adjustments


def calibration_factor(gate: dict[str, Any] | None) -> float:
    if not gate:
        return 1.0
    try:
        return max(0.0, min(float(gate.get("stake_factor", 1.0) or 1.0), 1.0))
    except (TypeError, ValueError):
        return 1.0


def default_calibration_gate() -> dict[str, Any]:
    return {
        "status": "pass",
        "label": "校準通過",
        "message": "未提供額外校準 gate，按原本風險設定計注。",
        "stake_factor": 1.0,
        "promote_allowed": True,
    }


def apply_pool_replay_stake_gate(
    decisions: list[dict[str, Any]],
    bankroll: float,
    gate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    adjustments = []
    for decision in decisions:
        market = str(decision.get("market") or "")
        market_gate = pool_replay_market_context(gate, market)
        factor = pool_replay_stake_factor(market_gate)
        optimizer_factor, optimizer_factor_reason = pool_choice_optimizer_stake_adjustment(market_gate)
        decision["pool_replay_gate_status"] = market_gate.get("status", "unverified")
        decision["pool_replay_gate_label"] = market_gate.get("label", "Replay 未驗證")
        decision["pool_replay_gate_reason"] = market_gate.get("reason", "未有分池 replay 校準。")
        decision["pool_replay_stake_factor"] = factor
        decision["pool_choice_optimizer_stake_factor"] = optimizer_factor
        decision["pool_choice_optimizer_stake_reason"] = optimizer_factor_reason
        decision["pool_replay_sample_size"] = market_gate.get("reconciled", 0)
        decision["pool_replay_min_samples"] = market_gate.get("min_samples")
        decision["pool_replay_roi"] = market_gate.get("roi")
        decision["pool_replay_execution_roi"] = market_gate.get("execution_roi")
        decision["pool_replay_context_status"] = market_gate.get("context_status")
        decision["pool_replay_context_segment_label"] = market_gate.get("context_segment_label")
        decision["pool_replay_context_sample_size"] = market_gate.get("context_sample_size")
        decision["pool_replay_context_min_samples"] = market_gate.get("context_min_samples")
        decision["pool_replay_context_roi"] = market_gate.get("context_roi")
        decision["pool_choice_optimizer_status"] = market_gate.get("optimizer_status")
        decision["pool_choice_optimizer_policy"] = market_gate.get("optimizer_policy")
        decision["pool_choice_optimizer_reason"] = market_gate.get("optimizer_reason")
        decision["pool_choice_optimizer_delta_roi"] = market_gate.get("optimizer_delta_roi")
        decision["pool_choice_optimizer_baseline_max_drawdown"] = market_gate.get("optimizer_baseline_max_drawdown")
        decision["pool_choice_optimizer_gated_max_drawdown"] = market_gate.get("optimizer_gated_max_drawdown")
        decision["pool_choice_optimizer_retention_rate"] = market_gate.get("optimizer_retention_rate")
        original_stake = float(decision.get("recommended_stake") or 0.0)
        if original_stake <= 0 or abs(factor - 1.0) <= 0.001:
            continue
        new_stake = round_stake_to_unit(original_stake * factor, market)
        if factor <= 0.0:
            new_stake = 0.0
        decision["recommended_stake"] = new_stake
        decision["stake_fraction"] = round(new_stake / bankroll, 6) if bankroll else 0.0
        reason_parts = [decision["pool_replay_gate_reason"]]
        if optimizer_factor_reason:
            reason_parts.append(optimizer_factor_reason)
        decision["reason"] = f"{decision.get('reason') or '符合條件'}；{'；'.join(reason_parts)}"
        if new_stake <= 0:
            decision["action"] = "觀望"
        adjustments.append(
            {
                "market": market,
                "horse_id": decision.get("horse_id"),
                "horse_name": decision.get("horse_name"),
                "original_stake": round(original_stake, 1),
                "adjusted_stake": round(new_stake, 1),
                "factor": factor,
                "optimizer_factor": optimizer_factor,
                "status": market_gate.get("status", "unverified"),
                "reason": "；".join(reason_parts),
            }
        )
    return adjustments


def pool_replay_market_context(gate: dict[str, Any] | None, market: str) -> dict[str, Any]:
    if not gate:
        return {}
    markets = gate.get("markets") if isinstance(gate, dict) else None
    if isinstance(markets, dict):
        row = markets.get(str(market).upper())
        if isinstance(row, dict):
            return row
    return {}


def pool_replay_stake_factor(market_gate: dict[str, Any] | None) -> float:
    if not market_gate:
        return 1.0
    try:
        value = market_gate.get("stake_factor", 1.0)
        base = max(0.0, min(float(1.0 if value is None else value), 1.0))
    except (TypeError, ValueError):
        base = 1.0
    optimizer_factor, _ = pool_choice_optimizer_stake_adjustment(market_gate)
    return round(max(0.0, min(base * optimizer_factor, 1.15)), 4)


def pool_choice_optimizer_stake_adjustment(market_gate: dict[str, Any] | None) -> tuple[float, str]:
    if not market_gate:
        return 1.0, ""
    status = str(market_gate.get("optimizer_status") or "no_sample")
    policy = str(market_gate.get("optimizer_policy") or "collect")
    if policy == "block":
        return 0.0, "walk-forward 彩池 optimizer 封池，注碼降至 0。"
    if policy == "reduce":
        return 0.5, "walk-forward 彩池 optimizer 要求降注。"
    if status != "pass" or policy not in {"pass", "watch"}:
        return 1.0, ""
    delta_roi = safe_float(market_gate.get("optimizer_delta_roi"))
    retention = safe_float(market_gate.get("optimizer_retention_rate"))
    baseline_drawdown = safe_float(market_gate.get("optimizer_baseline_max_drawdown"))
    gated_drawdown = safe_float(market_gate.get("optimizer_gated_max_drawdown"))
    if delta_roi is None or delta_roi <= 0:
        return 1.0, ""
    drawdown_ok = drawdown_not_worse(gated_drawdown, baseline_drawdown)
    retention_ok = retention is None or retention >= 0.55
    if not drawdown_ok or not retention_ok:
        return 1.0, ""
    factor = 1.0 + min(delta_roi, 0.30) * 0.5
    factor = round(min(factor, 1.15), 4)
    if factor <= 1.001:
        return 1.0, ""
    return factor, f"walk-forward 彩池 optimizer ROI 改善 {delta_roi:.1%}，回撤未惡化，注碼係數 {factor:.2f}。"


def drawdown_not_worse(value: float | None, baseline: float | None) -> bool:
    if value is None or baseline is None:
        return True
    return abs(value) <= abs(baseline) * 1.10 + 1e-9


def default_pool_replay_gate() -> dict[str, Any]:
    return {
        "status": "unverified",
        "label": "分彩池 replay 未啟用",
        "message": "未提供分池 replay gate，注碼只按即場 EV、校準及曝險規則處理。",
        "markets": {},
        "blocked_markets": [],
        "reduced_markets": [],
        "sample_building_markets": [],
    }


def build_market_decision(
    prediction: dict[str, Any],
    rank: int,
    market: str,
    probability_key: str,
    odds_key: str,
    source_key: str,
    race_status: str,
    bankroll: float,
    profile: RiskProfile,
    fractional_kelly: float,
) -> dict[str, Any]:
    probability = safe_float(prediction.get(probability_key))
    odds = safe_float(prediction.get(odds_key))
    source = prediction.get(source_key)
    market_fallback_no_edge = bool(prediction.get("market_fallback_no_edge"))
    fair_odds = (1.0 / probability) if probability and probability > 0 else None
    market_probability = (1.0 / odds) if odds and odds > 1 else None
    edge = probability - market_probability if market_probability is not None else None
    expected_value = probability * odds - 1.0 if odds and odds > 1 else None
    adjusted_ev = cost_adjusted_expected_value(expected_value, market)
    req_ev = required_expected_value(market, profile.min_expected_value)
    req_edge = required_edge(market, profile.min_edge)
    req_dividend = required_dividend(probability, market, profile.min_expected_value)
    raw_kelly = kelly_fraction(probability, odds)
    kelly = raw_kelly * fractional_kelly
    capped_fraction = min(kelly, profile.max_bet_fraction)
    eligible, reason = decision_eligibility(
        race_status,
        market,
        source,
        odds,
        expected_value,
        adjusted_ev,
        edge,
        req_ev,
        req_edge,
    )
    if market_fallback_no_edge and market in {"WIN", "PLACE"}:
        eligible = False
        reason = "同分熔斷只計市場公平EV，未有模型edge"
    action = action_label(eligible, expected_value, edge, profile, reason)
    stake_fraction = capped_fraction if action == "有值博" else 0.0
    stake = stake_from_fraction(bankroll, stake_fraction, market) if stake_fraction > 0 else 0.0
    return {
        "market": market,
        "market_label": "獨贏" if market == "WIN" else "位置",
        "horse_id": prediction.get("horse_id"),
        "horse_no": prediction.get("horse_no"),
        "horse_name": prediction.get("display_name") or prediction.get("horse_name_zh") or prediction.get("horse_name"),
        "jockey": prediction.get("display_jockey") or prediction.get("jockey"),
        "trainer": prediction.get("display_trainer") or prediction.get("trainer"),
        "model_rank": rank,
        "probability": round(probability, 6) if probability is not None else None,
        "odds": odds,
        "odds_source": source,
        "fair_odds": round(fair_odds, 3) if fair_odds else None,
        "market_probability": round(market_probability, 6) if market_probability is not None else None,
        "edge": round(edge, 6) if edge is not None else None,
        "expected_value": round(expected_value, 6) if expected_value is not None else None,
        "expected_value_source": prediction.get("expected_value_source") or "model_probability",
        "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
        "required_expected_value": round(req_ev, 6),
        "required_edge": round(req_edge, 6),
        "required_dividend": round(req_dividend, 3) if req_dividend else None,
        "pool_rule": pool_rule_payload(market),
        "combination_count": 1,
        "minimum_ticket_cost": pool_rule_payload(market)["min_unit"],
        "kelly_fraction": round(max(raw_kelly, 0.0), 6),
        "fractional_kelly": round(max(kelly, 0.0), 6),
        "stake_fraction": round(stake_fraction, 6),
        "recommended_stake": stake,
        "action": action,
        "reason": reason,
        "model_edge_available": not market_fallback_no_edge,
        "prediction_safeguard": prediction.get("prediction_safeguard"),
        "prediction_safeguard_reason": prediction.get("prediction_safeguard_reason"),
        "market_fallback_no_edge": market_fallback_no_edge,
        "exposure_action": "保留",
        "exposure_reason": "未觸及相關曝險上限",
        "exposure_adjustment_factor": 1.0,
        "pace_projected_position": prediction.get("pace_projected_position"),
        "pace_role": prediction.get("pace_role"),
        "pace_role_label": prediction.get("pace_role_label"),
        "traffic_risk": prediction.get("traffic_risk"),
        "traffic_risk_label": prediction.get("traffic_risk_label"),
        "wide_risk": prediction.get("wide_risk"),
        "wide_risk_label": prediction.get("wide_risk_label"),
        "pace_advantage": prediction.get("pace_advantage"),
        "pace_note": prediction.get("pace_note"),
    }


def adaptive_kelly_context(
    predictions: list[dict[str, Any]],
    profile: RiskProfile,
    race_status: str,
    calibration_gate: dict[str, Any] | None,
) -> dict[str, Any]:
    if race_status in {"live", "resulted"}:
        return {
            "fractional_kelly": 0.0,
            "base_fractional_kelly": profile.fractional_kelly,
            "modifier": 0.0,
            "label": "停止下注",
            "reasons": ["賽事已開跑/完場"],
        }
    runners = max(len(predictions), 1)
    live_win = sum(1 for row in predictions if row.get("latest_win_odds_source") in LIVE_ODDS_SOURCES)
    live_place = sum(1 for row in predictions if row.get("place_odds_source") in LIVE_ODDS_SOURCES)
    odds_coverage = (live_win + live_place) / max(runners * 2, 1)
    ev_values = [safe_float(row.get("expected_value")) or -1.0 for row in predictions]
    top3_ev_values = [safe_float(row.get("top3_expected_value")) or -1.0 for row in predictions]
    best_ev = max([*ev_values, *top3_ev_values, -1.0])
    edge_strength = clamp_value((best_ev - profile.min_expected_value) / 0.40)
    gate_factor = calibration_factor(calibration_gate)
    modifier = 0.48 + 0.32 * odds_coverage + 0.20 * edge_strength
    if odds_coverage < 0.50:
        modifier *= 0.72
    modifier = clamp_value(modifier, 0.20, 1.20)
    fractional = round(profile.fractional_kelly * modifier, 4)
    if fractional >= profile.fractional_kelly * 0.95:
        label = "正常"
    elif fractional >= profile.fractional_kelly * 0.60:
        label = "降注"
    else:
        label = "保守觀望"
    reasons = [
        f"即時賠率覆蓋 {odds_coverage:.0%}",
        f"最佳EV {best_ev:.3f}" if best_ev > -1 else "未見正EV",
    ]
    if gate_factor < 0.999:
        reasons.append(f"另有校準 gate {gate_factor:.0%} 降注")
    return {
        "fractional_kelly": fractional,
        "base_fractional_kelly": profile.fractional_kelly,
        "modifier": round(modifier, 4),
        "label": label,
        "reasons": reasons,
    }


def decision_eligibility(
    race_status: str,
    market: str,
    source: object,
    odds: float | None,
    expected_value: float | None,
    adjusted_ev: float | None,
    edge: float | None,
    required_ev: float,
    required_edge_value: float,
    price_status: object | None = None,
) -> tuple[bool, str]:
    if not odds or odds <= 1:
        return False, "未有官方賠率"
    if race_status == "resulted":
        return False, "已完場，只作回測"
    if race_status == "live":
        return False, "已開跑，停止下注"
    if market in {"WIN", "PLACE"} and source not in LIVE_ODDS_SOURCES:
        return False, "未有官方實時賠率"
    if market not in {"WIN", "PLACE"}:
        if source in FINAL_DIVIDEND_SOURCES:
            return False, "賽果派彩，只作回測"
        if source not in LIVE_ODDS_SOURCES:
            return False, "未有官方組合彩池派彩"
        if str(price_status or "probable") != "probable":
            return False, "組合彩池派彩狀態未可落注"
    if source in FINAL_DIVIDEND_SOURCES:
        return False, "賽後賠率，只作回測"
    if expected_value is None or edge is None:
        return False, "資料不足"
    if expected_value < required_ev:
        return False, "期望值未達門檻"
    if adjusted_ev is None or adjusted_ev < 0:
        return False, "扣成本後EV未達門檻"
    if edge < required_edge_value:
        return False, "價值差未達門檻"
    return True, "符合 Kelly 下注條件"


def action_label(
    eligible: bool,
    expected_value: float | None,
    edge: float | None,
    profile: RiskProfile,
    reason: str,
) -> str:
    if eligible:
        return "有值博"
    if "回測" in reason:
        return "只供回測"
    if "停止下注" in reason:
        return "停止下注"
    if expected_value is not None and edge is not None and expected_value >= 0 and edge >= 0:
        return "觀望"
    return "避開"


def kelly_fraction(probability: float | None, odds: float | None) -> float:
    if probability is None or odds is None or odds <= 1:
        return 0.0
    edge = probability * odds - 1.0
    if edge <= 0:
        return 0.0
    return edge / (odds - 1.0)


def stake_from_fraction(
    bankroll: float,
    stake_fraction: float,
    market: str,
    minimum_ticket_cost: float | None = None,
) -> float:
    raw_stake = max(float(bankroll or 0.0) * max(float(stake_fraction or 0.0), 0.0), 0.0)
    if raw_stake <= 0:
        return 0.0
    minimum = minimum_ticket_cost if minimum_ticket_cost and minimum_ticket_cost > 0 else float(pool_rule_payload(market)["min_unit"])
    rounded = round_stake_to_unit(raw_stake, market)
    return max(float(minimum), rounded)


def clamp_value(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(float(value), upper))


def build_exotic_candidates(
    predictions: list[dict[str, Any]],
    race_status: str,
    max_runners: int = 6,
    exotic_dividends: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    ranked = list(predictions[:max_runners])
    probabilities = {
        str(row["horse_id"]): max(safe_float(row.get("win_probability")) or 0.0, 0.0)
        for row in predictions
    }
    total = sum(probabilities.values())
    if total > 0:
        probabilities = {horse_id: value / total for horse_id, value in probabilities.items()}
    runner_by_id = {str(row["horse_id"]): row for row in predictions}
    candidate_ids = [str(row["horse_id"]) for row in ranked]
    candidates = []
    for code, product in EXOTIC_PRODUCTS.items():
        product_candidates = []
        size = int(product["size"])
        if len(candidate_ids) < size:
            continue
        groups = permutations(candidate_ids, size) if product["ordered"] else combinations(candidate_ids, size)
        for group in groups:
            horse_ids = [str(horse_id) for horse_id in group]
            if product["ordered"]:
                probability = ordered_finish_probability(horse_ids, probabilities)
            else:
                probability = unordered_top_k_probability(horse_ids, int(product["top_k"]), probabilities)
            if probability <= 0:
                continue
            horse_numbers = [runner_by_id[horse_id].get("horse_no") for horse_id in horse_ids]
            combination_key = exotic_combination_key(code, horse_numbers)
            dividend_row = (exotic_dividends or {}).get((code, combination_key))
            dividend = safe_float(dividend_row.get("dividend")) if dividend_row else None
            dividend_quality = exotic_dividend_quality(dividend_row)
            expected_value = probability * dividend - 1.0 if dividend else None
            adjusted_ev = cost_adjusted_expected_value(expected_value, code)
            req_dividend = required_dividend(probability, code, RISK_PROFILES["standard"].min_expected_value)
            structure = exotic_structure_payload(
                code,
                str(product["label"]),
                horse_ids,
                runner_by_id,
                probabilities,
                bool(product["ordered"]),
                race_status,
                dividend,
                expected_value,
                adjusted_ev,
            )
            pace_payload = exotic_pace_payload(horse_ids, runner_by_id, bool(product["ordered"]))
            pace_adjusted_probability = probability * pace_probability_multiplier(pace_payload)
            unit = float(pool_rule_payload(code)["min_unit"])
            product_candidates.append(
                {
                    "market": code,
                    "market_label": product["label"],
                    "ordered": bool(product["ordered"]),
                    "horse_ids": horse_ids,
                    "horse_numbers": horse_numbers,
                    "horse_names": [
                        runner_by_id[horse_id].get("display_name")
                        or runner_by_id[horse_id].get("horse_name_zh")
                        or runner_by_id[horse_id].get("horse_name")
                        for horse_id in horse_ids
                    ],
                    "combination_key": combination_key,
                    "combination": format_combination(horse_ids, runner_by_id, bool(product["ordered"])),
                    "probability": round(probability, 6),
                    "pace_adjusted_probability": round(pace_adjusted_probability, 6),
                    "break_even_dividend": round(1.0 / probability, 2),
                    "required_dividend": round(req_dividend, 2) if req_dividend else None,
                    "dividend": dividend,
                    "dividend_status": dividend_row.get("dividend_status") if dividend_row else None,
                    "dividend_source": dividend_row.get("source") if dividend_row else None,
                    "dividend_quality": dividend_quality["quality"],
                    "dividend_quality_label": dividend_quality["label"],
                    "dividend_is_official": dividend_quality["is_official"],
                    "dividend_is_live": dividend_quality["is_live"],
                    "dividend_gate_reason": dividend_quality["reason"],
                    "expected_value": round(expected_value, 6) if expected_value is not None else None,
                    "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
                    "combination_count": structure["combination_count"],
                    "minimum_ticket_cost": round(unit * int(structure["combination_count"]), 1),
                    "recommended_stake": 0.0,
                    "per_combination_stake": 0.0,
                    "stake_reason": "未有官方派彩或未過EV門檻，建議不下注",
                    "action": exotic_action(race_status),
                    "reason": exotic_reason(race_status),
                    "pool_rule": pool_rule_payload(code),
                    **pace_payload,
                    **structure,
                }
            )
        product_candidates.sort(
            key=lambda item: (
                float(item.get("pace_adjusted_probability") or item["probability"]),
                float(item.get("pace_fit_score") or 0.0),
                -float(item.get("pace_risk_score") or 0.0),
            ),
            reverse=True,
        )
        candidates.extend(product_candidates[:12])
    candidates.sort(
        key=lambda item: (
            exotic_priority(str(item["market"])),
            float(item["probability"]),
            float(item.get("pace_fit_score") or 0.0),
        ),
        reverse=True,
    )
    return candidates


def pace_probability_multiplier(pace_payload: dict[str, Any]) -> float:
    fit = safe_float(pace_payload.get("pace_fit_score"))
    risk = safe_float(pace_payload.get("pace_risk_score")) or 0.0
    edge = safe_float(pace_payload.get("pace_edge_score")) or 0.0
    distance_fit = safe_float(pace_payload.get("pace_distance_fit")) or 0.0
    if fit is None:
        return 1.0
    multiplier = 0.92 + (fit * 0.16) + (max(edge, 0.0) * 0.08) + (max(distance_fit, 0.0) * 0.04) - (max(risk - 0.55, 0.0) * 0.12)
    return clamp_value(multiplier, 0.82, 1.14)


def exotic_dividend_quality(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "quality": "missing",
            "label": "未有官方派彩",
            "is_official": False,
            "is_live": False,
            "reason": "未有官方可能派彩，候選只可觀察。",
        }
    source = str(row.get("source") or "")
    status = str(row.get("dividend_status") or "")
    if source in LIVE_ODDS_SOURCES and status == "probable":
        return {
            "quality": "official_probable",
            "label": "官方即時派彩",
            "is_official": True,
            "is_live": True,
            "reason": "官方即時可能派彩，可進入下注 gate。",
        }
    if source in FINAL_DIVIDEND_SOURCES or status == "final":
        return {
            "quality": "final_result",
            "label": "賽果最終派彩",
            "is_official": True,
            "is_live": False,
            "reason": "賽果派彩只供回測/結算，不可臨場落注。",
        }
    if status == "estimated":
        return {
            "quality": "estimated",
            "label": "模型估算派彩",
            "is_official": False,
            "is_live": False,
            "reason": "估算派彩不可作臨場下注依據。",
        }
    return {
        "quality": "unverified",
        "label": "未核實派彩",
        "is_official": False,
        "is_live": False,
        "reason": "派彩來源未核實，只可用作分析，等官方價先落注。",
    }


def build_exotic_decisions(
    candidates: list[dict[str, Any]],
    race_status: str,
    bankroll: float,
    profile: RiskProfile,
    fractional_kelly: float,
) -> list[dict[str, Any]]:
    decisions = []
    for rank, candidate in enumerate(candidates, start=1):
        probability = safe_float(candidate.get("probability"))
        dividend = safe_float(candidate.get("dividend"))
        fair_odds = 1.0 / probability if probability and probability > 0 else None
        market_probability = 1.0 / dividend if dividend and dividend > 1 else None
        edge = probability - market_probability if probability is not None and market_probability is not None else None
        expected_value = probability * dividend - 1.0 if probability is not None and dividend else None
        adjusted_ev = cost_adjusted_expected_value(expected_value, str(candidate["market"]))
        req_ev = required_expected_value(str(candidate["market"]), profile.min_expected_value)
        req_edge = required_edge(str(candidate["market"]), profile.min_edge)
        req_dividend = required_dividend(probability, str(candidate["market"]), profile.min_expected_value)
        raw_kelly = kelly_fraction(probability, dividend)
        kelly = raw_kelly * fractional_kelly
        capped_fraction = min(kelly, profile.max_bet_fraction)
        eligible, reason = decision_eligibility(
            race_status,
            str(candidate["market"]),
            candidate.get("dividend_source"),
            dividend,
            expected_value,
            adjusted_ev,
            edge,
            req_ev,
            req_edge,
            candidate.get("dividend_status"),
        )
        action = action_label(eligible, expected_value, edge, profile, reason)
        stake_fraction = capped_fraction if action == "有值博" else 0.0
        stake = (
            stake_from_fraction(
                bankroll,
                stake_fraction,
                str(candidate["market"]),
                safe_float(candidate.get("minimum_ticket_cost")) or None,
            )
            if stake_fraction > 0
            else 0.0
        )
        decisions.append(
            {
                "market": candidate["market"],
                "market_label": candidate["market_label"],
                "horse_id": candidate["combination_key"],
                "horse_ids": candidate["horse_ids"],
                "horse_no": None,
                "horse_numbers": candidate["horse_numbers"],
                "horse_name": candidate["combination"],
                "horse_names": candidate["horse_names"],
                "model_rank": rank,
                "probability": round(probability, 6) if probability is not None else None,
                "odds": dividend,
                "odds_source": candidate.get("dividend_source"),
                "price_status": candidate.get("dividend_status"),
                "price_quality": candidate.get("dividend_quality"),
                "price_quality_label": candidate.get("dividend_quality_label"),
                "price_is_official": candidate.get("dividend_is_official"),
                "price_is_live": candidate.get("dividend_is_live"),
                "price_gate_reason": candidate.get("dividend_gate_reason"),
                "fair_odds": round(fair_odds, 3) if fair_odds else None,
                "market_probability": round(market_probability, 6) if market_probability is not None else None,
                "edge": round(edge, 6) if edge is not None else None,
                "expected_value": round(expected_value, 6) if expected_value is not None else None,
                "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
                "required_expected_value": round(req_ev, 6),
                "required_edge": round(req_edge, 6),
                "required_dividend": round(req_dividend, 3) if req_dividend else None,
                "pool_rule": pool_rule_payload(str(candidate["market"])),
                "combination_count": candidate.get("combination_count", 1),
                "minimum_ticket_cost": candidate.get("minimum_ticket_cost", pool_rule_payload(str(candidate["market"]))["min_unit"]),
                "kelly_fraction": round(max(raw_kelly, 0.0), 6),
                "fractional_kelly": round(max(kelly, 0.0), 6),
                "stake_fraction": round(stake_fraction, 6),
                "recommended_stake": stake,
                "action": action,
                "reason": reason,
                "exposure_action": "保留",
                "exposure_reason": "未觸及相關曝險上限",
                "exposure_adjustment_factor": 1.0,
                "break_even_dividend": candidate.get("break_even_dividend"),
                "combination_key": candidate.get("combination_key"),
                "pace_fit_score": candidate.get("pace_fit_score"),
                "pace_order_fit": candidate.get("pace_order_fit"),
                "pace_risk_score": candidate.get("pace_risk_score"),
                "pace_edge_score": candidate.get("pace_edge_score"),
                "pace_distance_fit": candidate.get("pace_distance_fit"),
                "pace_note": candidate.get("pace_note"),
                "pace_shape": candidate.get("pace_shape"),
                "pace_shape_label": candidate.get("pace_shape_label"),
            }
        )
    return decisions


def build_pool_choice_scorecard(
    decisions: list[dict[str, Any]],
    exotic_candidates: list[dict[str, Any]],
    tickets: list[dict[str, Any]],
    pool_replay_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markets = ["WIN", "PLACE", *EXOTIC_PRODUCTS.keys()]
    rows = []
    for market in markets:
        market_decisions = [row for row in decisions if str(row.get("market")) == market]
        market_candidates = [row for row in exotic_candidates if str(row.get("market")) == market]
        row = pool_choice_market_row(market, market_decisions, market_candidates, pool_replay_market_context(pool_replay_gate, market))
        rows.append(row)
    ranked = sorted(rows, key=lambda row: float(row["choice_score"]), reverse=True)
    actionable = [row for row in ranked if row["actionable_count"] > 0]
    best = actionable[0] if actionable else ranked[0] if ranked else None
    recommendations = pool_choice_recommendations(rows, best)
    return {
        "summary": {
            "best_market": best.get("market") if best else None,
            "best_market_label": best.get("market_label") if best else None,
            "best_score": best.get("choice_score") if best else None,
            "active_ticket_markets": len({str(ticket.get("market")) for ticket in tickets}),
            "actionable_markets": len(actionable),
            "positive_ev_markets": sum(1 for row in rows if float(row.get("best_cost_adjusted_expected_value") or -999) > 0),
        },
        "markets": ranked,
        "recommendations": recommendations,
    }


def pool_choice_market_row(
    market: str,
    decisions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    replay_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sources = [*decisions, *candidates]
    rule = pool_rule_payload(market)
    replay_gate = replay_gate or {}
    optimizer_factor, optimizer_factor_reason = pool_choice_optimizer_stake_adjustment(replay_gate)
    if not sources:
        return {
            "market": market,
            "market_label": market_label(market),
            "choice_score": -999.0,
            "ticket_count": 0,
            "candidate_count": 0,
            "actionable_count": 0,
            "takeout_rate": rule["takeout_rate"],
            "payout_rate": rule["payout_rate"],
            "min_unit": rule["min_unit"],
            "best_probability": None,
            "best_dividend": None,
            "best_required_dividend": None,
            "best_expected_value": None,
            "best_cost_adjusted_expected_value": None,
            "best_minimum_ticket_cost": rule["min_unit"],
            "best_recommended_stake": 0.0,
            "best_combination": "",
            "official_price_count": 0,
            "price_quality": "missing",
            "price_quality_label": "未有派彩",
            "price_gate_reason": "未有候選或官方派彩。",
            "price_coverage": 0.0,
            "pool_replay_gate_status": replay_gate.get("status", "no_candidate"),
            "pool_replay_gate_label": replay_gate.get("label", "未有候選"),
            "pool_replay_gate_reason": replay_gate.get("reason", "未有候選可做 replay 校準。"),
            "pool_replay_stake_factor": pool_replay_stake_factor(replay_gate),
            "pool_replay_sample_size": replay_gate.get("reconciled", 0),
            "pool_replay_min_samples": replay_gate.get("min_samples"),
            "pool_replay_roi": replay_gate.get("roi"),
            "pool_replay_execution_roi": replay_gate.get("execution_roi"),
            "context_status": replay_gate.get("context_status"),
            "context_segment_type": replay_gate.get("context_segment_type"),
            "context_segment_label": replay_gate.get("context_segment_label"),
            "context_sample_size": replay_gate.get("context_sample_size"),
            "context_min_samples": replay_gate.get("context_min_samples"),
            "context_roi": replay_gate.get("context_roi"),
            "global_status": replay_gate.get("global_status"),
            "global_roi": replay_gate.get("global_roi"),
            "optimizer_status": replay_gate.get("optimizer_status"),
            "optimizer_policy": replay_gate.get("optimizer_policy"),
            "optimizer_reason": replay_gate.get("optimizer_reason"),
            "optimizer_delta_roi": replay_gate.get("optimizer_delta_roi"),
            "optimizer_baseline_roi": replay_gate.get("optimizer_baseline_roi"),
            "optimizer_gated_roi": replay_gate.get("optimizer_gated_roi"),
            "optimizer_baseline_max_drawdown": replay_gate.get("optimizer_baseline_max_drawdown"),
            "optimizer_gated_max_drawdown": replay_gate.get("optimizer_gated_max_drawdown"),
            "optimizer_retention_rate": replay_gate.get("optimizer_retention_rate"),
            "optimizer_stake_factor": optimizer_factor,
            "optimizer_stake_reason": optimizer_factor_reason,
            "leverage_index": 0.0,
            "efficiency_gap": None,
            "risk_penalty": 0.0,
            "verdict": "no_candidate",
        }
    best_source = max(sources, key=pool_choice_item_score)
    actionable = [
        row
        for row in sources
        if float(row.get("recommended_stake") or 0.0) > 0 or str(row.get("action") or "") == "有值博"
    ]
    probability = safe_float(best_source.get("probability"))
    dividend = safe_float(best_source.get("odds")) or safe_float(best_source.get("dividend"))
    required = safe_float(best_source.get("required_dividend"))
    expected_value = safe_float(best_source.get("expected_value"))
    adjusted_ev = safe_float(best_source.get("cost_adjusted_expected_value"))
    minimum_cost = safe_float(best_source.get("minimum_ticket_cost")) or float(rule["min_unit"])
    recommended_stake = safe_float(best_source.get("recommended_stake")) or 0.0
    official_price_count = sum(1 for row in sources if pool_price_is_official(market, row))
    price_coverage = official_price_count / max(len(sources), 1)
    price_quality = pool_price_quality(market, best_source)
    leverage = max((dividend or required or 1.0) - 1.0, 0.0)
    efficiency_gap = (dividend - required) if dividend is not None and required is not None else None
    risk_penalty = pool_choice_risk_penalty(market, probability, minimum_cost, leverage)
    replay_factor = pool_replay_stake_factor(replay_gate)
    replay_penalty = pool_choice_replay_penalty(replay_gate)
    score = pool_choice_score(
        adjusted_ev=adjusted_ev,
        probability=probability,
        efficiency_gap=efficiency_gap,
        leverage=leverage,
        takeout=float(rule["takeout_rate"]),
        minimum_cost=minimum_cost,
        risk_penalty=risk_penalty + replay_penalty,
        actionable=bool(actionable),
        price_quality_score=float(price_quality["score"]),
    )
    return {
        "market": market,
        "market_label": market_label(market),
        "choice_score": round(score, 4),
        "ticket_count": len(decisions),
        "candidate_count": len(candidates),
        "actionable_count": len(actionable),
        "takeout_rate": rule["takeout_rate"],
        "payout_rate": rule["payout_rate"],
        "min_unit": rule["min_unit"],
        "best_probability": round(probability, 6) if probability is not None else None,
        "best_dividend": dividend,
        "best_required_dividend": round(required, 3) if required is not None else None,
        "best_expected_value": round(expected_value, 6) if expected_value is not None else None,
        "best_cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
        "best_minimum_ticket_cost": round(minimum_cost, 1),
        "best_recommended_stake": round(recommended_stake, 1),
        "best_combination": str(best_source.get("combination") or best_source.get("horse_name") or ""),
        "official_price_count": official_price_count,
        "price_coverage": round(price_coverage, 4),
        "price_quality": price_quality["quality"],
        "price_quality_label": price_quality["label"],
        "price_gate_reason": price_quality["reason"],
        "pool_replay_gate_status": replay_gate.get("status", "unverified"),
        "pool_replay_gate_label": replay_gate.get("label", "Replay 未驗證"),
        "pool_replay_gate_reason": replay_gate.get("reason", "未有分池 replay 校準。"),
        "pool_replay_stake_factor": replay_factor,
        "pool_replay_sample_size": replay_gate.get("reconciled", 0),
        "pool_replay_min_samples": replay_gate.get("min_samples"),
        "pool_replay_roi": replay_gate.get("roi"),
        "pool_replay_execution_roi": replay_gate.get("execution_roi"),
        "context_status": replay_gate.get("context_status"),
        "context_segment_type": replay_gate.get("context_segment_type"),
        "context_segment_label": replay_gate.get("context_segment_label"),
        "context_sample_size": replay_gate.get("context_sample_size"),
        "context_min_samples": replay_gate.get("context_min_samples"),
        "context_roi": replay_gate.get("context_roi"),
        "global_status": replay_gate.get("global_status"),
        "global_roi": replay_gate.get("global_roi"),
        "optimizer_status": replay_gate.get("optimizer_status"),
        "optimizer_policy": replay_gate.get("optimizer_policy"),
        "optimizer_reason": replay_gate.get("optimizer_reason"),
        "optimizer_delta_roi": replay_gate.get("optimizer_delta_roi"),
        "optimizer_baseline_roi": replay_gate.get("optimizer_baseline_roi"),
        "optimizer_gated_roi": replay_gate.get("optimizer_gated_roi"),
        "optimizer_baseline_max_drawdown": replay_gate.get("optimizer_baseline_max_drawdown"),
        "optimizer_gated_max_drawdown": replay_gate.get("optimizer_gated_max_drawdown"),
        "optimizer_retention_rate": replay_gate.get("optimizer_retention_rate"),
        "optimizer_stake_factor": optimizer_factor,
        "optimizer_stake_reason": optimizer_factor_reason,
        "leverage_index": round(leverage, 3),
        "efficiency_gap": round(efficiency_gap, 3) if efficiency_gap is not None else None,
        "risk_penalty": round(risk_penalty + replay_penalty, 4),
        "verdict": pool_choice_verdict(market, adjusted_ev, efficiency_gap, actionable, risk_penalty, bool(price_quality["is_official"]), replay_gate),
    }


def pool_choice_item_score(row: dict[str, Any]) -> float:
    adjusted_ev = safe_float(row.get("cost_adjusted_expected_value"))
    probability = safe_float(row.get("probability")) or 0.0
    dividend = safe_float(row.get("odds")) or safe_float(row.get("dividend"))
    required = safe_float(row.get("required_dividend"))
    efficiency_gap = (dividend - required) if dividend is not None and required is not None else None
    stake = safe_float(row.get("recommended_stake")) or 0.0
    pace_fit = safe_float(row.get("pace_fit_score")) or 0.0
    pace_risk = safe_float(row.get("pace_risk_score")) or 0.0
    quality = pool_price_quality(str(row.get("market") or ""), row)
    return (
        (adjusted_ev if adjusted_ev is not None else -0.25) * 100.0
        + probability * 8.0
        + (efficiency_gap or 0.0) * 0.15
        + pace_fit * 2.0
        - max(pace_risk - 0.55, 0.0) * 2.5
        + (5.0 if stake > 0 else 0.0)
        + float(quality["score"]) * 6.0
    )


def pool_price_is_official(market: str, row: dict[str, Any]) -> bool:
    return bool(pool_price_quality(market, row)["is_official"])


def pool_price_quality(market: str, row: dict[str, Any]) -> dict[str, Any]:
    if market in {"WIN", "PLACE"}:
        source = str(row.get("odds_source") or "")
        if source in LIVE_ODDS_SOURCES:
            return {
                "quality": "official_live",
                "label": "官方即時賠率",
                "reason": "官方 WIN/PLACE 即時賠率可作下注依據。",
                "is_official": True,
                "score": 1.0,
            }
        if source in FINAL_DIVIDEND_SOURCES:
            return {
                "quality": "final_result",
                "label": "賽果賠率",
                "reason": "賽果賠率只供回測，不可臨場落注。",
                "is_official": True,
                "score": 0.2,
            }
        return {
            "quality": "missing",
            "label": "未有官方賠率",
            "reason": "未有官方即時賠率，不能落注。",
            "is_official": False,
            "score": -1.0,
        }
    quality = str(row.get("dividend_quality") or row.get("price_quality") or "")
    label = str(row.get("dividend_quality_label") or row.get("price_quality_label") or "")
    reason = str(row.get("dividend_gate_reason") or row.get("price_gate_reason") or "")
    is_official = bool(row.get("dividend_is_official") or row.get("price_is_official"))
    if quality == "official_probable":
        return {"quality": quality, "label": label or "官方即時派彩", "reason": reason or "官方可能派彩可作下注依據。", "is_official": True, "score": 1.0}
    if quality == "final_result":
        return {"quality": quality, "label": label or "賽果最終派彩", "reason": reason or "賽果派彩只供回測。", "is_official": True, "score": 0.2}
    if not quality:
        quality = "missing"
    return {
        "quality": quality,
        "label": label or ("未有官方派彩" if quality == "missing" else "未核實派彩"),
        "reason": reason or "未有官方組合彩池派彩，不能落注。",
        "is_official": is_official,
        "score": -1.0,
    }


def pool_choice_score(
    adjusted_ev: float | None,
    probability: float | None,
    efficiency_gap: float | None,
    leverage: float,
    takeout: float,
    minimum_cost: float,
    risk_penalty: float,
    actionable: bool,
    price_quality_score: float = 0.0,
) -> float:
    ev_component = (adjusted_ev if adjusted_ev is not None else -0.25) * 100.0
    probability_component = (probability or 0.0) * 8.0
    gap_component = (efficiency_gap or 0.0) * 0.15
    leverage_component = min(leverage, 80.0) * 0.04
    cost_penalty = min(max(minimum_cost - 1.0, 0.0), 30.0) * 0.08
    action_bonus = 8.0 if actionable else 0.0
    quality_component = price_quality_score * 6.0
    return ev_component + probability_component + gap_component + leverage_component + action_bonus + quality_component - takeout * 12.0 - cost_penalty - risk_penalty


def pool_choice_risk_penalty(market: str, probability: float | None, minimum_cost: float, leverage: float) -> float:
    probability_value = probability or 0.0
    if market in {"WIN", "PLACE"}:
        return 0.0
    low_hit_penalty = max(0.0, 0.08 - probability_value) * 35.0
    cost_penalty = max(0.0, minimum_cost - 10.0) * 0.05
    leverage_penalty = max(0.0, leverage - 120.0) * 0.02
    return low_hit_penalty + cost_penalty + leverage_penalty


def pool_choice_replay_penalty(replay_gate: dict[str, Any] | None) -> float:
    if not replay_gate:
        return 0.0
    status = str(replay_gate.get("status") or "")
    return {
        "replay_block": 18.0,
        "replay_reduce": 6.0,
        "waiting_final_dividend": 5.0,
        "ready_to_reconcile": 3.0,
        "sample_building": 1.5,
        "no_replay_data": 1.0,
    }.get(status, 0.0)


def pool_choice_verdict(
    market: str,
    adjusted_ev: float | None,
    efficiency_gap: float | None,
    actionable: list[dict[str, Any]] | bool,
    risk_penalty: float,
    official_price: bool,
    replay_gate: dict[str, Any] | None = None,
) -> str:
    replay_status = str((replay_gate or {}).get("status") or "")
    if replay_status == "replay_block":
        return "replay_blocked"
    if adjusted_ev is None:
        return "need_dividend"
    if market not in {"WIN", "PLACE"} and not official_price:
        return "need_official_dividend"
    if adjusted_ev <= 0:
        return "no_edge_after_cost"
    if efficiency_gap is not None and efficiency_gap < 0:
        return "dividend_too_short"
    if risk_penalty >= 2.5:
        return "high_variance"
    if actionable:
        return "actionable"
    return "watchlist"


def pool_choice_recommendations(rows: list[dict[str, Any]], best: dict[str, Any] | None) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    blocked = [row for row in rows if row.get("pool_replay_gate_status") == "replay_block"]
    reduced = [
        row
        for row in rows
        if row.get("pool_replay_gate_status") in {"replay_reduce", "waiting_final_dividend", "ready_to_reconcile"}
    ]
    if blocked:
        labels = "、".join(str(row.get("market_label") or row.get("market")) for row in blocked[:3])
        recommendations.append(
            {
                "level": "risk",
                "title": "Replay 封鎖彩池",
                "body": f"{labels} 的已結算 replay 表現未達標，今場只保留觀察，不輸出真注。",
            }
        )
    if reduced:
        labels = "、".join(str(row.get("market_label") or row.get("market")) for row in reduced[:3])
        recommendations.append(
            {
                "level": "risk",
                "title": "Replay 降注彩池",
                "body": f"{labels} 因 ROI、對數或 final dividend 未完全可信，已自動降低注碼及排序分。",
            }
        )
    if best and best.get("verdict") == "actionable":
        recommendations.append(
            {
                "level": "focus",
                "title": f"優先彩池：{best['market_label']}",
                "body": f"分數 {best['choice_score']}，成本後EV {format_signed_pct(best.get('best_cost_adjusted_expected_value'))}，建議注碼 ${best.get('best_recommended_stake') or 0}。",
            }
        )
    by_market = {row["market"]: row for row in rows}
    qpl = by_market.get("QPL")
    trio = by_market.get("TRIO")
    tce = by_market.get("TCE")
    if qpl and trio and qpl.get("best_probability") and trio.get("best_probability"):
        qpl_ev = safe_float(qpl.get("best_cost_adjusted_expected_value")) or -999.0
        trio_ev = safe_float(trio.get("best_cost_adjusted_expected_value")) or -999.0
        if qpl_ev > 0 and trio_ev > qpl_ev and float(trio.get("choice_score") or 0.0) >= float(qpl.get("choice_score") or 0.0) - 4.0:
            recommendations.append(
                {
                    "level": "upgrade",
                    "title": "位置Q可升級檢查：單T/三重彩",
                    "body": f"位置Q有值，但{trio['market_label']}成本後EV更高；同一組馬腳可用較小注碼追求更高派彩。",
                }
            )
        elif qpl_ev > 0 and trio_ev <= 0:
            recommendations.append(
                {
                    "level": "risk",
                    "title": "保留位置Q，暫不升級",
                    "body": "組合彩池派彩未補償三甲難度，避免為了高派彩犧牲正期望。",
                }
            )
    if tce and (safe_float(tce.get("best_cost_adjusted_expected_value")) or -999.0) > 0 and float(tce.get("risk_penalty") or 0.0) < 2.5:
        recommendations.append(
            {
                "level": "upgrade",
                "title": "可研究單T精準腳法",
                "body": "三重彩排序風險高，只在官方派彩足夠、模型排序清晰、風險扣分不高時納入候選。",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "level": "data",
                "title": "彩池選擇仍需更多派彩樣本",
                "body": "現階段先按成本後EV與風險分數排序；等 final dividends / betting ledger 增加後再用真實ROI校準。",
            }
        )
    return recommendations[:4]


def build_bet_slip(
    tickets: list[dict[str, Any]],
    pool_choice: dict[str, Any],
    exposure_report: dict[str, Any],
    bankroll: float,
    profile: RiskProfile,
    race_status: str,
) -> dict[str, Any]:
    pool_context = {
        str(row.get("market")): row
        for row in (pool_choice.get("markets") or [])
        if isinstance(row, dict)
    }
    annotated = []
    for ticket in tickets:
        market = str(ticket.get("market") or "")
        probability = safe_float(ticket.get("probability")) or 0.0
        expected_value = safe_float(ticket.get("expected_value")) or 0.0
        adjusted_ev = safe_float(ticket.get("cost_adjusted_expected_value"))
        stake = safe_float(ticket.get("recommended_stake")) or 0.0
        minimum_cost = safe_float(ticket.get("minimum_ticket_cost")) or 0.0
        risk_tier = slip_risk_tier(market, probability)
        role = slip_portfolio_role(market, probability, adjusted_ev)
        score = slip_priority_score(ticket, pool_context.get(market), risk_tier)
        expected_profit = stake * expected_value
        ticket["slip_strategy"] = profile.name
        ticket["slip_priority_score"] = round(score, 4)
        ticket["risk_tier"] = risk_tier
        ticket["portfolio_role"] = role
        ticket["expected_profit"] = round(expected_profit, 2)
        ticket["hit_probability"] = round(probability, 6)
        ticket["minimum_ticket_cost"] = minimum_cost
        annotated.append(ticket)

    ranked = sorted(annotated, key=lambda row: float(row.get("slip_priority_score") or -999.0), reverse=True)
    for rank, ticket in enumerate(ranked, start=1):
        ticket["slip_rank"] = rank

    total_stake = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in ranked)
    expected_profit = sum(safe_float(row.get("expected_profit")) or 0.0 for row in ranked)
    max_race_stake = bankroll * profile.max_race_fraction
    tier_stakes = {
        tier: round(sum(safe_float(row.get("recommended_stake")) or 0.0 for row in ranked if row.get("risk_tier") == tier), 2)
        for tier in ["conservative", "standard", "aggressive"]
    }
    at_least_one = portfolio_hit_probability(ranked)
    status = slip_status(race_status, ranked)
    strategies = build_slip_strategies(ranked, bankroll, profile)
    return {
        "summary": {
            "status": status,
            "ticket_count": len(ranked),
            "total_stake": round(total_stake, 2),
            "max_race_stake": round(max_race_stake, 2),
            "stake_usage": round(total_stake / max_race_stake, 4) if max_race_stake else None,
            "expected_profit": round(expected_profit, 2),
            "expected_roi": round(expected_profit / total_stake, 6) if total_stake else None,
            "at_least_one_hit_probability": at_least_one,
            "risk_profile": profile.name,
            "recommended_strategy": profile.name,
            "core_ticket_count": sum(1 for row in ranked if row.get("portfolio_role") == "core"),
            "leverage_ticket_count": sum(1 for row in ranked if row.get("portfolio_role") == "leverage"),
            "conservative_stake": tier_stakes["conservative"],
            "standard_stake": tier_stakes["standard"],
            "aggressive_stake": tier_stakes["aggressive"],
            "exposure_adjusted": bool((exposure_report.get("adjusted_tickets") or [])),
        },
        "tickets": [slip_ticket_payload(row) for row in ranked],
        "strategies": strategies,
        "notes": bet_slip_notes(ranked, pool_choice, exposure_report),
    }


def slip_risk_tier(market: str, probability: float) -> str:
    if market in {"PLACE", "QPL"} or probability >= 0.45:
        return "conservative"
    if market in {"WIN", "QIN", "FCT"} or probability >= 0.16:
        return "standard"
    return "aggressive"


def slip_portfolio_role(market: str, probability: float, adjusted_ev: float | None) -> str:
    if market in {"PLACE", "QPL"} and probability >= 0.35:
        return "core"
    if market in {"TRIO", "TCE", "FIRST4", "QUARTET"}:
        return "leverage"
    if adjusted_ev is not None and adjusted_ev > 0.25:
        return "value"
    return "support"


def slip_priority_score(ticket: dict[str, Any], pool_context: dict[str, Any] | None, risk_tier: str) -> float:
    probability = safe_float(ticket.get("probability")) or 0.0
    adjusted_ev = safe_float(ticket.get("cost_adjusted_expected_value"))
    edge = safe_float(ticket.get("edge")) or 0.0
    stake = safe_float(ticket.get("recommended_stake")) or 0.0
    pool_score = safe_float((pool_context or {}).get("choice_score")) or 0.0
    role_bonus = {"conservative": 4.0, "standard": 2.0, "aggressive": -1.0}.get(risk_tier, 0.0)
    ev_component = (adjusted_ev if adjusted_ev is not None else -0.2) * 80.0
    return ev_component + probability * 18.0 + edge * 40.0 + min(stake, 200.0) * 0.02 + pool_score * 0.12 + role_bonus


def portfolio_hit_probability(tickets: list[dict[str, Any]]) -> float | None:
    if not tickets:
        return None
    miss_probability = 1.0
    for ticket in tickets[:8]:
        probability = min(max(safe_float(ticket.get("probability")) or 0.0, 0.0), 0.95)
        miss_probability *= 1.0 - probability
    return round(1.0 - miss_probability, 6)


def slip_status(race_status: str, tickets: list[dict[str, Any]]) -> str:
    if race_status == "resulted":
        return "review_only"
    if race_status == "live":
        return "locked_live"
    if tickets:
        return "actionable"
    return "no_edge"


def build_slip_strategies(
    tickets: list[dict[str, Any]],
    bankroll: float,
    profile: RiskProfile,
) -> list[dict[str, Any]]:
    specs = [
        ("conservative", "保守", {"conservative"}, 0.65),
        ("standard", "標準", {"conservative", "standard"}, 1.0),
        ("aggressive", "進取", {"conservative", "standard", "aggressive"}, 1.25),
    ]
    rows = []
    max_stake = bankroll * profile.max_race_fraction
    for key, label, tiers, multiplier in specs:
        selected = [row for row in tickets if row.get("risk_tier") in tiers]
        raw_stake = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in selected) * multiplier
        scale = min(1.0, max_stake / raw_stake) if raw_stake > 0 and max_stake > 0 else 1.0
        stake = raw_stake * scale
        expected_profit = sum((safe_float(row.get("expected_profit")) or 0.0) * multiplier * scale for row in selected)
        rows.append(
            {
                "strategy": key,
                "label": label,
                "ticket_count": len(selected),
                "stake": round(stake, 2),
                "expected_profit": round(expected_profit, 2),
                "expected_roi": round(expected_profit / stake, 6) if stake else None,
                "hit_probability": portfolio_hit_probability(selected),
                "stake_multiplier": multiplier,
                "is_current": key == profile.name,
                "message": strategy_message(key, selected),
            }
        )
    return rows


def strategy_message(strategy: str, tickets: list[dict[str, Any]]) -> str:
    if not tickets:
        return "未有合資格投注票。"
    if strategy == "conservative":
        return "集中高命中率及較低波動玩法。"
    if strategy == "aggressive":
        return "保留槓桿彩池，但必須接受較大回撤。"
    return "平衡命中率、EV、注碼及彩池槓桿。"


def slip_ticket_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "recommendation_id",
        "slip_rank",
        "slip_priority_score",
        "slip_strategy",
        "risk_tier",
        "portfolio_role",
        "market",
        "market_label",
        "horse_id",
        "horse_no",
        "horse_name",
        "horse_numbers",
        "horse_names",
        "probability",
        "hit_probability",
        "odds",
        "required_dividend",
        "expected_value",
        "cost_adjusted_expected_value",
        "expected_profit",
        "recommended_stake",
        "minimum_ticket_cost",
        "combination_count",
        "action",
        "reason",
        "exposure_action",
        "exposure_reason",
        "pool_replay_gate_status",
        "pool_replay_gate_label",
        "pool_replay_gate_reason",
        "pool_replay_stake_factor",
        "pool_choice_optimizer_stake_factor",
        "pool_choice_optimizer_stake_reason",
        "pool_replay_sample_size",
        "pool_replay_min_samples",
        "pool_replay_roi",
        "pool_replay_execution_roi",
    ]
    return {key: ticket.get(key) for key in keys if key in ticket}


def bet_slip_notes(
    tickets: list[dict[str, Any]],
    pool_choice: dict[str, Any],
    exposure_report: dict[str, Any],
) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    if not tickets:
        notes.append({"level": "data", "title": "不下注", "body": "現時未有投注票同時通過 EV、成本、賠率來源及風險 gate。"})
        return notes
    best_market = (pool_choice.get("summary") or {}).get("best_market_label")
    if best_market:
        notes.append({"level": "focus", "title": "主攻彩池", "body": f"彩池選擇模型暫時偏向 {best_market}，下注單會優先排序同類高分票。"})
    replay_limited = [row for row in tickets if row.get("pool_replay_gate_status") in {"replay_reduce", "waiting_final_dividend", "ready_to_reconcile"}]
    if replay_limited:
        markets = sorted({str(row.get("market_label") or row.get("market")) for row in replay_limited})
        notes.append({"level": "risk", "title": "分池 replay 降注", "body": f"{'、'.join(markets[:3])} 受已結算 ROI / final dividend 對數狀態限制，注碼已先行收細。"})
    leverage = [row for row in tickets if row.get("portfolio_role") == "leverage"]
    if leverage:
        notes.append({"level": "upgrade", "title": "槓桿票", "body": "已將單T/三連彩/四連環類高派彩票標成槓桿角色，注碼受單場風險上限約束。"})
    if exposure_report.get("adjusted_tickets"):
        notes.append({"level": "risk", "title": "已降相關曝險", "body": "有重複馬匹、彩池或組合曝險過高，系統已先降注再輸出下注單。"})
    return notes[:4]


def market_label(market: str) -> str:
    if market == "WIN":
        return "獨贏"
    if market == "PLACE":
        return "位置"
    return str(EXOTIC_PRODUCTS.get(market, {}).get("label") or market)


def format_signed_pct(value: object) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric * 100:+.1f}%"


def apply_correlated_exposure_controls(
    decisions: list[dict[str, Any]],
    bankroll: float,
    profile: RiskProfile,
) -> dict[str, Any]:
    active = [decision for decision in decisions if float(decision.get("recommended_stake") or 0.0) > 0]
    max_race_stake = max(bankroll * profile.max_race_fraction, 0.0)
    caps = {
        "horse": round(max_race_stake * 0.50, 2),
        "pool": round(max_race_stake * 0.65, 2),
        "combination": round(max(bankroll * profile.max_bet_fraction * 1.25, 0.0), 2),
    }
    before = exposure_snapshot(active, caps)

    adjusted: list[dict[str, Any]] = []
    for decision in active:
        factor, reasons = exposure_adjustment_factor(decision, before, caps)
        if factor >= 0.999:
            continue
        original_stake = float(decision.get("recommended_stake") or 0.0)
        new_stake = round_stake_to_unit(original_stake * factor, str(decision.get("market")))
        decision["recommended_stake"] = new_stake
        decision["stake_fraction"] = round(new_stake / bankroll, 6) if bankroll else 0.0
        decision["exposure_adjustment_factor"] = round(factor, 4)
        decision["exposure_action"] = "降注" if new_stake > 0 else "不加注"
        decision["exposure_reason"] = "；".join(reasons)
        if new_stake <= 0:
            decision["action"] = "觀望"
            decision["reason"] = f"{decision.get('reason') or '符合條件'}；相關曝險後低於最低投注單位"
        else:
            decision["reason"] = f"{decision.get('reason') or '符合條件'}；相關曝險降注"
        adjusted.append(
            {
                "market": decision.get("market"),
                "horse_id": decision.get("horse_id"),
                "horse_name": decision.get("horse_name"),
                "original_stake": round(original_stake, 1),
                "adjusted_stake": round(float(new_stake), 1),
                "factor": round(factor, 4),
                "reason": decision["exposure_reason"],
            }
        )

    after = exposure_snapshot(active, caps)
    return {
        "caps": caps,
        "before": before,
        "after": after,
        "adjusted_tickets": adjusted,
        "summary": exposure_summary(after, caps, adjusted),
    }


def exposure_snapshot(decisions: list[dict[str, Any]], caps: dict[str, float]) -> dict[str, Any]:
    horse_exposure: dict[str, dict[str, Any]] = {}
    pool_exposure: dict[str, float] = {}
    combination_exposure: dict[str, dict[str, Any]] = {}

    for decision in decisions:
        stake = float(decision.get("recommended_stake") or 0.0)
        if stake <= 0:
            continue
        market = str(decision.get("market") or "")
        pool_exposure[market] = pool_exposure.get(market, 0.0) + stake
        horse_ids = decision_horse_ids(decision)
        horse_names = decision_horse_names(decision)
        for index, horse_id in enumerate(horse_ids):
            row = horse_exposure.setdefault(
                horse_id,
                {
                    "horse_id": horse_id,
                    "horse_name": horse_names[index] if index < len(horse_names) else horse_id,
                    "stake": 0.0,
                    "ticket_count": 0,
                },
            )
            row["stake"] = round(float(row["stake"]) + stake, 2)
            row["ticket_count"] = int(row["ticket_count"]) + 1
        signature = combination_signature(decision, horse_ids)
        if signature:
            row = combination_exposure.setdefault(
                signature,
                {
                    "signature": signature,
                    "label": decision.get("horse_name") or " + ".join(horse_names),
                    "stake": 0.0,
                    "ticket_count": 0,
                },
            )
            row["stake"] = round(float(row["stake"]) + stake, 2)
            row["ticket_count"] = int(row["ticket_count"]) + 1

    return {
        "horse": top_exposures(horse_exposure.values(), caps["horse"]),
        "pool": top_pool_exposures(pool_exposure, caps["pool"]),
        "combination": top_exposures(combination_exposure.values(), caps["combination"]),
    }


def exposure_adjustment_factor(
    decision: dict[str, Any],
    snapshot: dict[str, Any],
    caps: dict[str, float],
) -> tuple[float, list[str]]:
    factors: list[float] = []
    reasons: list[str] = []
    horse_ids = set(decision_horse_ids(decision))
    for row in snapshot.get("horse", []):
        if str(row.get("horse_id")) in horse_ids and float(row.get("stake") or 0.0) > caps["horse"] > 0:
            factor = caps["horse"] / float(row["stake"])
            factors.append(factor)
            reasons.append(f"{row.get('horse_name') or row.get('horse_id')} 同馬曝險 {float(row['stake']):.1f}>{caps['horse']:.1f}")
    market = str(decision.get("market") or "")
    for row in snapshot.get("pool", []):
        if str(row.get("market")) == market and float(row.get("stake") or 0.0) > caps["pool"] > 0:
            factor = caps["pool"] / float(row["stake"])
            factors.append(factor)
            reasons.append(f"{row.get('market_label') or market} 彩池曝險 {float(row['stake']):.1f}>{caps['pool']:.1f}")
    signature = combination_signature(decision, decision_horse_ids(decision))
    for row in snapshot.get("combination", []):
        if str(row.get("signature")) == signature and float(row.get("stake") or 0.0) > caps["combination"] > 0:
            factor = caps["combination"] / float(row["stake"])
            factors.append(factor)
            reasons.append(f"同組腳位曝險 {float(row['stake']):.1f}>{caps['combination']:.1f}")
    if not factors:
        return 1.0, []
    return max(min(factors), 0.0), reasons


def exposure_summary(snapshot: dict[str, Any], caps: dict[str, float], adjusted: list[dict[str, Any]]) -> dict[str, Any]:
    breached = []
    for scope, rows in snapshot.items():
        cap = caps.get(scope, 0.0)
        for row in rows:
            if float(row.get("stake") or 0.0) > cap > 0:
                breached.append({"scope": scope, **row})
    return {
        "adjusted_count": len(adjusted),
        "breach_count": len(breached),
        "status": "已降注" if adjusted else "正常",
        "message": "已按同馬/同池/同腳位曝險調整注碼" if adjusted else "未見過度集中曝險",
    }


def decision_horse_ids(decision: dict[str, Any]) -> list[str]:
    if isinstance(decision.get("horse_ids"), list) and decision["horse_ids"]:
        return [str(horse_id) for horse_id in decision["horse_ids"]]
    horse_id = decision.get("horse_id")
    return [str(horse_id)] if horse_id else []


def decision_horse_names(decision: dict[str, Any]) -> list[str]:
    if isinstance(decision.get("horse_names"), list) and decision["horse_names"]:
        return [str(name) for name in decision["horse_names"]]
    name = decision.get("horse_name")
    return [str(name)] if name else []


def combination_signature(decision: dict[str, Any], horse_ids: list[str]) -> str:
    if len(horse_ids) <= 1:
        return ""
    return "+".join(sorted(str(horse_id) for horse_id in horse_ids))


def top_exposures(rows: Any, cap: float) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda row: float(row.get("stake") or 0.0), reverse=True)
    result = []
    for row in sorted_rows[:8]:
        stake = float(row.get("stake") or 0.0)
        payload = dict(row)
        payload["stake"] = round(stake, 1)
        payload["cap"] = round(cap, 1)
        payload["usage"] = round(stake / cap, 4) if cap > 0 else None
        payload["status"] = "超額" if cap > 0 and stake > cap else "正常"
        result.append(payload)
    return result


def top_pool_exposures(pool_exposure: dict[str, float], cap: float) -> list[dict[str, Any]]:
    rows = []
    for market, stake in pool_exposure.items():
        rule = pool_rule_payload(market)
        rows.append(
            {
                "market": market,
                "market_label": rule.get("label") or market,
                "stake": round(float(stake), 2),
                "ticket_count": 0,
            }
        )
    return top_exposures(rows, cap)


def annotate_exotic_candidate_stakes(
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> None:
    decision_by_key = {
        (str(row.get("market")), str(row.get("combination_key") or row.get("horse_id"))): row
        for row in decisions
    }
    for candidate in candidates:
        key = (str(candidate.get("market")), str(candidate.get("combination_key")))
        decision = decision_by_key.get(key)
        if not decision:
            continue
        candidate["recommended_stake"] = float(decision.get("recommended_stake") or 0.0)
        candidate["stake_fraction"] = decision.get("stake_fraction")
        candidate["stake_reason"] = decision.get("reason")
        candidate["stake_action"] = decision.get("action")
        candidate["exposure_action"] = decision.get("exposure_action")
        candidate["exposure_reason"] = decision.get("exposure_reason")
        candidate["exposure_adjustment_factor"] = decision.get("exposure_adjustment_factor")
        candidate["pool_replay_gate_status"] = decision.get("pool_replay_gate_status")
        candidate["pool_replay_gate_label"] = decision.get("pool_replay_gate_label")
        candidate["pool_replay_gate_reason"] = decision.get("pool_replay_gate_reason")
        candidate["pool_replay_stake_factor"] = decision.get("pool_replay_stake_factor")
        candidate["pool_replay_sample_size"] = decision.get("pool_replay_sample_size")
        candidate["pool_replay_min_samples"] = decision.get("pool_replay_min_samples")
        candidate["pool_replay_roi"] = decision.get("pool_replay_roi")
        candidate["pool_replay_execution_roi"] = decision.get("pool_replay_execution_roi")
        combination_count = max(int(candidate.get("combination_count") or 1), 1)
        candidate["per_combination_stake"] = round(float(candidate["recommended_stake"]) / combination_count, 2)
        if float(candidate["recommended_stake"]) <= 0:
            existing_reason = str(candidate.get("structure_reason") or "").strip()
            no_bet_reason = str(decision.get("reason") or "未達下注門檻")
            candidate["structure_reason"] = (
                f"{existing_reason}；{no_bet_reason}，所以建議 $0"
                if existing_reason
                else f"{no_bet_reason}，所以建議 $0"
            )
            continue
        if float(candidate["recommended_stake"]) <= 0:
            candidate["structure_label"] = "不做膽腳"
            candidate["structure_mode"] = "none"
            candidate["bankers"] = []
            candidate["banker_ids"] = []
            candidate["legs"] = []
            candidate["leg_ids"] = []
            candidate["structure"] = "不做膽腳：未達下注門檻"
            candidate["structure_reason"] = f"{decision.get('reason') or '未有edge'}，建議 $0"
        else:
            candidate["structure_reason"] = (
                f"{candidate.get('structure_reason') or '候選組合已過門檻'}；"
                f"建議總注 {candidate['recommended_stake']:.1f}，每組約 {candidate['per_combination_stake']:.2f}"
            )


def build_upgrade_paths(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    qpl_by_pair = {
        frozenset(str(horse_id) for horse_id in item["horse_ids"]): item
        for item in candidates
        if item["market"] == "QPL"
    }
    paths = []
    for trio in [item for item in candidates if item["market"] == "TRIO"]:
        horse_ids = [str(horse_id) for horse_id in trio["horse_ids"]]
        pair_items = [
            qpl_by_pair.get(frozenset(pair))
            for pair in combinations(horse_ids, 2)
        ]
        pair_items = [item for item in pair_items if item]
        if len(pair_items) < 2:
            continue
        top_pairs = sorted(pair_items, key=lambda item: float(item["probability"]), reverse=True)[:2]
        paths.append(
            {
                "from_markets": [item["combination"] for item in top_pairs],
                "to_market": trio["combination"],
                "to_label": trio["market_label"],
                "trio_probability": trio["probability"],
                "trio_break_even_dividend": trio["break_even_dividend"],
                "note": "同一組膽腳可比較單T回報",
            }
        )
    paths.sort(key=lambda item: float(item["trio_probability"]), reverse=True)
    return paths[:6]


def exotic_structure_payload(
    market: str,
    market_label: str,
    horse_ids: list[str],
    runner_by_id: dict[str, dict[str, Any]],
    probabilities: dict[str, float],
    ordered: bool,
    race_status: str,
    dividend: float | None,
    expected_value: float | None,
    adjusted_ev: float | None,
) -> dict[str, Any]:
    base = {
        "structure_mode": "none",
        "structure_label": "不做膽腳",
        "structure": "不做膽腳：只列為觀察候選",
        "structure_reason": "未有足夠派彩/edge，建議 $0",
        "banker_ids": [],
        "bankers": [],
        "leg_ids": [],
        "legs": [],
        "combination_count": 1,
    }
    if race_status in {"resulted", "live"}:
        base["structure_reason"] = "已完場/已開跑，停止下注；只作回測觀察"
        return base
    if not dividend or expected_value is None or adjusted_ev is None:
        return base
    if expected_value <= 0 or adjusted_ev < 0:
        base["structure_reason"] = "派彩未能覆蓋抽水及風險緩衝，建議 $0"
        return base

    rows = [runner_by_id[str(horse_id)] for horse_id in horse_ids if str(horse_id) in runner_by_id]
    ranked_rows = sorted(rows, key=lambda row: probabilities.get(str(row.get("horse_id")), 0.0), reverse=True)
    top = ranked_rows[0] if ranked_rows else None
    second_strength = probabilities.get(str(ranked_rows[1].get("horse_id")), 0.0) if len(ranked_rows) > 1 else 0.0
    top_strength = probabilities.get(str(top.get("horse_id")), 0.0) if top else 0.0
    leg_rows = [row for row in ranked_rows if top and str(row.get("horse_id")) != str(top.get("horse_id"))]
    leg_count = len(leg_rows)
    market_size = int(EXOTIC_PRODUCTS.get(market, {}).get("size") or len(rows))
    clear_banker = bool(
        top
        and not ordered
        and len(rows) > market_size
        and leg_count in {2, 3}
        and top_strength >= second_strength * 1.2
    )
    if clear_banker:
        return {
            "structure_mode": "banker_leg",
            "structure_label": "膽拖腳",
            "structure": f"{market_label}：{runner_label(top)} 做膽，拖 {leg_count} 腳（只限本候選，不加全腳）",
            "structure_reason": "候選內首選馬優勢明顯，膽腳只覆蓋呢一條候選組合，避免拖太多腳拉高成本",
            "banker_ids": [str(top.get("horse_id"))],
            "bankers": [runner_label(top)],
            "leg_ids": [str(row.get("horse_id")) for row in leg_rows],
            "legs": [runner_label(row) for row in leg_rows],
            "combination_count": 1,
        }

    separator = " + "
    combination_count = factorial(len(rows)) if ordered and len(rows) > 1 else 1
    ordered_note = f"；複式會覆蓋 {combination_count} 條排序飛，只限本候選馬匹" if ordered and combination_count > 1 else ""
    return {
        "structure_mode": "box",
        "structure_label": "複式",
        "structure": f"{market_label}: {separator.join(runner_label(row) for row in rows)}",
        "structure_reason": f"不設膽腳，不加全馬，只用本候選入面幾匹做複式{ordered_note}",
        "banker_ids": [],
        "bankers": [],
        "leg_ids": [str(row.get("horse_id")) for row in rows],
        "legs": [runner_label(row) for row in rows],
        "combination_count": combination_count,
    }
    return {
        "structure_mode": "box",
        "structure_label": "複式",
        "structure": f"{market_label}：{separator.join(runner_label(row) for row in rows)}",
        "structure_reason": "未有足夠清晰單膽優勢，保持候選複式/單組合，唔額外拖腳",
        "banker_ids": [],
        "bankers": [],
        "leg_ids": [str(row.get("horse_id")) for row in rows],
        "legs": [runner_label(row) for row in rows],
        "combination_count": 1,
    }


def runner_label(row: dict[str, Any]) -> str:
    horse_no = row.get("horse_no") or "-"
    name = row.get("display_name") or row.get("horse_name_zh") or row.get("horse_name") or row.get("horse_id")
    return f"{horse_no} {name}"


def ordered_finish_probability(order: list[str], probabilities: dict[str, float]) -> float:
    remaining = 1.0
    probability = 1.0
    for horse_id in order:
        strength = probabilities.get(horse_id, 0.0)
        if strength <= 0 or remaining <= 0:
            return 0.0
        probability *= strength / remaining
        remaining -= strength
    return probability


def unordered_top_k_probability(
    target_ids: list[str],
    top_k: int,
    probabilities: dict[str, float],
) -> float:
    if len(target_ids) > top_k:
        return 0.0
    other_ids = [horse_id for horse_id in probabilities if horse_id not in set(target_ids)]
    extra_count = top_k - len(target_ids)
    probability = 0.0
    for extras in combinations(other_ids, extra_count):
        group = list(target_ids) + [str(horse_id) for horse_id in extras]
        for order in permutations(group, top_k):
            probability += ordered_finish_probability(list(order), probabilities)
    return probability


def format_combination(horse_ids: list[str], runner_by_id: dict[str, dict[str, Any]], ordered: bool) -> str:
    separator = " > " if ordered else " + "
    return separator.join(
        str(runner_by_id[horse_id].get("horse_no") or runner_by_id[horse_id].get("display_name") or horse_id)
        for horse_id in horse_ids
    )


def exotic_action(race_status: str) -> str:
    if race_status == "resulted":
        return "只供回測"
    if race_status == "live":
        return "停止下注"
    return "等組合賠率"


def exotic_reason(race_status: str) -> str:
    if race_status == "resulted":
        return "已完場，只作回測"
    if race_status == "live":
        return "已開跑，停止下注"
    return "未接入官方組合彩池賠率，先看打和派彩"


def exotic_combination_key(market: str, horse_numbers: list[object]) -> str:
    ordered = bool(EXOTIC_PRODUCTS.get(market, {}).get("ordered"))
    numbers = []
    for value in horse_numbers:
        try:
            numbers.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ordered:
        numbers = sorted(numbers)
    separator = ">" if ordered else "+"
    return separator.join(str(number) for number in numbers)


def exotic_priority(market: str) -> int:
    return {
        "TRIO": 7,
        "QPL": 6,
        "QIN": 5,
        "FCT": 4,
        "TCE": 3,
        "FIRST4": 2,
        "QUARTET": 1,
    }.get(market, 0)


def safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
