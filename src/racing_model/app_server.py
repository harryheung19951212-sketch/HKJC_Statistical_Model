from __future__ import annotations

import json
import gzip
import mimetypes
import uuid
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .adaptive import adaptive_prediction_policy, adaptive_predict_race
from .backtest import run_backtest
from .backfill import (
    align_resulted_race_data,
    complete_repaired_runners,
    data_quality_report,
    load_hkjc_date_range,
    normalize_hkjc_date,
    repair_orphan_result_runners,
    train_model_if_requested,
)
from .betting import build_betting_decisions
from .calibration_gate import build_calibration_gate
from .betting_ledger import (
    annotate_pool_choice_context,
    auto_execution_payload,
    betting_ledger_report,
    confirm_betting_recommendation,
    recommendation_key,
    reconcile_betting_ledger,
    record_betting_payload,
    refresh_open_betting_prices,
)
from .config import display_database_target, get_settings
from .coverage import build_coverage_report
from .error_taxonomy import error_taxonomy_report
from .evolution import evaluate_model_evolution, generate_codex_iteration, generate_openai_iteration
from .exotic_dividends import exotic_dividend_report, load_exotic_dividend_lookup, upsert_exotic_dividends
from .exotic_live import build_exotic_dividend_provider, refresh_exotic_dividends
from .feed_health import odds_feed_health
from .features import build_race_features
from .live import hong_kong_tz, load_hkjc_race_day, refresh_hkjc_results_if_available
from .market_flow import market_flow_report
from .model import RankingModel
from .model_compare import dual_model_backtest, dual_model_comparison
from .model_registry import model_registry_report, promote_latest_model, run_and_record_model_registry
from .odds import (
    backfill_final_place_odds,
    build_odds_provider,
    build_official_odds_provider,
    odds_history,
    refresh_odds,
)
from .pace import annotate_predictions_with_pace
from .pool_replay import pool_replay_calibration, pool_replay_report
from .promotion_scorecard import build_promotion_scorecard, promotion_scorecard
from .storage import (
    connect,
    fetch_all,
    final_place_odds_completeness,
    freeze_final_place_snapshots,
    init_db,
    latest_odds_by_race,
    refresh_race_statuses,
    upsert_race_status,
)
from .walk_forward import run_walk_forward_versions
from .weather import race_weather


APP_DIR = Path(__file__).resolve().parent / "web"


def pool_replay_context_key(race_context: dict[str, object] | None) -> str:
    if not race_context:
        return "global"
    return "|".join(
        str(race_context.get(key) or "")
        for key in ("track", "course", "distance_m", "going", "class_rating")
    )


