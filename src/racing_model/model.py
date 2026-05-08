from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .features import FEATURE_NAMES, RunnerFeatures


FEATURE_LABELS_ZH = {
    "official_rating": "評分",
    "weight_lbs": "負磅",
    "draw_inside": "內檔",
    "draw_outside": "外檔",
    "age": "馬齡",
    "recent_speed": "近期速度",
    "adjusted_speed_figure": "標準化速度分",
    "recent_form": "近期狀態",
    "distance_fit": "路程適性",
    "going_fit": "場地適性",
    "jockey_win_rate": "騎師勝率",
    "trainer_win_rate": "練馬師勝率",
    "workout_score": "操練分",
    "pace_pressure": "步速形勢",
    "market_implied": "市場機率",
    "odds_delta_5m": "5分鐘賠率流",
    "odds_delta_2m": "2分鐘賠率流",
    "odds_delta_30s": "30秒賠率流",
    "late_steam": "臨場熱捧",
    "late_drift": "臨場轉冷",
    "same_day_inside_bias": "同日內檔偏差",
    "same_day_outside_bias": "同日外檔偏差",
    "same_day_pace_bias": "同日跑法偏差",
}


@dataclass
class RankingModel:
    feature_names: list[str]
    weights: dict[str, float]
    means: dict[str, float]
    scales: dict[str, float]
    bias: float = 0.0
    top3_weights: dict[str, float] | None = None
    top3_bias: float = 0.0
    top3_model_trained: bool = False

    def __post_init__(self) -> None:
        if self.top3_weights is None:
            self.top3_weights = {name: 0.0 for name in self.feature_names}

    @classmethod
    def new(cls) -> "RankingModel":
        return cls(
            feature_names=list(FEATURE_NAMES),
            weights={name: 0.0 for name in FEATURE_NAMES},
            means={name: 0.0 for name in FEATURE_NAMES},
            scales={name: 1.0 for name in FEATURE_NAMES},
        )

    def fit(self, races: list[list[RunnerFeatures]], epochs: int = 400, learning_rate: float = 0.03) -> None:
        self._fit_scaler(races)
        pairs = []
        for race in races:
            known = [runner for runner in race if runner.finish_position is not None]
            for better in known:
                for worse in known:
                    if int(better.finish_position or 99) < int(worse.finish_position or 99):
                        pairs.append((better, worse))
        if pairs:
            for _ in range(epochs):
                for better, worse in pairs:
                    diff = [
                        self._scaled(better, name) - self._scaled(worse, name)
                        for name in self.feature_names
                    ]
                    z = sum(self.weights[name] * value for name, value in zip(self.feature_names, diff))
                    probability = sigmoid(z)
                    error = 1.0 - probability
                    for name, value in zip(self.feature_names, diff):
                        self.weights[name] += learning_rate * error * value
        self._fit_top3_model(races, epochs=epochs, learning_rate=learning_rate * 0.6)

    def score_runner(self, runner: RunnerFeatures) -> float:
        return self.bias + sum(
            self.weights[name] * self._scaled(runner, name)
            for name in self.feature_names
        )

    def top3_score_runner(self, runner: RunnerFeatures) -> float:
        weights = self.top3_weights or {}
        return self.top3_bias + sum(
            weights.get(name, 0.0) * self._scaled(runner, name)
            for name in self.feature_names
        )

    def predict_race(self, runners: list[RunnerFeatures]) -> list[dict[str, float | str | None]]:
        scores = [self.score_runner(runner) for runner in runners]
        probabilities = softmax(scores)
        top3_scores = [self.top3_score_runner(runner) for runner in runners]
        top3_strengths = softmax(top3_scores) if self.top3_model_trained else probabilities
        top3_probabilities = plackett_luce_top_k_probabilities(top3_strengths, top_k=3)
        top3_source = "independent_top3_model" if self.top3_model_trained else "win_rank_distribution"
        rows = []
        for runner, score, probability, top3_score, top3_probability in zip(
            runners,
            scores,
            probabilities,
            top3_scores,
            top3_probabilities,
        ):
            market_probability = runner.features.get("market_implied", 0.0)
            value_gap = probability - market_probability
            expected_value = None
            if runner.latest_win_odds:
                expected_value = probability * runner.latest_win_odds - 1.0
            place_odds = runner.latest_place_odds
            place_odds_source = runner.latest_place_odds_source if runner.latest_place_odds else None
            place_market_probability = odds_to_probability(place_odds)
            top3_value_gap = top3_probability - place_market_probability
            top3_expected_value = None
            if place_odds:
                top3_expected_value = top3_probability * place_odds - 1.0
            rows.append(
                {
                    "race_id": runner.race_id,
                    "horse_id": runner.horse_id,
                    "horse_no": runner.horse_no,
                    "horse_name": runner.horse_name,
                    "last_six_runs": runner.last_six_runs,
                    "horse_name_zh": runner.horse_name_zh,
                    "display_name": runner.horse_name_zh or runner.horse_name,
                    "running_style": runner.running_style,
                    "jockey": runner.jockey,
                    "jockey_zh": runner.jockey_zh,
                    "display_jockey": runner.jockey_zh or runner.jockey,
                    "trainer": runner.trainer,
                    "trainer_zh": runner.trainer_zh,
                    "display_trainer": runner.trainer_zh or runner.trainer,
                    "draw": runner.draw,
                    "score": score,
                    "win_probability": probability,
                    "top3_score": top3_score,
                    "top3_probability": top3_probability,
                    "top3_model_source": top3_source,
                    "latest_win_odds": runner.latest_win_odds,
                    "latest_win_odds_source": runner.latest_win_odds_source,
                    "latest_place_odds": runner.latest_place_odds,
                    "place_odds": place_odds,
                    "place_odds_source": place_odds_source,
                    "market_probability": market_probability,
                    "adjusted_speed_figure": runner.features.get("adjusted_speed_figure", 0.0),
                    "odds_delta_5m": runner.features.get("odds_delta_5m", 0.0),
                    "odds_delta_2m": runner.features.get("odds_delta_2m", 0.0),
                    "odds_delta_30s": runner.features.get("odds_delta_30s", 0.0),
                    "late_steam": runner.features.get("late_steam", 0.0),
                    "late_drift": runner.features.get("late_drift", 0.0),
                    "same_day_inside_bias": runner.features.get("same_day_inside_bias", 0.0),
                    "same_day_outside_bias": runner.features.get("same_day_outside_bias", 0.0),
                    "same_day_pace_bias": runner.features.get("same_day_pace_bias", 0.0),
                    "place_market_probability": place_market_probability,
                    "value_gap": value_gap,
                    "top3_value_gap": top3_value_gap,
                    "expected_value": expected_value,
                    "top3_expected_value": top3_expected_value,
                    "explanation": self.explain_runner(runner),
                }
            )
        return sorted(rows, key=lambda row: float(row["win_probability"]), reverse=True)

    def explain_runner(self, runner: RunnerFeatures) -> dict[str, object]:
        factors = []
        for name in self.feature_names:
            raw_value = float(runner.features.get(name, 0.0))
            scaled_value = self._scaled(runner, name)
            contribution = self.weights[name] * scaled_value
            factors.append(
                {
                    "name": name,
                    "label": FEATURE_LABELS_ZH.get(name, name),
                    "value": raw_value,
                    "scaled": scaled_value,
                    "weight": self.weights[name],
                    "contribution": contribution,
                }
            )
        positive = sorted(factors, key=lambda item: float(item["contribution"]), reverse=True)
        negative = sorted(factors, key=lambda item: float(item["contribution"]))
        return {
            "positive": [item for item in positive if float(item["contribution"]) > 0][:5],
            "negative": [item for item in negative if float(item["contribution"]) < 0][:5],
            "all": factors,
        }

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "feature_names": self.feature_names,
                    "weights": self.weights,
                    "means": self.means,
                    "scales": self.scales,
                    "bias": self.bias,
                    "top3_weights": self.top3_weights,
                    "top3_bias": self.top3_bias,
                    "top3_model_trained": self.top3_model_trained,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | str) -> "RankingModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            feature_names=list(data["feature_names"]),
            weights={key: float(value) for key, value in data["weights"].items()},
            means={key: float(value) for key, value in data["means"].items()},
            scales={key: float(value) for key, value in data["scales"].items()},
            bias=float(data.get("bias", 0.0)),
            top3_weights={
                key: float(value)
                for key, value in data.get("top3_weights", {}).items()
            }
            or None,
            top3_bias=float(data.get("top3_bias", 0.0)),
            top3_model_trained=bool(data.get("top3_model_trained", False)),
        )

    def _fit_scaler(self, races: list[list[RunnerFeatures]]) -> None:
        values = {name: [] for name in self.feature_names}
        for race in races:
            for runner in race:
                for name in self.feature_names:
                    values[name].append(float(runner.features.get(name, 0.0)))
        for name, items in values.items():
            if not items:
                continue
            mean = sum(items) / len(items)
            variance = sum((item - mean) ** 2 for item in items) / len(items)
            self.means[name] = mean
            self.scales[name] = math.sqrt(variance) or 1.0

    def _scaled(self, runner: RunnerFeatures, name: str) -> float:
        return (float(runner.features.get(name, 0.0)) - self.means[name]) / self.scales[name]

    def _fit_top3_model(
        self,
        races: list[list[RunnerFeatures]],
        epochs: int,
        learning_rate: float,
    ) -> None:
        examples: list[tuple[RunnerFeatures, float, float]] = []
        for race in races:
            known = [runner for runner in race if runner.finish_position is not None]
            runner_count = len(known)
            if runner_count < 2:
                continue
            positive_count = min(3, runner_count)
            negative_count = max(runner_count - positive_count, 1)
            positive_weight = runner_count / (2.0 * positive_count)
            negative_weight = runner_count / (2.0 * negative_count)
            for runner in known:
                label = 1.0 if int(runner.finish_position or 99) <= positive_count else 0.0
                sample_weight = positive_weight if label else negative_weight
                examples.append((runner, label, sample_weight))
        if not examples or not any(label for _, label, _ in examples):
            self.top3_model_trained = False
            return
        if self.top3_weights is None:
            self.top3_weights = {name: 0.0 for name in self.feature_names}
        for _ in range(epochs):
            for runner, label, sample_weight in examples:
                z = self.top3_score_runner(runner)
                probability = sigmoid(z)
                error = (label - probability) * sample_weight
                self.top3_bias += learning_rate * error
                for name in self.feature_names:
                    self.top3_weights[name] += learning_rate * error * self._scaled(runner, name)
        self.top3_model_trained = True


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    max_score = max(scores)
    exps = [math.exp(score - max_score) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def odds_to_probability(odds: float | None) -> float:
    if odds is None or odds <= 1:
        return 0.0
    return 1.0 / odds




def plackett_luce_top_k_probabilities(probabilities: list[float], top_k: int = 3) -> list[float]:
    """Estimate each runner's chance of finishing inside top_k."""

    runner_count = len(probabilities)
    if runner_count == 0:
        return []
    if top_k <= 0:
        return [0.0 for _ in probabilities]
    if top_k >= runner_count:
        return [1.0 for _ in probabilities]

    strengths = [max(float(probability), 0.0) for probability in probabilities]
    total_strength = sum(strengths)
    if total_strength <= 0:
        return [min(top_k / runner_count, 1.0) for _ in probabilities]

    place_probabilities = [0.0 for _ in strengths]

    def visit(prefix: list[int], remaining: list[int], prefix_probability: float, remaining_strength: float) -> None:
        if len(prefix) == top_k:
            for runner_index in prefix:
                place_probabilities[runner_index] += prefix_probability
            return
        if remaining_strength <= 0:
            return
        for runner_index in remaining:
            strength = strengths[runner_index]
            if strength <= 0:
                continue
            next_probability = prefix_probability * strength / remaining_strength
            next_remaining = [index for index in remaining if index != runner_index]
            visit(prefix + [runner_index], next_remaining, next_probability, remaining_strength - strength)

    visit([], list(range(runner_count)), 1.0, total_strength)
    return [min(max(value, 0.0), 1.0) for value in place_probabilities]
