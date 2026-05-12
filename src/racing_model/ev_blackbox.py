from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, LATE_MARKET_FLOW_FEATURES, RunnerFeatures, build_race_features
from .model import RankingModel
from .storage import fetch_all


MARKET_FEATURES = {"market_implied", *LATE_MARKET_FLOW_FEATURES}
CORE_FEATURES = [
    "official_rating",
    "weight_lbs",
    "draw_inside",
    "draw_outside",
    "recent_speed",
    "adjusted_speed_figure",
    "recent_form",
    "distance_fit",
    "going_fit",
    "jockey_win_rate",
    "trainer_win_rate",
    "market_implied",
]


@dataclass(frozen=True)
class EVPolicy:
    min_win_ev: float
    min_win_probability: float
    min_win_odds: float
    max_win_odds: float
    max_win_bets_per_race: int
    min_place_ev: float
    min_place_probability: float
    max_place_bets_per_race: int
    stake: float


@dataclass(frozen=True)
class CandidateConfig:
    candidate_id: str
    feature_mode: str
    feature_names: list[str]
    epochs: int
    learning_rate: float
    temperature: float
    policy: EVPolicy


def run_ev_blackbox_training(
    conn,
    output_dir: Path | str = "reports",
    holdout_date: str = "2026-05-09",
    trials: int = 80,
    seed: int = 20260509,
    min_epochs: int = 80,
    max_epochs: int = 260,
    stake: float = 10.0,
    validation_fraction: float = 0.18,
    min_validation_races: int = 45,
) -> dict[str, Any]:
    race_ids = eligible_resulted_race_ids(conn)
    race_dates = race_date_lookup(conn)
    holdout_ids = [race_id for race_id in race_ids if race_dates.get(race_id) == holdout_date]
    pre_holdout_ids = [race_id for race_id in race_ids if race_dates.get(race_id, "") < holdout_date]
    if not holdout_ids:
        holdout_ids = race_ids[-max(1, min(12, len(race_ids) // 10)) :]
        pre_holdout_ids = [race_id for race_id in race_ids if race_id not in set(holdout_ids)]

    validation_size = max(min_validation_races, int(len(pre_holdout_ids) * validation_fraction))
    validation_size = min(max(validation_size, 1), max(len(pre_holdout_ids) - 1, 1))
    train_ids = pre_holdout_ids[:-validation_size]
    validation_ids = pre_holdout_ids[-validation_size:]
    if not train_ids or not validation_ids:
        raise ValueError("Not enough eligible races for black-box EV training.")

    selected_ids = list(dict.fromkeys([*train_ids, *validation_ids, *holdout_ids]))
    race_features = {race_id: build_race_features(conn, race_id) for race_id in selected_ids}
    rng = random.Random(seed)
    candidates = []
    best: dict[str, Any] | None = None

    for index in range(max(trials, 1)):
        config = random_candidate_config(rng, index, min_epochs, max_epochs, stake)
        model = model_from_config(config)
        model.fit([race_features[race_id] for race_id in train_ids], epochs=config.epochs, learning_rate=config.learning_rate)
        validation = replay_races(model, config.policy, [race_features[race_id] for race_id in validation_ids])
        score = blackbox_objective(validation)
        row = {
            "candidate": candidate_public(config),
            "validation": validation["summary"],
            "score": score,
        }
        candidates.append(row)
        if best is None or score > float(best["score"]):
            best = row

    assert best is not None
    best_config = config_from_public(best["candidate"])
    final_model = model_from_config(best_config)
    final_model.fit(
        [race_features[race_id] for race_id in [*train_ids, *validation_ids]],
        epochs=best_config.epochs,
        learning_rate=best_config.learning_rate,
    )
    validation_replay = replay_races(final_model, best_config.policy, [race_features[race_id] for race_id in validation_ids])
    holdout_replay = replay_races(final_model, best_config.policy, [race_features[race_id] for race_id in holdout_ids])
    place_holdout = replay_races(final_model, best_config.policy, [race_features[race_id] for race_id in holdout_ids], include_place=True)
    full_pre_holdout_replay = replay_races(
        final_model,
        best_config.policy,
        [race_features[race_id] for race_id in [*train_ids, *validation_ids]],
    )

    created_at = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target_dir = Path(output_dir) / f"ev_blackbox_{created_at}"
    target_dir.mkdir(parents=True, exist_ok=True)
    model_path = target_dir / "best_model.json"
    report_path = target_dir / "report.json"
    final_model.save(model_path)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_mode": "blackbox_ev_walk_forward_holdout",
        "guardrails": {
            "holdout_date": holdout_date,
            "holdout_used_for_selection": False,
            "candidate_selection": "validation_roi_drawdown_hitrate_bet_count",
            "promotion": "manual_only_after_oos_review",
        },
        "sample": {
            "eligible_races": len(race_ids),
            "train_races": len(train_ids),
            "validation_races": len(validation_ids),
            "holdout_races": len(holdout_ids),
            "train_start": race_dates.get(train_ids[0]),
            "train_end": race_dates.get(train_ids[-1]),
            "validation_start": race_dates.get(validation_ids[0]),
            "validation_end": race_dates.get(validation_ids[-1]),
            "holdout_date": holdout_date,
        },
        "best_candidate": best["candidate"],
        "candidate_count": len(candidates),
        "top_candidates": sorted(candidates, key=lambda item: float(item["score"]), reverse=True)[:10],
        "validation_replay": validation_replay,
        "pre_holdout_replay": full_pre_holdout_replay,
        "holdout_win_replay": holdout_replay,
        "holdout_place_replay": place_holdout,
        "model_path": str(model_path),
        "report_path": str(report_path),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def eligible_resulted_race_ids(conn) -> list[str]:
    return [
        str(row["race_id"])
        for row in fetch_all(
            conn,
            """
            SELECT r.race_id
            FROM races r
            WHERE EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
              AND EXISTS (SELECT 1 FROM runners ru WHERE ru.race_id = r.race_id)
              AND NOT EXISTS (
                SELECT 1
                FROM runners ru
                LEFT JOIN odds_ticks o
                  ON o.race_id = ru.race_id
                 AND o.horse_id = ru.horse_id
                 AND o.win_odds > 0
                WHERE ru.race_id = r.race_id
                  AND o.horse_id IS NULL
              )
            ORDER BY r.date, r.race_id
            """,
        )
    ]


def race_date_lookup(conn) -> dict[str, str]:
    return {
        str(row["race_id"]): str(row["date"])
        for row in fetch_all(conn, "SELECT race_id, date FROM races")
    }


def random_candidate_config(
    rng: random.Random,
    index: int,
    min_epochs: int,
    max_epochs: int,
    stake: float,
) -> CandidateConfig:
    mode = rng.choice(["all", "no_market", "market_only", "core", "core_no_late"])
    features = feature_names_for_mode(mode)
    min_ev = rng.uniform(0.02, 0.75)
    policy = EVPolicy(
        min_win_ev=min_ev,
        min_win_probability=rng.uniform(0.035, 0.22),
        min_win_odds=rng.uniform(1.4, 4.0),
        max_win_odds=rng.choice([18.0, 28.0, 45.0, 80.0, 120.0]),
        max_win_bets_per_race=rng.choice([1, 1, 2, 2, 3]),
        min_place_ev=max(0.01, min_ev * rng.uniform(0.35, 0.85)),
        min_place_probability=rng.uniform(0.18, 0.42),
        max_place_bets_per_race=rng.choice([1, 2, 2, 3]),
        stake=stake,
    )
    return CandidateConfig(
        candidate_id=f"candidate_{index:04d}",
        feature_mode=mode,
        feature_names=features,
        epochs=rng.randint(min_epochs, max(min_epochs, max_epochs)),
        learning_rate=10 ** rng.uniform(math.log10(0.006), math.log10(0.07)),
        temperature=rng.uniform(0.75, 2.4),
        policy=policy,
    )


def feature_names_for_mode(mode: str) -> list[str]:
    if mode == "no_market":
        return [name for name in FEATURE_NAMES if name not in MARKET_FEATURES]
    if mode == "market_only":
        return ["market_implied", *LATE_MARKET_FLOW_FEATURES]
    if mode == "core":
        return [name for name in CORE_FEATURES if name in FEATURE_NAMES]
    if mode == "core_no_late":
        return [name for name in CORE_FEATURES if name not in set(LATE_MARKET_FLOW_FEATURES)]
    return list(FEATURE_NAMES)


def model_from_config(config: CandidateConfig) -> RankingModel:
    return RankingModel(
        feature_names=list(config.feature_names),
        weights={name: 0.0 for name in config.feature_names},
        means={name: 0.0 for name in config.feature_names},
        scales={name: 1.0 for name in config.feature_names},
        temperature=config.temperature,
    )


def replay_races(
    model: RankingModel,
    policy: EVPolicy,
    races: list[list[RunnerFeatures]],
    include_place: bool = False,
) -> dict[str, Any]:
    tickets = []
    race_summaries = []
    for runners in races:
        predictions = model.predict_race(runners)
        result_by_horse = {runner.horse_id: int(runner.finish_position or 99) for runner in runners}
        win_candidates = sorted(
            [
                row
                for row in predictions
                if row.get("latest_win_odds")
                and float(row["latest_win_odds"] or 0) >= policy.min_win_odds
                and float(row["latest_win_odds"] or 0) <= policy.max_win_odds
                and float(row["win_probability"] or 0) >= policy.min_win_probability
                and float(row.get("expected_value") or -999) >= policy.min_win_ev
            ],
            key=lambda row: float(row.get("expected_value") or -999),
            reverse=True,
        )[: policy.max_win_bets_per_race]
        race_tickets = []
        for row in win_candidates:
            ticket = settle_ticket(
                race_id=str(row["race_id"]),
                horse_id=str(row["horse_id"]),
                horse_no=row.get("horse_no"),
                horse_name=str(row.get("display_name") or row.get("horse_name") or row["horse_id"]),
                market="WIN",
                probability=float(row["win_probability"] or 0),
                odds=float(row["latest_win_odds"] or 0),
                expected_value=float(row.get("expected_value") or 0),
                finish_position=result_by_horse.get(str(row["horse_id"]), 99),
                stake=policy.stake,
                winning_positions={1},
            )
            tickets.append(ticket)
            race_tickets.append(ticket)
        if include_place:
            place_candidates = sorted(
                [
                    row
                    for row in predictions
                    if row.get("latest_place_odds")
                    and float(row["top3_probability"] or 0) >= policy.min_place_probability
                    and float(row.get("top3_expected_value") or -999) >= policy.min_place_ev
                ],
                key=lambda row: float(row.get("top3_expected_value") or -999),
                reverse=True,
            )[: policy.max_place_bets_per_race]
            for row in place_candidates:
                ticket = settle_ticket(
                    race_id=str(row["race_id"]),
                    horse_id=str(row["horse_id"]),
                    horse_no=row.get("horse_no"),
                    horse_name=str(row.get("display_name") or row.get("horse_name") or row["horse_id"]),
                    market="PLACE",
                    probability=float(row["top3_probability"] or 0),
                    odds=float(row["latest_place_odds"] or 0),
                    expected_value=float(row.get("top3_expected_value") or 0),
                    finish_position=result_by_horse.get(str(row["horse_id"]), 99),
                    stake=policy.stake,
                    winning_positions={1, 2, 3},
                )
                tickets.append(ticket)
                race_tickets.append(ticket)
        race_summaries.append(
            {
                "race_id": runners[0].race_id if runners else "",
                "ticket_count": len(race_tickets),
                "profit": round(sum(float(ticket["profit"]) for ticket in race_tickets), 4),
                "tickets": race_tickets,
                "top_pick": prediction_public(predictions[0], result_by_horse) if predictions else None,
                "top3_hit": any(result_by_horse.get(str(row["horse_id"]), 99) == 1 for row in predictions[:3]),
            }
        )
    return {
        "summary": summarize_tickets(tickets, len(races), race_summaries),
        "races": race_summaries,
        "tickets": tickets,
    }


def settle_ticket(
    race_id: str,
    horse_id: str,
    horse_no: Any,
    horse_name: str,
    market: str,
    probability: float,
    odds: float,
    expected_value: float,
    finish_position: int,
    stake: float,
    winning_positions: set[int],
) -> dict[str, Any]:
    hit = finish_position in winning_positions
    returned = stake * odds if hit else 0.0
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": horse_name,
        "market": market,
        "probability": probability,
        "odds": odds,
        "expected_value": expected_value,
        "stake": stake,
        "finish_position": finish_position,
        "hit": hit,
        "returned": round(returned, 4),
        "profit": round(returned - stake, 4),
    }


def summarize_tickets(tickets: list[dict[str, Any]], race_count: int, races: list[dict[str, Any]]) -> dict[str, Any]:
    staked = sum(float(ticket["stake"]) for ticket in tickets)
    returned = sum(float(ticket["returned"]) for ticket in tickets)
    profit = returned - staked
    hits = sum(1 for ticket in tickets if ticket["hit"])
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for ticket in tickets:
        cumulative += float(ticket["profit"])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "race_count": race_count,
        "races_with_bets": sum(1 for race in races if race["ticket_count"]),
        "ticket_count": len(tickets),
        "hits": hits,
        "hit_rate": hits / len(tickets) if tickets else 0.0,
        "staked": round(staked, 4),
        "returned": round(returned, 4),
        "profit": round(profit, 4),
        "roi": profit / staked if staked else 0.0,
        "max_drawdown": round(max_drawdown, 4),
        "top_pick_hit_rate": top_pick_hit_rate(races),
        "top3_hit_rate": sum(1 for race in races if race.get("top3_hit")) / race_count if race_count else 0.0,
    }


def top_pick_hit_rate(races: list[dict[str, Any]]) -> float:
    if not races:
        return 0.0
    hits = 0
    for race in races:
        top_pick = race.get("top_pick") or {}
        if top_pick.get("finish_position") == 1:
            hits += 1
    return hits / len(races)


def prediction_public(row: dict[str, Any], result_by_horse: dict[str, int]) -> dict[str, Any]:
    return {
        "horse_id": row.get("horse_id"),
        "horse_no": row.get("horse_no"),
        "horse_name": row.get("display_name") or row.get("horse_name"),
        "win_probability": row.get("win_probability"),
        "latest_win_odds": row.get("latest_win_odds"),
        "expected_value": row.get("expected_value"),
        "finish_position": result_by_horse.get(str(row.get("horse_id")), 99),
    }


def blackbox_objective(replay: dict[str, Any]) -> float:
    summary = replay["summary"]
    bets = int(summary["ticket_count"])
    if bets < 12:
        return -10.0 + bets * 0.05
    roi = float(summary["roi"])
    hit_rate = float(summary["hit_rate"])
    retention = min(1.0, bets / 60.0)
    drawdown = float(summary["max_drawdown"])
    staked = max(float(summary["staked"]), 1.0)
    drawdown_penalty = drawdown / staked
    return roi * retention + hit_rate * 0.25 + math.log1p(bets) * 0.025 - drawdown_penalty * 0.18


def candidate_public(config: CandidateConfig) -> dict[str, Any]:
    return {
        "candidate_id": config.candidate_id,
        "feature_mode": config.feature_mode,
        "feature_names": config.feature_names,
        "epochs": config.epochs,
        "learning_rate": config.learning_rate,
        "temperature": config.temperature,
        "policy": asdict(config.policy),
    }


def config_from_public(row: dict[str, Any]) -> CandidateConfig:
    policy = EVPolicy(**row["policy"])
    return CandidateConfig(
        candidate_id=str(row["candidate_id"]),
        feature_mode=str(row["feature_mode"]),
        feature_names=list(row["feature_names"]),
        epochs=int(row["epochs"]),
        learning_rate=float(row["learning_rate"]),
        temperature=float(row["temperature"]),
        policy=policy,
    )