class AppState:
    def __init__(self, model_path: Path, odds_interval_seconds: int) -> None:
        self.settings = get_settings()
        self.model_path = model_path
        self.odds_interval_seconds = odds_interval_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.jobs: dict[str, dict[str, object]] = {}
        self.jobs_lock = threading.Lock()
        self.active_race_id: str | None = None
        self.active_race_seen_at = 0.0
        self.active_race_external_clock = False
        self.active_race_ttl_seconds = max(90, odds_interval_seconds * 3)
        self.active_race_lock = threading.Lock()
        self.exotic_refreshing_races: set[str] = set()
        self.exotic_refresh_lock = threading.Lock()
        self.betting_recording_races: set[str] = set()
        self.betting_record_lock = threading.Lock()
        self.prediction_cache: dict[str, dict[str, object]] = {}
        self.prediction_cache_lock = threading.Lock()
        self.policy_cache: dict[str, object] = {"expires_at": 0.0, "policy": None, "refreshing": False}
        self.policy_lock = threading.Lock()
        self.calibration_cache: dict[str, object] = {"expires_at": 0.0, "gate": None}
        self.calibration_lock = threading.Lock()
        self.pool_replay_gate_cache: dict[str, object] = {"expires_at": 0.0, "gate": None}
        self.pool_replay_gate_lock = threading.Lock()

    def model(self) -> RankingModel:
        if self.model_path.exists():
            return RankingModel.load(self.model_path)
        return RankingModel.new()

    def prediction_policy(self, conn, model: RankingModel) -> dict[str, object]:
        now = time.monotonic()
        with self.policy_lock:
            cached = self.policy_cache.get("policy")
            if cached and now < float(self.policy_cache.get("expires_at") or 0):
                return dict(cached)
            if not self.policy_cache.get("refreshing"):
                self.policy_cache["refreshing"] = True
                threading.Thread(target=self.refresh_prediction_policy_cache, daemon=True).start()
        return fast_prediction_policy(conn)

    def refresh_prediction_policy_cache(self) -> None:
        try:
            time.sleep(15.0)
            with connect(self.settings.db_path) as conn:
                policy = adaptive_prediction_policy(conn, self.model())
        except Exception:
            policy = None
        with self.policy_lock:
            self.policy_cache["refreshing"] = False
            if policy:
                self.policy_cache = {"expires_at": time.monotonic() + 300.0, "policy": dict(policy), "refreshing": False}

    def calibration_gate(self, conn, model: RankingModel) -> dict[str, object]:
        now = time.monotonic()
        with self.calibration_lock:
            cached = self.calibration_cache.get("gate")
            if cached and now < float(self.calibration_cache.get("expires_at") or 0):
                return dict(cached)
        try:
            gate = build_calibration_gate(conn, model)
        except Exception as exc:
            gate = {
                "status": "unverified",
                "label": "校準暫未確認",
                "message": f"校準 gate 暫時未能計算：{str(exc)[:160]}。注碼先減半。",
                "stake_factor": 0.5,
                "promote_allowed": False,
            }
        with self.calibration_lock:
            self.calibration_cache = {"expires_at": now + 300.0, "gate": dict(gate)}
        return dict(gate)

    def pool_replay_gate(self, conn, race_context: dict[str, object] | None = None) -> dict[str, object]:
        now = time.monotonic()
        context_key = pool_replay_context_key(race_context)
        with self.pool_replay_gate_lock:
            cached = self.pool_replay_gate_cache.get("gate")
            cached_key = self.pool_replay_gate_cache.get("context_key")
            if cached and cached_key == context_key and now < float(self.pool_replay_gate_cache.get("expires_at") or 0):
                return dict(cached)
        try:
            gate = pool_replay_calibration(pool_replay_report(conn), race_context=race_context)
        except Exception as exc:
            gate = {
                "status": "unverified",
                "label": "分彩池 replay 暫未確認",
                "message": f"分池 replay gate 暫時未能計算：{str(exc)[:160]}。先不改變注碼。",
                "markets": {},
                "blocked_markets": [],
                "reduced_markets": [],
                "sample_building_markets": [],
            }
        with self.pool_replay_gate_lock:
            self.pool_replay_gate_cache = {"expires_at": now + 60.0, "gate": dict(gate), "context_key": context_key}
        return dict(gate)

    def focus_race(self, race_id: str, now: float | None = None) -> dict[str, object]:
        timestamp = time.monotonic() if now is None else now
        with self.active_race_lock:
            self.active_race_id = race_id
            self.active_race_seen_at = timestamp
            self.active_race_external_clock = now is not None
        return {
            "active_race_id": race_id,
            "expires_in_seconds": None,
            "persistent": True,
        }

    def sleep_race(self, race_id: str | None = None) -> dict[str, object]:
        with self.active_race_lock:
            if race_id is None or race_id == self.active_race_id:
                self.active_race_id = None
                self.active_race_seen_at = 0.0
                self.active_race_external_clock = False
        return {"active_race_id": self.active_race_id}

    def active_race(self, now: float | None = None) -> str | None:
        with self.active_race_lock:
            if not self.active_race_id:
                return None
            return self.active_race_id

    def active_race_expires_in(self, now: float | None = None) -> int | None:
        with self.active_race_lock:
            if not self.active_race_id:
                return 0
            return None

    def active_race_timestamp(self, now: float | None = None) -> float:
        if now is not None:
            return now
        if self.active_race_external_clock:
            return self.active_race_seen_at
        return time.monotonic()

    def start_refresh_loop(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.refresh_loop, daemon=True)
        self.thread.start()

    def refresh_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with connect(self.settings.db_path) as conn:
                    scope = "active" if self.active_race() else "global"
                    run_lifecycle_step(conn, self, scope=scope)
            except Exception:
                pass
            self.stop_event.wait(self.odds_interval_seconds)

    def start_exotic_refresh_job(self, race_id: str) -> bool:
        with self.exotic_refresh_lock:
            if race_id in self.exotic_refreshing_races:
                return False
            self.exotic_refreshing_races.add(race_id)

        def worker() -> None:
            try:
                with connect(self.settings.db_path) as conn:
                    if race_lifecycle_status(conn, race_id) in {"scheduled", "live"}:
                        refresh_exotic_dividends(conn, race_id, build_exotic_dividend_provider(self.settings))
            except Exception:
                pass
            finally:
                with self.exotic_refresh_lock:
                    self.exotic_refreshing_races.discard(race_id)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def start_betting_record_job(self, race: dict[str, object], payload: dict[str, object], model_path: Path | str) -> bool:
        race_id = str(race.get("race_id") or "")
        if not race_id:
            return False
        with self.betting_record_lock:
            if race_id in self.betting_recording_races:
                return False
            self.betting_recording_races.add(race_id)

        race_copy = dict(race)
        payload_copy = dict(payload)
        payload_copy["tickets"] = [dict(ticket) for ticket in payload.get("tickets", []) if isinstance(ticket, dict)]

        def worker() -> None:
            try:
                with connect(self.settings.db_path) as conn:
                    record_betting_payload(conn, race_copy, payload_copy, model_path)
            except Exception:
                pass
            finally:
                with self.betting_record_lock:
                    self.betting_recording_races.discard(race_id)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def cache_race_predictions(self, race_id: str, predictions: list[dict[str, object]], policy: dict[str, object]) -> None:
        with self.prediction_cache_lock:
            self.prediction_cache[race_id] = {
                "created_at": time.monotonic(),
                "predictions": [dict(row) for row in predictions],
                "policy": dict(policy),
            }

    def cached_race_predictions(self, race_id: str, max_age_seconds: float) -> dict[str, object] | None:
        with self.prediction_cache_lock:
            cached = self.prediction_cache.get(race_id)
            if not cached:
                return None
            if time.monotonic() - float(cached.get("created_at") or 0.0) > max_age_seconds:
                return None
            return {
                "predictions": [dict(row) for row in cached.get("predictions", [])],
                "policy": dict(cached.get("policy") or {}),
            }

    def start_race_day_job(self, race_date: str, venue: str, race_count: int) -> dict[str, object]:
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "running",
            "message": "準備載入賽日",
            "current": 0,
            "total": race_count,
            "result": None,
            "error": None,
        }
        with self.jobs_lock:
            self.jobs[job_id] = job

        def update(current: int, total: int, message: str) -> None:
            with self.jobs_lock:
                self.jobs[job_id].update({"current": current, "total": total, "message": message})

        def worker() -> None:
            try:
                with connect(self.settings.db_path) as conn:
                    result = load_hkjc_race_day(
                        conn,
                        race_date,
                        venue,
                        race_count,
                        self.settings.user_agent,
                        self.settings.backfill_request_delay_seconds,
                        progress=update,
                    )
                with self.jobs_lock:
                    self.jobs[job_id].update(
                        {
                            "status": "done",
                            "message": "載入完成",
                            "current": race_count,
                            "result": result,
                        }
                    )
            except Exception as exc:
                with self.jobs_lock:
                    self.jobs[job_id].update({"status": "error", "message": "載入失敗", "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()
        return dict(job)

    def start_backfill_job(
        self,
        start_date: str,
        end_date: str,
        venue: str,
        race_count: int,
        train_epochs: int,
    ) -> dict[str, object]:
        job_id = uuid.uuid4().hex
        total = max(race_count, 1)
        job = {
            "job_id": job_id,
            "kind": "backfill",
            "status": "running",
            "message": "準備回填歷史資料",
            "current": 0,
            "total": total,
            "result": None,
            "error": None,
        }
        with self.jobs_lock:
            self.jobs[job_id] = job

        def update(current: int, total_units: int, message: str) -> None:
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "current": current,
                        "total": total_units,
                        "message": message,
                    }
                )

        def worker() -> None:
            try:
                with connect(self.settings.db_path) as conn:
                    result = load_hkjc_date_range(
                        conn,
                        start_date,
                        end_date,
                        venue,
                        race_count,
                        self.settings.user_agent,
                        self.settings.backfill_request_delay_seconds,
                        model_path=self.model_path,
                        train_epochs=train_epochs,
                        progress=update,
                    )
                with self.jobs_lock:
                    self.jobs[job_id].update(
                        {
                            "status": "done",
                            "message": "歷史回填完成",
                            "current": result.get("requested_races", 0),
                            "total": result.get("requested_races", 0),
                            "result": result,
                        }
                    )
            except Exception as exc:
                with self.jobs_lock:
                    self.jobs[job_id].update({"status": "error", "message": "歷史回填失敗", "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()
        return dict(job)

    def start_runner_completion_job(self) -> dict[str, object]:
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "kind": "runner_completion",
            "status": "running",
            "message": "準備補完馬匹資料",
            "current": 0,
            "total": 1,
            "result": None,
            "error": None,
        }
        with self.jobs_lock:
            self.jobs[job_id] = job

        def update(message: str, current: int = 0, total: int = 1) -> None:
            with self.jobs_lock:
                self.jobs[job_id].update({"current": current, "total": total, "message": message})

        def worker() -> None:
            try:
                update("補完馬匹資料中", 0, 2)
                with connect(self.settings.db_path) as conn:
                    completion = complete_repaired_runners(
                        conn,
                        self.settings.user_agent,
                        self.settings.backfill_request_delay_seconds,
                    )
                update("更新資料質素報告", 1, 2)
                with connect(self.settings.db_path) as conn:
                    alignment = align_resulted_race_data(conn)
                    quality = data_quality_report(conn)
                result = {
                    "status": "done",
                    "completion": completion,
                    "alignment": alignment,
                    "quality": quality,
                    "training": {"trained": False, "reason": "runner_completion_no_inline_training"},
                    "model_versions": None,
                }
                with self.jobs_lock:
                    self.jobs[job_id].update(
                        {
                            "status": "done",
                            "message": "補完完成",
                            "current": 2,
                            "total": 2,
                            "result": result,
                        }
                    )
            except Exception as exc:
                with self.jobs_lock:
                    self.jobs[job_id].update({"status": "error", "message": "補完失敗", "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()
        return dict(job)

    def get_job(self, job_id: str) -> dict[str, object] | None:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None


def run_server(host: str, port: int, model_path: Path, odds_interval_seconds: int) -> ThreadingHTTPServer:
    settings = get_settings()
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_model_file(conn, model_path)
    state = AppState(model_path, odds_interval_seconds)
    state.start_refresh_loop()

    class Handler(RacingRequestHandler):
        app_state = state

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Racing app running at http://{host}:{port}")
    print(f"Odds refresh loop: every {odds_interval_seconds}s")
    try:
        server.serve_forever()
    finally:
        state.stop_event.set()
    return server


def ensure_model_file(conn, model_path: Path, epochs: int = 120) -> dict[str, object]:
    if model_path.exists():
        return {"trained": False, "model_path": str(model_path), "reason": "model_exists"}
    return train_model_if_requested(conn, model_path, epochs)


class RacingRequestHandler(BaseHTTPRequestHandler):
    app_state: AppState

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_post(parsed.path, parse_qs(parsed.query))
            return
        self.send_error(404)

    def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/job":
            job_id = required_query(query, "id")
            job = self.app_state.get_job(job_id)
            self.send_json(job or {"job_id": job_id, "status": "missing"})
            return
        with connect(self.app_state.settings.db_path) as conn:
            refresh_race_statuses(conn)
            if path == "/api/state":
                self.send_json(api_state(conn, self.app_state))
            elif path == "/api/races":
                self.send_json(api_races(conn))
            elif path == "/api/backtest":
                result = run_backtest(conn, self.app_state.model(), max_races=80)
                self.send_json(asdict(result))
            elif path == "/api/evolution":
                self.send_json(evaluate_model_evolution(conn, self.app_state.model(), max_races=40))
            elif path == "/api/error-taxonomy":
                refresh = query_bool(query, "refresh", False)
                self.send_json(error_taxonomy_report(conn, self.app_state.model(), refresh=refresh))
            elif path == "/api/model-versions":
                self.send_json(run_walk_forward_versions(conn, epochs=20, max_folds=6, max_train_races=24))
            elif path == "/api/promotion-scorecard":
                self.send_json(promotion_scorecard(conn))
            elif path == "/api/model-registry":
                self.send_json(model_registry_report(conn))
            elif path == "/api/lifecycle":
                self.send_json(api_lifecycle(conn, self.app_state))
            elif path == "/api/data-quality":
                self.send_json(data_quality_report(conn))
            elif path == "/api/race-dashboard":
                race_id = required_query(query, "race_id")
                bankroll = query_float(query, "bankroll", 10000.0)
                risk = query.get("risk", ["standard"])[0] or "standard"
                self.send_json(api_race_dashboard(conn, self.app_state, race_id, bankroll, risk))
            elif path == "/api/analytics-dashboard":
                include_coverage = query_bool(query, "include_coverage", False)
                fast = query_bool(query, "fast", True)
                self.send_json(api_analytics_dashboard(conn, self.app_state, include_coverage, fast=fast))
            elif path == "/api/coverage":
                self.send_json(build_coverage_report(conn))
            elif path == "/api/odds-history":
                race_id = required_query(query, "race_id")
                self.send_json(odds_history(conn, race_id))
            elif path == "/api/market-flow":
                race_id = required_query(query, "race_id")
                self.send_json(market_flow_report(conn, race_id))
            elif path == "/api/odds-feed":
                race_id = required_query(query, "race_id")
                self.send_json(odds_feed_health(conn, race_id, self.app_state.odds_interval_seconds))
            elif path == "/api/weather":
                race_id = required_query(query, "race_id")
                self.send_json(race_weather(conn, race_id))
            elif path == "/api/results":
                race_id = required_query(query, "race_id")
                model = self.app_state.model()
                policy = self.app_state.prediction_policy(conn, model)
                self.send_json(api_results(conn, model, race_id, policy=policy))
            elif path == "/api/predictions":
                race_id = required_query(query, "race_id")
                model = self.app_state.model()
                policy = self.app_state.prediction_policy(conn, model)
                self.send_json(api_predictions(conn, model, race_id, policy))
            elif path == "/api/model-comparison":
                race_id = required_query(query, "race_id")
                self.send_json(dual_model_comparison(conn, self.app_state.model(), race_id))
            elif path == "/api/model-comparison-backtest":
                self.send_json(dual_model_backtest(conn, self.app_state.model()))
            elif path == "/api/betting":
                race_id = required_query(query, "race_id")
                bankroll = query_float(query, "bankroll", 10000.0)
                risk = query.get("risk", ["standard"])[0] or "standard"
                include_exotics = query_bool(query, "include_exotics", True)
                refresh_odds_live = query_bool(query, "refresh_odds", False)
                refresh_exotics = query_bool(query, "refresh_exotics", False)
                record_mode = query.get("record_mode", ["async"])[0] or "async"
                model = self.app_state.model()
                policy = self.app_state.prediction_policy(conn, model)
                self.send_json(
                    api_betting(
                        conn,
                        model,
                        race_id,
                        bankroll,
                        risk,
                        self.app_state.model_path,
                        state=self.app_state,
                        policy=policy,
                        include_exotics=include_exotics,
                        refresh_odds_live=refresh_odds_live,
                        refresh_exotics=refresh_exotics,
                        record_mode=record_mode,
                    )
                )
            elif path == "/api/exotic-dividends":
                race_id = required_query(query, "race_id")
                self.send_json(exotic_dividend_report(conn, race_id))
            elif path == "/api/betting-ledger":
                race_id = query.get("race_id", [None])[0]
                self.send_json(betting_ledger_report(conn, race_id=race_id))
            elif path == "/api/pool-replay":
                race_id = query.get("race_id", [None])[0]
                self.send_json(pool_replay_report(conn, race_id=race_id))
            else:
                self.send_error(404)

    def handle_api_post(self, path: str, query: dict[str, list[str]]) -> None:
        with connect(self.app_state.settings.db_path) as conn:
            if path == "/api/refresh-odds":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status not in {"scheduled", "live"}:
                    self.send_json(
                        {
                            "race_id": race_id,
                            "inserted": 0,
                            "status": "frozen",
                            "message": "race_resulted_last_live_odds_preserved",
                        }
                    )
                    return
                try:
                    inserted = refresh_odds(conn, race_id, build_odds_provider(self.app_state.settings))
                    self.send_json({"race_id": race_id, "inserted": inserted, "status": "ok"})
                except Exception as exc:
                    upsert_race_status(
                        conn,
                        race_id,
                        "scheduled",
                        last_odds_refresh_at=datetime.now(timezone.utc).isoformat(),
                        notes=f"odds_provider={self.app_state.settings.odds_provider}; error={str(exc)[:180]}",
                    )
                    conn.commit()
                    self.send_json({"race_id": race_id, "inserted": 0, "status": "error", "error": str(exc)})
            elif path == "/api/refresh-exotic-dividends":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status not in {"scheduled", "live"}:
                    self.send_json(
                        {
                            "race_id": race_id,
                            "inserted": 0,
                            "status": "frozen",
                            "message": "race_resulted_last_live_dividends_preserved",
                        }
                    )
                    return
                try:
                    self.send_json(
                        refresh_exotic_dividends(
                            conn,
                            race_id,
                            build_exotic_dividend_provider(self.app_state.settings),
                        )
                    )
                except Exception as exc:
                    self.send_json({"race_id": race_id, "inserted": 0, "status": "error", "error": str(exc)})
            elif path == "/api/watch-race":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status == "missing":
                    self.send_json({"race_id": race_id, "status": "missing", "active_race_id": None})
                    return
                if status == "resulted":
                    payload = self.app_state.sleep_race()
                    payload.update(
                        {
                            "race_id": race_id,
                            "status": status,
                            "mode": "active_race_only",
                            "persistent": False,
                            "message": "race_resulted",
                        }
                    )
                    self.send_json(payload)
                    return
                payload = self.app_state.focus_race(race_id)
                payload.update({"race_id": race_id, "status": status, "mode": "active_race_only"})
                self.send_json(payload)
            elif path == "/api/sleep-race":
                race_id = query.get("race_id", [None])[0]
                self.send_json(self.app_state.sleep_race(race_id))
            elif path == "/api/mark-resulted":
                race_id = required_query(query, "race_id")
                result = refresh_hkjc_results_if_available(
                    conn,
                    race_id,
                    self.app_state.model(),
                    self.app_state.settings.user_agent,
                    self.app_state.settings.request_delay_seconds,
                )
                if result and result.get("status") == "resulted":
                    result["final_place_backfill"] = backfill_final_place_odds(
                        conn,
                        race_id,
                        build_official_odds_provider(self.app_state.settings),
                    )
                    self.send_json(result)
                else:
                    self.send_json(result or {"race_id": race_id, "status": "not_hkjc_race"})
            elif path == "/api/mark-live":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status == "resulted":
                    self.send_json({"race_id": race_id, "status": "resulted", "message": "already_resulted"})
                    return
                upsert_race_status(
                    conn,
                    race_id,
                    "live",
                    last_odds_refresh_at=datetime.now(timezone.utc).isoformat(),
                    notes="frozen_after_start",
                )
                conn.commit()
                self.send_json({"race_id": race_id, "status": "live"})
            elif path == "/api/mark-scheduled":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status == "resulted":
                    self.send_json({"race_id": race_id, "status": "resulted", "message": "already_resulted"})
                    return
                upsert_race_status(conn, race_id, "scheduled", notes="refresh_enabled")
                conn.commit()
                self.send_json({"race_id": race_id, "status": "scheduled"})
            elif path == "/api/refresh-results":
                race_id = required_query(query, "race_id")
                result = refresh_hkjc_results_if_available(
                    conn,
                    race_id,
                    self.app_state.model(),
                    self.app_state.settings.user_agent,
                    self.app_state.settings.request_delay_seconds,
                )
                if result and result.get("status") == "resulted":
                    result["final_place_backfill"] = backfill_final_place_odds(
                        conn,
                        race_id,
                        build_official_odds_provider(self.app_state.settings),
                    )
                self.send_json(result or {"race_id": race_id, "status": "not_hkjc_race"})
            elif path == "/api/backfill-final-place-odds":
                race_id = required_query(query, "race_id")
                self.send_json(
                    backfill_final_place_odds(
                        conn,
                        race_id,
                        build_official_odds_provider(self.app_state.settings),
                    )
                )
            elif path == "/api/lifecycle-step":
                self.send_json(run_lifecycle_step(conn, self.app_state, scope="global"))
            elif path == "/api/load-race-day":
                race_date = normalize_hkjc_date(required_query(query, "date"))
                venue = required_query(query, "venue")
                race_count = int(query.get("races", ["10"])[0])
                self.send_json(self.app_state.start_race_day_job(race_date, venue, race_count))
            elif path == "/api/backfill":
                start_date = normalize_hkjc_date(required_query(query, "start"))
                end_date = normalize_hkjc_date(required_query(query, "end"))
                venue = required_query(query, "venue")
                race_count = int(query.get("races", ["10"])[0])
                train_epochs = int(query.get("epochs", ["120"])[0])
                self.send_json(self.app_state.start_backfill_job(start_date, end_date, venue, race_count, train_epochs))
            elif path == "/api/repair-data":
                repair = repair_orphan_result_runners(conn)
                alignment = align_resulted_race_data(conn)
                quality = data_quality_report(conn)
                self.send_json(
                    {
                        "status": "done",
                        "repair": repair,
                        "alignment": alignment,
                        "training": {"trained": False, "reason": "repair_data_no_inline_training"},
                        "quality": quality,
                        "model_versions": None,
                    }
                )
            elif path == "/api/complete-runners":
                self.send_json(self.app_state.start_runner_completion_job())
            elif path == "/api/gpt-iteration":
                report = evaluate_model_evolution(conn, self.app_state.model())
                report["model_versions"] = run_walk_forward_versions(conn)
                try:
                    notes = generate_codex_iteration(
                        self.app_state.settings.codex_cli_command,
                        self.app_state.settings.codex_model,
                        self.app_state.settings.codex_reasoning_effort,
                        report,
                        self.app_state.settings.codex_timeout_seconds,
                    )
                    self.send_json(
                        {
                            "status": "done",
                            "engine": "codex_cli",
                            "model": self.app_state.settings.codex_model,
                            "reasoning_effort": self.app_state.settings.codex_reasoning_effort,
                            "notes": notes,
                            "report": report,
                        }
                    )
                    return
                except Exception as codex_exc:
                    if not self.app_state.settings.openai_api_key:
                        self.send_json(
                            {
                                "status": "error",
                                "engine": "codex_cli",
                                "model": self.app_state.settings.codex_model,
                                "reasoning_effort": self.app_state.settings.codex_reasoning_effort,
                                "message": str(codex_exc),
                                "report": report,
                            }
                        )
                        return
                try:
                    notes = generate_openai_iteration(
                        self.app_state.settings.openai_api_key,
                        self.app_state.settings.openai_model,
                        report,
                    )
                    self.send_json(
                        {
                            "status": "done",
                            "engine": "openai_api",
                            "model": self.app_state.settings.openai_model,
                            "notes": notes,
                            "report": report,
                        }
                    )
                except Exception as exc:
                    self.send_json(
                        {
                            "status": "error",
                            "engine": "openai_api",
                            "model": self.app_state.settings.openai_model,
                            "message": str(exc),
                            "report": report,
                        }
                    )
            elif path == "/api/model-registry/run":
                epochs = int(query.get("epochs", ["80"])[0])
                min_train_races = int(query.get("min_train_races", ["1"])[0])
                min_expected_value = float(query.get("min_ev", ["0.05"])[0])
                stake = float(query.get("stake", ["10"])[0])
                self.send_json(
                    run_and_record_model_registry(
                        conn,
                        self.app_state.model_path,
                        trigger="manual",
                        min_train_races=min_train_races,
                        epochs=epochs,
                        min_expected_value=min_expected_value,
                        stake=stake,
                    )
                )
            elif path == "/api/model-registry/promote":
                epochs = int(query.get("epochs", ["400"])[0])
                result = promote_latest_model(conn, self.app_state.model_path, epochs=epochs)
                result["registry"] = model_registry_report(conn)
                self.send_json(result)
            elif path == "/api/betting-ledger/reconcile":
                race_id = query.get("race_id", [None])[0]
                result = reconcile_betting_ledger(conn, race_id=race_id)
                result["ledger"] = betting_ledger_report(conn, race_id=race_id)
                self.send_json(result)
            elif path == "/api/betting-ledger/confirm":
                body = self.read_json_body()
                body_recommendation_id = body.get("recommendation_id") if isinstance(body, dict) else ""
                recommendation_id = str(body_recommendation_id or query.get("recommendation_id", [""])[0])
                result = confirm_betting_recommendation(
                    conn,
                    recommendation_id,
                    execution_odds=query_float_from_body(body, query, "execution_odds", None),
                    execution_stake=query_float_from_body(body, query, "execution_stake", None),
                    source=str(body.get("source") or "manual_confirm") if isinstance(body, dict) else "manual_confirm",
                )
                race_id = query.get("race_id", [None])[0] or (result.get("item") or {}).get("race_id")
                result["ledger"] = betting_ledger_report(conn, race_id=race_id)
                self.send_json(result)
            elif path == "/api/pool-replay/reconcile":
                race_id = query.get("race_id", [None])[0]
                self.send_json(reconcile_pool_replay_with_final_dividends(conn, self.app_state, race_id=race_id))
            elif path == "/api/exotic-dividends":
                race_id = required_query(query, "race_id")
                body = self.read_json_body()
                rows = body.get("items", []) if isinstance(body, dict) else []
                source = str(body.get("source", "manual")) if isinstance(body, dict) else "manual"
                status = str(body.get("dividend_status", "probable")) if isinstance(body, dict) else "probable"
                self.send_json(upsert_exotic_dividends(conn, race_id, rows, source=source, dividend_status=status))
            else:
                self.send_error(404)

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            target = APP_DIR / "index.html"
        else:
            target = (APP_DIR / path.lstrip("/")).resolve()
            if APP_DIR not in target.parents and target != APP_DIR:
                self.send_error(403)
                return
        if not target.exists() or not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "text/javascript"}:
            content_type = f"{content_type}; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        encoding = "gzip" if accepts_gzip and len(body) >= 1024 else None
        if encoding:
            body = gzip.compress(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def api_state(conn, state: AppState) -> dict[str, object]:
    return {
        "now": datetime.now(timezone.utc).isoformat(),
        "db_path": display_database_target(state.settings.db_path),
        "model_path": str(state.model_path),
        "odds_interval_seconds": state.odds_interval_seconds,
        "odds_provider": state.settings.odds_provider,
        "current_race_id": state.active_race() or current_race_id(conn),
        "active_race_id": state.active_race(),
        "active_race_expires_in_seconds": state.active_race_expires_in(),
    }


def fast_prediction_policy(conn) -> dict[str, object]:
    rows = fetch_all(
        conn,
        """
        SELECT count(DISTINCT race_id) AS n
        FROM results
        WHERE finish_position = 1
        """,
    )
    races = int(rows[0]["n"] or 0) if rows else 0
    if races >= 5:
        return {
            "mode": "market_blend",
            "market_blend_weight": 0.35,
            "reason": "快速載入策略：先用市場融合加權，正式雙軌回測在背景更新。",
            "races": races,
            "ability_win_rate": None,
            "market_win_rate": None,
            "verdict": "fast_market_default",
        }
    return {
        "mode": "baseline",
        "market_blend_weight": 0.0,
        "reason": "快速載入策略：樣本不足，先用基礎模型，正式雙軌回測在背景更新。",
        "races": races,
        "ability_win_rate": None,
        "market_win_rate": None,
        "verdict": "fast_baseline_default",
    }


def api_lifecycle(conn, state: AppState) -> dict[str, object]:
    refresh_race_statuses(conn)
    rows = fetch_all(
        conn,
        """
        SELECT COALESCE(s.status, 'scheduled') AS status, count(*) AS n
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        GROUP BY COALESCE(s.status, 'scheduled')
        """,
    )
    counts = {row["status"]: row["n"] for row in rows}
    active = state.active_race()
    current = active_refreshable_race_id(conn, state)
    frozen_rows = fetch_all(
        conn,
        """
        SELECT r.race_id
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE COALESCE(s.status, 'scheduled') = 'live'
        ORDER BY r.date, r.race_id
        LIMIT 1
        """,
    )
    latest_resulted = fetch_all(
        conn,
        """
        SELECT r.race_id
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE COALESCE(s.status, 'scheduled') = 'resulted'
        ORDER BY r.date DESC, r.race_id DESC
        LIMIT 1
        """,
    )
    return {
        "odds_provider": state.settings.odds_provider,
        "odds_interval_seconds": state.odds_interval_seconds,
        "refreshable_race_id": current,
        "active_race_id": active,
        "active_race_expires_in_seconds": state.active_race_expires_in(),
        "next_scheduled_race_id": current_refreshable_race_id(conn),
        "frozen_race_id": frozen_rows[0]["race_id"] if frozen_rows else None,
        "latest_resulted_race_id": latest_resulted[0]["race_id"] if latest_resulted else None,
        "counts": {
            "scheduled": counts.get("scheduled", 0),
            "live": counts.get("live", 0),
            "resulted": counts.get("resulted", 0),
        },
        "mode": "active_race_only",
    }


def api_races(conn) -> list[dict[str, object]]:
    rows = fetch_all(
        conn,
        """
        SELECT r.*,
               COALESCE(s.status, 'scheduled') AS status,
               s.last_odds_refresh_at,
               s.last_result_refresh_at,
               s.last_backtest_at,
               s.notes,
               (SELECT count(*) FROM runners ru WHERE ru.race_id = r.race_id) AS runners,
               (SELECT count(*) FROM results x WHERE x.race_id = r.race_id) AS results,
               (
                 SELECT count(*)
                 FROM odds_ticks o
                 WHERE o.race_id = r.race_id
                   AND o.source IN (
                     'hkjc_graphql',
                     'hkjc_mqtt',
                     'hkjc_results_final',
                     'hkjc_final_place_snapshot',
                     'hkjc_final_place_backfill'
                   )
               ) AS odds_ticks,
               (SELECT count(*) FROM exotic_dividends ed WHERE ed.race_id = r.race_id) AS exotic_dividends
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        ORDER BY r.date, r.race_id
        """,
    )
    return [dict(row) for row in rows]


def api_race_dashboard(conn, state: AppState, race_id: str, bankroll: float, risk: str) -> dict[str, object]:
    model = state.model()
    policy = state.prediction_policy(conn, model)
    prediction_payload = api_predictions(conn, model, race_id, policy)
    state.cache_race_predictions(
        race_id,
        prediction_payload.get("predictions", []),
        prediction_payload.get("policy", {}),
    )
    return {
        "lifecycle": api_lifecycle(conn, state),
        "races": api_races(conn),
        "predictions": prediction_payload,
        "betting": api_fast_betting_preview(
            conn,
            race_id,
            bankroll,
            risk,
            prediction_payload.get("predictions", []),
        ),
        "betting_ledger": {"deferred": True, "summary": {}, "items": []},
        "odds_feed": {"deferred": True},
        "market_flow": {"deferred": True},
        "model_comparison": {"deferred": True},
        "odds_history": [],
        "results": {"deferred": True, "results": []},
        "weather": {"deferred": True},
    }


def api_fast_betting_preview(
    conn,
    race_id: str,
    bankroll: float,
    risk: str,
    predictions: list[dict[str, object]],
) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "tickets": [], "decisions": [], "fast_preview": True, "exotics_deferred": True}
    payload = build_betting_decisions(
        predictions,
        race_lifecycle_status(conn, race_id),
        bankroll=bankroll,
        risk_profile=risk,
        exotic_dividends={},
        include_exotics=False,
        calibration_gate=None,
    )
    payload["race"] = dict(race_rows[0])
    payload["fast_preview"] = True
    payload["settlement"] = {"summary": {}, "items": []}
    return payload


def api_analytics_dashboard(conn, state: AppState, include_coverage: bool = False, fast: bool = True) -> dict[str, object]:
    if fast:
        payload: dict[str, object] = {
            "backtest": {"deferred": True},
            "evolution": {
                "metrics": {},
                "ideas": [],
                "data_quality": ["模型診斷正在背景計算，完成後會自動更新。"],
            },
            "dual_track": {"summary": {}, "recommendation": {"message": "雙軌回測正在背景計算。"}, "recent_races": []},
            "taxonomy": {"summary": {"total": 0}, "items": [], "deferred": True},
            "model_versions": {
                "summary": {"recommendation": "Walk-forward 模型版本比較正在背景計算，完成後會自動更新。"},
                "versions": [],
                "deferred": True,
            },
            "model_registry": {"summary": {}, "runs": [], "deferred": True},
            "calibration_gate": {"status": "deferred", "label": "Deferred", "stake_factor": 1.0},
            "pool_replay": {"summary": {}, "markets": [], "insights": [], "deferred": True},
            "promotion_scorecard": {"summary": {}, "sections": [], "slice_scorecard": [], "deferred": True},
            "data_quality": data_quality_report(conn),
            "fast": True,
        }
        if include_coverage:
            payload["coverage"] = build_coverage_report(conn)
        return payload

    model = state.model()
    model_versions = run_walk_forward_versions(conn, epochs=20, max_folds=6, max_train_races=24)
    model_registry = model_registry_report(conn)
    pool_replay = pool_replay_report(conn)
    payload = {
        "backtest": asdict(run_backtest(conn, model, max_races=80)),
        "evolution": evaluate_model_evolution(conn, model, max_races=40),
        "dual_track": dual_model_backtest(conn, model),
        "taxonomy": error_taxonomy_report(conn, model),
        "model_versions": model_versions,
        "model_registry": model_registry,
        "calibration_gate": state.calibration_gate(conn, model),
        "pool_replay": pool_replay,
        "promotion_scorecard": build_promotion_scorecard(model_versions, pool_replay, model_registry),
        "data_quality": data_quality_report(conn),
    }
    if include_coverage:
        payload["coverage"] = build_coverage_report(conn)
    return payload


def reconcile_pool_replay_with_final_dividends(
    conn,
    state: AppState,
    race_id: str | None = None,
) -> dict[str, object]:
    before = pool_replay_report(conn, race_id=race_id)
    targets = final_dividend_refresh_targets(before)
    refreshes = []
    for target_race_id in targets:
        result = refresh_hkjc_results_if_available(
            conn,
            target_race_id,
            state.model(),
            state.settings.user_agent,
            state.settings.request_delay_seconds,
        )
        entry: dict[str, object] = {"race_id": target_race_id, "result": result}
        if result and result.get("status") == "resulted":
            entry["final_place_backfill"] = backfill_final_place_odds(
                conn,
                target_race_id,
                build_official_odds_provider(state.settings),
            )
        refreshes.append(entry)
    reconciliation = reconcile_betting_ledger(conn, race_id=race_id)
    after = pool_replay_report(conn, race_id=race_id)
    before_summary = (before.get("final_dividend_audit") or {}).get("summary", {})
    after_summary = (after.get("final_dividend_audit") or {}).get("summary", {})
    return {
        "status": "done",
        "race_id": race_id,
        "refresh_targets": targets,
        "refreshes": refreshes,
        "reconciliation": reconciliation,
        "updated": reconciliation.get("updated", 0),
        "before_final_dividend_audit": before_summary,
        "after_final_dividend_audit": after_summary,
        "pool_replay": after,
    }


def final_dividend_refresh_targets(pool_replay: dict[str, object]) -> list[str]:
    audit = pool_replay.get("final_dividend_audit") if isinstance(pool_replay, dict) else {}
    items = (audit or {}).get("items", []) if isinstance(audit, dict) else []
    targets = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        result_status = str(item.get("result_status") or "")
        final_dividend = item.get("final_dividend")
        if result_status not in {"no_results", "hit"}:
            continue
        if result_status == "hit" and final_dividend not in {None, ""}:
            continue
        item_race_id = str(item.get("race_id") or "")
        if item_race_id and item_race_id not in seen:
            seen.add(item_race_id)
            targets.append(item_race_id)
    return targets


def api_predictions(
    conn,
    model: RankingModel,
    race_id: str,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "predictions": []}
    adaptive = adaptive_predict_race(conn, model, race_id, policy)
    pace_map = annotate_predictions_with_pace(adaptive["predictions"])
    return {
        "race": dict(race_rows[0]),
        "predictions": adaptive["predictions"],
        "policy": adaptive["policy"],
        "pace_map": pace_map,
    }


def api_betting(
    conn,
    model: RankingModel,
    race_id: str,
    bankroll: float,
    risk: str,
    model_path: Path | str | None = None,
    state: AppState | None = None,
    predictions: list[dict[str, object]] | None = None,
    policy: dict[str, object] | None = None,
    include_exotics: bool = True,
    refresh_odds_live: bool = False,
    refresh_exotics: bool = False,
    record_mode: str = "sync",
) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "tickets": [], "decisions": []}
    status = race_lifecycle_status(conn, race_id)
    odds_refresh_status = "not_requested"
    odds_refresh_error = ""
    odds_refresh_inserted = 0
    if refresh_odds_live and status in {"scheduled", "live"} and state is not None:
        try:
            odds_refresh_inserted = refresh_odds(conn, race_id, build_odds_provider(state.settings))
            odds_refresh_status = "refreshed"
        except Exception as exc:
            odds_refresh_status = "error"
            odds_refresh_error = str(exc)
    if predictions is None:
        cached = None if refresh_odds_live else state.cached_race_predictions(race_id, state.odds_interval_seconds + 5) if state is not None else None
        if cached:
            predictions = cached["predictions"]
            policy = cached.get("policy") or policy
        else:
            adaptive = adaptive_predict_race(conn, model, race_id, policy)
            predictions = adaptive["predictions"]
            policy = adaptive.get("policy", policy or {})
            if state is not None:
                state.cache_race_predictions(race_id, predictions, policy or {})
    pace_map = annotate_predictions_with_pace(predictions)
    status = race_lifecycle_status(conn, race_id)
    exotic_refresh_status = "not_requested"
    exotic_refresh_error = ""
    if include_exotics and refresh_exotics and status in {"scheduled", "live"} and state is not None:
        try:
            refresh_exotic_dividends(conn, race_id, build_exotic_dividend_provider(state.settings))
            exotic_refresh_status = "refreshed"
        except Exception as exc:
            exotic_refresh_status = "error"
            exotic_refresh_error = str(exc)
    exotic_lookup = load_exotic_dividend_lookup(conn, race_id)
    if include_exotics and status in {"scheduled", "live"} and not exotic_lookup:
        queued = state.start_exotic_refresh_job(race_id) if state is not None else False
    else:
        queued = False
    race = dict(race_rows[0])
    payload = build_betting_decisions(
        predictions,
        status,
        bankroll=bankroll,
        risk_profile=risk,
        exotic_dividends=exotic_lookup,
        include_exotics=include_exotics,
        calibration_gate=None if status == "resulted" or state is None else state.calibration_gate(conn, model),
        pool_replay_gate=None if status == "resulted" or state is None else state.pool_replay_gate(conn, race),
    )
    payload["race"] = race
    payload["prediction_policy"] = policy or {}
    payload["pace_map"] = pace_map
    payload["exotic_refresh"] = {
        "status": exotic_refresh_status if exotic_refresh_status != "not_requested" else "queued" if queued else "cached" if exotic_lookup else "not_available",
        "cached_dividends": len(exotic_lookup),
        "error": exotic_refresh_error,
    }
    payload["odds_refresh"] = {
        "status": odds_refresh_status,
        "inserted": odds_refresh_inserted,
        "error": odds_refresh_error,
    }
    if model_path is not None:
        if record_mode == "async" and state is not None:
            annotate_betting_recommendation_ids(race, payload, model_path)
            queued_record = state.start_betting_record_job(race, payload, model_path)
            payload["ledger"] = {"record_mode": "async", "queued": queued_record}
        elif record_mode != "none":
            payload["ledger"] = record_betting_payload(conn, race, payload, model_path)
    if status == "resulted":
        reconcile_betting_ledger(conn, race_id=race_id)
        price_refresh = {"checked": 0, "updated": 0}
    else:
        price_refresh = refresh_open_betting_prices(conn, race_id=race_id)
    ledger_report = betting_ledger_report(conn, race_id=race_id)
    payload["ledger_price_refresh"] = price_refresh
    payload["placed_summary"] = placed_betting_summary(ledger_report)
    payload["settlement"] = betting_settlement_payload(ledger_report)
    settlement_summary = payload["settlement"].get("summary", {}) if isinstance(payload["settlement"], dict) else {}
    ledger_ticket_count = int((settlement_summary or {}).get("tickets") or 0) if isinstance(settlement_summary, dict) else 0
    ledger_staked = float((settlement_summary or {}).get("staked") or 0.0) if isinstance(settlement_summary, dict) else 0.0
    max_race_stake = float(payload.get("max_race_stake") or 0.0)
    current_ticket_stake = float((payload.get("bet_slip") or {}).get("summary", {}).get("total_stake") or payload.get("total_recommended_stake") or 0.0)
    display_total_stake = round(
        ledger_staked if ledger_ticket_count > 0 else current_ticket_stake,
        2,
    )
    payload["display_total_recommended_stake"] = display_total_stake
    payload["display_max_race_stake"] = round(max(max_race_stake, display_total_stake), 2)
    return payload


def placed_betting_summary(ledger: dict[str, object]) -> dict[str, object]:
    summary = ledger.get("summary", {}) if isinstance(ledger, dict) else {}
    executed = float((summary or {}).get("executed_staked") or 0.0) if isinstance(summary, dict) else 0.0
    confirmed = int((summary or {}).get("confirmed") or 0) if isinstance(summary, dict) else 0
    reconciled = int((summary or {}).get("reconciled") or 0) if isinstance(summary, dict) else 0
    return {
        "confirmed": confirmed,
        "reconciled": reconciled,
        "executed_staked": round(executed, 2),
        "display_stake": round(executed, 2),
        "source": "betting_ledger_confirmed",
        "note": "即場建議總注以派彩對數內已保存飛數加總；未保存前則顯示今次通過風險上限的下注單總額。",
    }


def betting_settlement_payload(ledger: dict[str, object]) -> dict[str, object]:
    items = list(ledger.get("items", [])) if isinstance(ledger, dict) else []
    executed_items = [row for row in items if isinstance(row, dict) and row.get("execution_status") == "confirmed"]
    unique_items = latest_logical_settlement_items(executed_items)
    settled = [row for row in unique_items if row.get("reconciliation_status") == "reconciled"]
    hits = [row for row in settled if int(row.get("outcome_win") or 0) == 1]
    misses = [row for row in settled if int(row.get("outcome_win") or 0) == 0]
    pending = [row for row in unique_items if row.get("reconciliation_status") != "reconciled"]
    return {
        "summary": {
            "tickets": len(unique_items),
            "raw_tickets": len(executed_items),
            "ignored_unexecuted": max(len(items) - len(executed_items), 0),
            "settled": len(settled),
            "hit": len(hits),
            "miss": len(misses),
            "pending": len(pending),
            "profit": round(sum(float(row.get("profit") or 0) for row in settled), 2),
            "returned": round(sum(float(row.get("returned") or 0) for row in settled), 2),
            "staked": round(sum(float(row.get("execution_stake") or row.get("recommended_stake") or 0) for row in unique_items), 2),
        },
        "items": unique_items,
        "note": "已完場會用投注留痕對照賽果及最終派彩；組合贏票未有 final dividend 時會保持待派彩。",
    }


def annotate_betting_recommendation_ids(
    race: dict[str, object],
    payload: dict[str, object],
    model_path: Path | str,
) -> None:
    tickets = [ticket for ticket in payload.get("tickets", []) if isinstance(ticket, dict) and float(ticket.get("recommended_stake") or 0) > 0]
    if not tickets:
        return
    annotate_pool_choice_context(tickets, payload)
    now = datetime.now(timezone.utc).isoformat()
    for ticket in tickets:
        ticket["recommendation_id"] = recommendation_key(ticket, race, payload, model_path)
        execution = auto_execution_payload(ticket, now)
        for key, value in execution.items():
            ticket.setdefault(key, value)


def latest_logical_settlement_items(items: list[object]) -> list[dict[str, object]]:
    latest: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("race_id") or ""),
            str(item.get("market") or ""),
            str(item.get("horse_id") or ""),
        )
        current = latest.get(key)
        if current is None or str(item.get("created_at") or "") >= str(current.get("created_at") or ""):
            latest[key] = item
    return sorted(latest.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)


