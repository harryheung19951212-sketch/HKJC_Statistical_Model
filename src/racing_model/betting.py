from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, permutations
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


LIVE_ODDS_SOURCES = {"hkjc_graphql", "hkjc_mqtt"}

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
) -> dict[str, Any]:
    profile = RISK_PROFILES.get(risk_profile, RISK_PROFILES["standard"])
    bankroll = max(float(bankroll or 0), 0.0)
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
            )
        )

    exotic_candidates = build_exotic_candidates(predictions, race_status, exotic_dividends=exotic_dividends)
    exotic_decisions = build_exotic_decisions(exotic_candidates, race_status, bankroll, profile)
    active = [decision for decision in [*decisions, *exotic_decisions] if decision["recommended_stake"] > 0]
    max_race_stake = round(bankroll * profile.max_race_fraction, 2)
    raw_total = sum(float(item["recommended_stake"]) for item in active)
    scale = 1.0
    if raw_total > max_race_stake > 0:
        scale = max_race_stake / raw_total
        for decision in active:
            decision["recommended_stake"] = round_stake_to_unit(
                float(decision["recommended_stake"]) * scale,
                str(decision["market"]),
            )
            decision["stake_fraction"] = round(float(decision["recommended_stake"]) / bankroll, 6) if bankroll else 0.0

    decisions.sort(
        key=lambda item: (
            float(item["recommended_stake"]),
            float(item["expected_value"] or -99),
            float(item["probability"] or 0),
        ),
        reverse=True,
    )
    tickets = [decision for decision in [*decisions, *exotic_decisions] if decision["recommended_stake"] > 0]
    return {
        "race_status": race_status,
        "bankroll": bankroll,
        "risk_profile": profile.name,
        "risk_settings": {
            "fractional_kelly": profile.fractional_kelly,
            "max_bet_fraction": profile.max_bet_fraction,
            "max_race_fraction": profile.max_race_fraction,
            "min_expected_value": profile.min_expected_value,
            "min_edge": profile.min_edge,
        },
        "pool_rules": all_pool_rules_payload(),
        "max_race_stake": max_race_stake,
        "total_recommended_stake": round(sum(float(item["recommended_stake"]) for item in tickets), 1),
        "stake_scaled": scale < 1.0,
        "tickets": tickets,
        "decisions": decisions,
        "exotic_decisions": exotic_decisions,
        "exotic_candidates": exotic_candidates,
        "upgrade_paths": build_upgrade_paths(exotic_candidates),
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
) -> dict[str, Any]:
    probability = safe_float(prediction.get(probability_key))
    odds = safe_float(prediction.get(odds_key))
    source = prediction.get(source_key)
    fair_odds = (1.0 / probability) if probability and probability > 0 else None
    market_probability = (1.0 / odds) if odds and odds > 1 else None
    edge = probability - market_probability if market_probability is not None else None
    expected_value = probability * odds - 1.0 if odds and odds > 1 else None
    adjusted_ev = cost_adjusted_expected_value(expected_value, market)
    req_ev = required_expected_value(market, profile.min_expected_value)
    req_edge = required_edge(market, profile.min_edge)
    req_dividend = required_dividend(probability, market, profile.min_expected_value)
    raw_kelly = kelly_fraction(probability, odds)
    kelly = raw_kelly * profile.fractional_kelly
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
    action = action_label(eligible, expected_value, edge, profile, reason)
    stake_fraction = capped_fraction if action == "有值博" else 0.0
    stake = round_stake_to_unit(bankroll * stake_fraction, market) if stake_fraction > 0 else 0.0
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
        "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
        "required_expected_value": round(req_ev, 6),
        "required_edge": round(req_edge, 6),
        "required_dividend": round(req_dividend, 3) if req_dividend else None,
        "pool_rule": pool_rule_payload(market),
        "kelly_fraction": round(max(raw_kelly, 0.0), 6),
        "fractional_kelly": round(max(kelly, 0.0), 6),
        "stake_fraction": round(stake_fraction, 6),
        "recommended_stake": stake,
        "action": action,
        "reason": reason,
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
) -> tuple[bool, str]:
    if not odds or odds <= 1:
        return False, "未有官方賠率"
    if race_status == "resulted":
        return False, "已完場，只作回測"
    if race_status == "live":
        return False, "已開跑，停止下注"
    if market == "PLACE" and source not in LIVE_ODDS_SOURCES:
        return False, "未有位置實時賠率"
    if source == "hkjc_results_final":
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
            expected_value = probability * dividend - 1.0 if dividend else None
            adjusted_ev = cost_adjusted_expected_value(expected_value, code)
            req_dividend = required_dividend(probability, code, RISK_PROFILES["standard"].min_expected_value)
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
                    "break_even_dividend": round(1.0 / probability, 2),
                    "required_dividend": round(req_dividend, 2) if req_dividend else None,
                    "dividend": dividend,
                    "dividend_status": dividend_row.get("dividend_status") if dividend_row else None,
                    "dividend_source": dividend_row.get("source") if dividend_row else None,
                    "expected_value": round(expected_value, 6) if expected_value is not None else None,
                    "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
                    "action": exotic_action(race_status),
                    "reason": exotic_reason(race_status),
                    "pool_rule": pool_rule_payload(code),
                }
            )
        product_candidates.sort(key=lambda item: float(item["probability"]), reverse=True)
        candidates.extend(product_candidates[:12])
    candidates.sort(
        key=lambda item: (
            exotic_priority(str(item["market"])),
            float(item["probability"]),
        ),
        reverse=True,
    )
    return candidates


def build_exotic_decisions(
    candidates: list[dict[str, Any]],
    race_status: str,
    bankroll: float,
    profile: RiskProfile,
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
        kelly = raw_kelly * profile.fractional_kelly
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
        )
        action = action_label(eligible, expected_value, edge, profile, reason)
        stake_fraction = capped_fraction if action == "有值博" else 0.0
        stake = round_stake_to_unit(bankroll * stake_fraction, str(candidate["market"])) if stake_fraction > 0 else 0.0
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
                "fair_odds": round(fair_odds, 3) if fair_odds else None,
                "market_probability": round(market_probability, 6) if market_probability is not None else None,
                "edge": round(edge, 6) if edge is not None else None,
                "expected_value": round(expected_value, 6) if expected_value is not None else None,
                "cost_adjusted_expected_value": round(adjusted_ev, 6) if adjusted_ev is not None else None,
                "required_expected_value": round(req_ev, 6),
                "required_edge": round(req_edge, 6),
                "required_dividend": round(req_dividend, 3) if req_dividend else None,
                "pool_rule": pool_rule_payload(str(candidate["market"])),
                "kelly_fraction": round(max(raw_kelly, 0.0), 6),
                "fractional_kelly": round(max(kelly, 0.0), 6),
                "stake_fraction": round(stake_fraction, 6),
                "recommended_stake": stake,
                "action": action,
                "reason": reason,
                "break_even_dividend": candidate.get("break_even_dividend"),
                "combination_key": candidate.get("combination_key"),
            }
        )
    return decisions


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
