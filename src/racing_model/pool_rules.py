from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PoolRule:
    market: str
    label: str
    payout_rate: float
    min_unit: float
    ordered: bool
    size: int
    top_k: int
    efficiency_buffer: float

    @property
    def takeout_rate(self) -> float:
        return round(1.0 - self.payout_rate, 6)


POOL_RULES = {
    "WIN": PoolRule("WIN", "Win", 0.825, 10.0, False, 1, 1, 0.010),
    "PLACE": PoolRule("PLACE", "Place", 0.825, 10.0, False, 1, 3, 0.010),
    "QIN": PoolRule("QIN", "Quinella", 0.825, 10.0, False, 2, 2, 0.015),
    "QPL": PoolRule("QPL", "Quinella Place", 0.825, 10.0, False, 2, 3, 0.015),
    "FCT": PoolRule("FCT", "Forecast", 0.805, 10.0, True, 2, 2, 0.020),
    "TRIO": PoolRule("TRIO", "Trio", 0.770, 10.0, False, 3, 3, 0.025),
    "TCE": PoolRule("TCE", "Tierce", 0.750, 10.0, True, 3, 3, 0.030),
    "FIRST4": PoolRule("FIRST4", "First 4", 0.750, 10.0, False, 4, 4, 0.035),
    "QUARTET": PoolRule("QUARTET", "Quartet", 0.750, 10.0, True, 4, 4, 0.040),
}


def pool_rule(market: str) -> PoolRule:
    return POOL_RULES.get(str(market).upper(), POOL_RULES["WIN"])


def pool_rule_payload(market: str) -> dict[str, float | int | str | bool]:
    rule = pool_rule(market)
    payload = asdict(rule)
    payload["takeout_rate"] = rule.takeout_rate
    return payload


def all_pool_rules_payload() -> dict[str, dict[str, float | int | str | bool]]:
    return {market: pool_rule_payload(market) for market in POOL_RULES}


def cost_buffer(market: str) -> float:
    return pool_rule(market).efficiency_buffer


def required_expected_value(market: str, base_min_ev: float) -> float:
    return float(base_min_ev or 0.0) + cost_buffer(market)


def required_edge(market: str, base_min_edge: float) -> float:
    return float(base_min_edge or 0.0) + cost_buffer(market)


def required_dividend(probability: float | None, market: str, base_min_ev: float) -> float | None:
    if probability is None or probability <= 0:
        return None
    return (1.0 + required_expected_value(market, base_min_ev)) / probability


def cost_adjusted_expected_value(expected_value: float | None, market: str) -> float | None:
    if expected_value is None:
        return None
    return float(expected_value) - cost_buffer(market)


def round_stake_to_unit(stake: float, market: str) -> float:
    unit = pool_rule(market).min_unit
    if stake <= 0 or unit <= 0:
        return 0.0
    units = int(stake // unit)
    return round(units * unit, 1)