def api_results(
    conn,
    model: RankingModel,
    race_id: str,
    policy: dict[str, object] | None = None,
    predictions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "results": []}
    result_count = fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"]
    if int(result_count or 0) > 0:
        snapshot_count = freeze_final_place_snapshots(conn, race_id, datetime.now(timezone.utc).isoformat())
        if snapshot_count:
            conn.commit()
    if predictions is None:
        predictions = adaptive_predict_race(conn, model, race_id, policy)["predictions"]
    prediction_by_horse = {
        str(row["horse_id"]): {
            "prediction_rank": index + 1,
            "win_probability": row["win_probability"],
            "top3_probability": row["top3_probability"],
            "expected_value": row["expected_value"],
            "top3_expected_value": row.get("top3_expected_value"),
            "place_odds": row.get("place_odds"),
            "place_odds_source": row.get("place_odds_source"),
        }
        for index, row in enumerate(predictions)
    }
    latest_odds = latest_odds_by_race(conn, race_id)
    rows = fetch_all(
        conn,
        """
        WITH final_odds AS (
          SELECT o.*
          FROM odds_ticks o
          JOIN (
            SELECT race_id, horse_id, max(timestamp) AS max_ts
            FROM odds_ticks
            WHERE race_id = ? AND source = 'hkjc_results_final'
            GROUP BY race_id, horse_id
          ) latest
            ON o.race_id = latest.race_id
           AND o.horse_id = latest.horse_id
           AND o.timestamp = latest.max_ts
        )
        SELECT
          re.race_id,
          re.horse_id,
          re.finish_position,
          re.finish_time_sec,
          re.margin_lengths,
          re.running_positions,
          re.comment,
          ru.horse_no,
          ru.horse_name,
          ru.horse_name_zh,
          COALESCE(NULLIF(ru.horse_name_zh, ''), ru.horse_name, re.horse_id) AS display_name,
          ru.jockey,
          ru.jockey_zh,
          COALESCE(NULLIF(ru.jockey_zh, ''), ru.jockey) AS display_jockey,
          ru.trainer,
          ru.trainer_zh,
          COALESCE(NULLIF(ru.trainer_zh, ''), ru.trainer) AS display_trainer,
          ru.draw,
          ru.body_weight_lbs,
          fo.win_odds,
          fo.place_odds AS final_place_odds
        FROM results re
        LEFT JOIN runners ru ON ru.race_id = re.race_id AND ru.horse_id = re.horse_id
        LEFT JOIN final_odds fo ON fo.race_id = re.race_id AND fo.horse_id = re.horse_id
        WHERE re.race_id = ?
        ORDER BY re.finish_position
        """,
        (race_id, race_id),
    )
    results = []
    for row in rows:
        item = dict(row)
        item.update(prediction_by_horse.get(str(item["horse_id"]), {}))
        latest = latest_odds.get(str(item["horse_id"]))
        if latest:
            item["final_place_odds"] = item.get("final_place_odds") or latest.get("place_odds")
            item["final_place_odds_source"] = latest.get("place_source")
            if item.get("top3_expected_value") is None and latest.get("place_odds") is not None:
                top3_probability = item.get("top3_probability")
                if top3_probability is not None:
                    item["top3_expected_value"] = float(top3_probability) * float(latest["place_odds"]) - 1.0
        results.append(item)
    return {
        "race": dict(race_rows[0]),
        "results": results,
        "place_odds_completeness": final_place_odds_completeness(conn, race_id),
    }


def run_lifecycle_step(conn, state: AppState, scope: str = "active") -> dict[str, object]:
    refresh_race_statuses(conn)
    race_id = current_refreshable_race_id(conn) if scope == "global" else active_refreshable_race_id(conn, state)
    if not race_id:
        active = state.active_race()
        message = "no_scheduled_race" if scope == "global" else "no_active_refreshable_race" if active else "no_active_race"
        return {
            "status": "idle",
            "message": message,
            "scope": scope,
            "active_race_id": active,
            "next_race_id": current_refreshable_race_id(conn),
        }

    odds = {"inserted": 0, "status": "skipped"}
    exotic = {"inserted": 0, "status": "skipped"}
    try:
        inserted = refresh_odds(conn, race_id, build_odds_provider(state.settings))
        odds = {"inserted": inserted, "status": "ok"}
    except Exception as exc:
        upsert_race_status(
            conn,
            race_id,
            "scheduled",
            last_odds_refresh_at=datetime.now(timezone.utc).isoformat(),
            notes=f"odds_provider={state.settings.odds_provider}; error={str(exc)[:180]}",
        )
        conn.commit()
        odds = {"inserted": 0, "status": "error", "error": str(exc)}

    try:
        exotic = refresh_exotic_dividends(conn, race_id, build_exotic_dividend_provider(state.settings))
    except Exception as exc:
        exotic = {"inserted": 0, "status": "error", "error": str(exc)}

    result = refresh_hkjc_results_if_available(
        conn,
        race_id,
        state.model(),
        state.settings.user_agent,
        state.settings.request_delay_seconds,
    )
    if result and result.get("status") == "resulted":
        result["final_place_backfill"] = backfill_final_place_odds(
            conn,
            race_id,
            build_official_odds_provider(state.settings),
        )
        result["betting_reconciliation"] = reconcile_betting_ledger(conn, race_id=race_id)
        result["pool_replay"] = pool_replay_report(conn, race_id=race_id)
        state.sleep_race(race_id)
    next_race_id = current_refreshable_race_id(conn)
    return {
        "status": "done",
        "scope": scope,
        "race_id": race_id,
        "odds": odds,
        "exotic_dividends": exotic,
        "result": result,
        "next_race_id": next_race_id,
    }


def current_race_id(conn) -> str | None:
    race_id = current_refreshable_race_id(conn)
    if race_id:
        return race_id
    rows = fetch_all(conn, "SELECT race_id FROM races ORDER BY date DESC, race_id DESC LIMIT 1")
    return rows[0]["race_id"] if rows else None


def current_refreshable_race_id(conn) -> str | None:
    today = datetime.now(hong_kong_tz()).date().isoformat()
    rows = fetch_all(
        conn,
        """
        SELECT r.race_id
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE COALESCE(s.status, 'scheduled') = 'scheduled'
          AND r.date >= ?
        ORDER BY r.date, r.race_id
        LIMIT 1
        """,
        (today,),
    )
    if rows:
        return rows[0]["race_id"]
    return None


def active_refreshable_race_id(conn, state: AppState) -> str | None:
    race_id = state.active_race()
    if not race_id:
        return None
    status = race_lifecycle_status(conn, race_id)
    if status not in {"scheduled", "live"}:
        return None
    if status == "live":
        return race_id
    rows = fetch_all(conn, "SELECT date FROM races WHERE race_id = ?", (race_id,))
    today = datetime.now(hong_kong_tz()).date().isoformat()
    if rows and str(rows[0]["date"] or "") < today:
        return None
    return race_id


def race_lifecycle_status(conn, race_id: str) -> str:
    rows = fetch_all(
        conn,
        """
        SELECT COALESCE(s.status, 'scheduled') AS status
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE r.race_id = ?
        """,
        (race_id,),
    )
    return rows[0]["status"] if rows else "missing"


def required_query(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key)
    if not values or not values[0]:
        raise ValueError(f"Missing query parameter: {key}")
    return values[0]


def query_float(query: dict[str, list[str]], key: str, default: float) -> float:
    values = query.get(key)
    if not values or not values[0]:
        return default
    try:
        return float(values[0])
    except ValueError:
        return default


def query_float_from_body(
    body: object,
    query: dict[str, list[str]],
    key: str,
    default: float | None,
) -> float | None:
    value = body.get(key) if isinstance(body, dict) else None
    if value in {None, ""}:
        values = query.get(key)
        value = values[0] if values else None
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def query_bool(query: dict[str, list[str]], key: str, default: bool) -> bool:
    values = query.get(key)
    if not values or not values[0]:
        return default
    return values[0].strip().lower() in {"1", "true", "yes", "y", "on"}
