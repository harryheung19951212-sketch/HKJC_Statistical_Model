from __future__ import annotations

import json
import mimetypes
import uuid
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .backtest import run_backtest
from .backfill import (
    complete_repaired_runners,
    data_quality_report,
    load_hkjc_date_range,
    normalize_hkjc_date,
    repair_orphan_result_runners,
    train_model_if_requested,
)
from .betting import build_betting_decisions
from .betting_ledger import betting_ledger_report, reconcile_betting_ledger, record_betting_payload
from .config import display_database_target, get_settings
from .coverage import build_coverage_report
from .error_taxonomy import error_taxonomy_report
from .evolution import evaluate_model_evolution, generate_codex_iteration, generate_openai_iteration
from .exotic_dividends import exotic_dividend_report, load_exotic_dividend_lookup, upsert_exotic_dividends
from .exotic_live import build_exotic_dividend_provider, refresh_exotic_dividends
from .features import build_race_features
from .live import load_hkjc_race_day, refresh_hkjc_results_if_available
from .model import RankingModel
from .model_registry import model_registry_report, run_and_record_model_registry
from .odds import build_odds_provider, odds_history, refresh_odds
from .storage import connect, fetch_all, init_db, refresh_race_statuses, upsert_race_status
from .walk_forward import run_walk_forward_versions
from .weather import race_weather


APP_DIR = Path(__file__).resolve().parent / "web"


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
        self.active_race_ttl_seconds = max(90, odds_interval_seconds * 3)
        self.active_race_lock = threading.Lock()

    def model(self) -> RankingModel:
        if self.model_path.exists():
            return RankingModel.load(self.model_path)
        return RankingModel.new()

    def focus_race(self, race_id: str, now: float | None = None) -> dict[str, object]:
        timestamp = time.monotonic() if now is None else now
        with self.active_race_lock:
            self.active_race_id = race_id
            self.active_race_seen_at = timestamp
        return {
            "active_race_id": race_id,
            "expires_in_seconds": self.active_race_ttl_seconds,
        }

    def sleep_race(self, race_id: str | None = None) -> dict[str, object]:
        with self.active_race_lock:
            if race_id is None or race_id == self.active_race_id:
                self.active_race_id = None
                self.active_race_seen_at = 0.0
        return {"active_race_id": self.active_race_id}

    def active_race(self, now: float | None = None) -> str | None:
        timestamp = time.monotonic() if now is None else now
        with self.active_race_lock:
            if not self.active_race_id:
                return None
            if timestamp - self.active_race_seen_at > self.active_race_ttl_seconds:
                self.active_race_id = None
                self.active_race_seen_at = 0.0
                return None
            return self.active_race_id

    def active_race_expires_in(self, now: float | None = None) -> int:
        timestamp = time.monotonic() if now is None else now
        with self.active_race_lock:
            if not self.active_race_id:
                return 0
            remaining = self.active_race_ttl_seconds - (timestamp - self.active_race_seen_at)
            return max(0, int(remaining))

    def start_refresh_loop(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.refresh_loop, daemon=True)
        self.thread.start()

    def refresh_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with connect(self.settings.db_path) as conn:
                    run_lifecycle_step(conn, self)
            except Exception:
                pass
            self.stop_event.wait(self.odds_interval_seconds)

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
                        self.settings.request_delay_seconds,
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
                        self.settings.request_delay_seconds,
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

    def get_job(self, job_id: str) -> dict[str, object] | None:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None


def run_server(host: str, port: int, model_path: Path, odds_interval_seconds: int) -> ThreadingHTTPServer:
    init_db(get_settings().db_path)
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
                result = run_backtest(conn, self.app_state.model())
                self.send_json(asdict(result))
            elif path == "/api/evolution":
                self.send_json(evaluate_model_evolution(conn, self.app_state.model()))
            elif path == "/api/error-taxonomy":
                refresh = query_bool(query, "refresh", False)
                self.send_json(error_taxonomy_report(conn, self.app_state.model(), refresh=refresh))
            elif path == "/api/model-versions":
                self.send_json(run_walk_forward_versions(conn))
            elif path == "/api/model-registry":
                self.send_json(model_registry_report(conn))
            elif path == "/api/lifecycle":
                self.send_json(api_lifecycle(conn, self.app_state))
            elif path == "/api/data-quality":
                self.send_json(data_quality_report(conn))
            elif path == "/api/coverage":
                self.send_json(build_coverage_report(conn))
            elif path == "/api/odds-history":
                race_id = required_query(query, "race_id")
                self.send_json(odds_history(conn, race_id))
            elif path == "/api/weather":
                race_id = required_query(query, "race_id")
                self.send_json(race_weather(conn, race_id))
            elif path == "/api/results":
                race_id = required_query(query, "race_id")
                self.send_json(api_results(conn, self.app_state.model(), race_id))
            elif path == "/api/predictions":
                race_id = required_query(query, "race_id")
                self.send_json(api_predictions(conn, self.app_state.model(), race_id))
            elif path == "/api/betting":
                race_id = required_query(query, "race_id")
                bankroll = query_float(query, "bankroll", 10000.0)
                risk = query.get("risk", ["standard"])[0] or "standard"
                self.send_json(api_betting(conn, self.app_state.model(), race_id, bankroll, risk, self.app_state.model_path))
            elif path == "/api/exotic-dividends":
                race_id = required_query(query, "race_id")
                self.send_json(exotic_dividend_report(conn, race_id))
            elif path == "/api/betting-ledger":
                race_id = query.get("race_id", [None])[0]
                self.send_json(betting_ledger_report(conn, race_id=race_id))
            else:
                self.send_error(404)

    def handle_api_post(self, path: str, query: dict[str, list[str]]) -> None:
        with connect(self.app_state.settings.db_path) as conn:
            if path == "/api/refresh-odds":
                race_id = required_query(query, "race_id")
                status = race_lifecycle_status(conn, race_id)
                if status != "scheduled":
                    self.send_json(
                        {
                            "race_id": race_id,
                            "inserted": 0,
                            "status": "frozen",
                            "message": "race_not_scheduled_last_live_odds_preserved",
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
                if status != "scheduled":
                    self.send_json(
                        {
                            "race_id": race_id,
                            "inserted": 0,
                            "status": "frozen",
                            "message": "race_not_scheduled_last_live_dividends_preserved",
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
                payload = self.app_state.focus_race(race_id)
                payload.update({"race_id": race_id, "status": status, "mode": "active_race_only"})
                self.send_json(payload)
            elif path == "/api/sleep-race":
                race_id = query.get("race_id", [None])[0]
                self.send_json(self.app_state.sleep_race(race_id))
            elif path == "/api/mark-resulted":
                race_id = required_query(query, "race_id")
                upsert_race_status(
                    conn,
                    race_id,
                    "resulted",
                    last_result_refresh_at=datetime.now(timezone.utc).isoformat(),
                )
                result = run_backtest(conn, self.app_state.model())
                upsert_race_status(
                    conn,
                    race_id,
                    "resulted",
                    last_backtest_at=datetime.now(timezone.utc).isoformat(),
                )
                conn.commit()
                self.send_json({"race_id": race_id, "backtest": asdict(result)})
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
                self.send_json(result or {"race_id": race_id, "status": "not_hkjc_race"})
            elif path == "/api/lifecycle-step":
                self.send_json(run_lifecycle_step(conn, self.app_state))
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
                training = train_model_if_requested(conn, self.app_state.model_path, 120)
                quality = data_quality_report(conn)
                versions = run_walk_forward_versions(conn)
                self.send_json(
                    {
                        "status": "done",
                        "repair": repair,
                        "training": training,
                        "quality": quality,
                        "model_versions": versions,
                    }
                )
            elif path == "/api/complete-runners":
                completion = complete_repaired_runners(
                    conn,
                    self.app_state.settings.user_agent,
                    self.app_state.settings.request_delay_seconds,
                )
                training = train_model_if_requested(conn, self.app_state.model_path, 120)
                quality = data_quality_report(conn)
                versions = run_walk_forward_versions(conn)
                self.send_json(
                    {
                        "status": "done",
                        "completion": completion,
                        "training": training,
                        "quality": quality,
                        "model_versions": versions,
                    }
                )
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
            elif path == "/api/betting-ledger/reconcile":
                race_id = query.get("race_id", [None])[0]
                result = reconcile_betting_ledger(conn, race_id=race_id)
                result["ledger"] = betting_ledger_report(conn, race_id=race_id)
                self.send_json(result)
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
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
               (SELECT count(*) FROM odds_ticks o WHERE o.race_id = r.race_id) AS odds_ticks,
               (SELECT count(*) FROM exotic_dividends ed WHERE ed.race_id = r.race_id) AS exotic_dividends
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        ORDER BY r.date, r.race_id
        """,
    )
    return [dict(row) for row in rows]


def api_predictions(conn, model: RankingModel, race_id: str) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "predictions": []}
    predictions = model.predict_race(build_race_features(conn, race_id))
    return {"race": dict(race_rows[0]), "predictions": predictions}


def api_betting(
    conn,
    model: RankingModel,
    race_id: str,
    bankroll: float,
    risk: str,
    model_path: Path | str | None = None,
) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "tickets": [], "decisions": []}
    predictions = model.predict_race(build_race_features(conn, race_id))
    status = race_lifecycle_status(conn, race_id)
    exotic_lookup = load_exotic_dividend_lookup(conn, race_id)
    payload = build_betting_decisions(
        predictions,
        status,
        bankroll=bankroll,
        risk_profile=risk,
        exotic_dividends=exotic_lookup,
    )
    race = dict(race_rows[0])
    payload["race"] = race
    if model_path is not None:
        payload["ledger"] = record_betting_payload(conn, race, payload, model_path)
    return payload


def api_results(conn, model: RankingModel, race_id: str) -> dict[str, object]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "results": []}
    predictions = model.predict_race(build_race_features(conn, race_id))
    prediction_by_horse = {
        str(row["horse_id"]): {
            "prediction_rank": index + 1,
            "win_probability": row["win_probability"],
            "top3_probability": row["top3_probability"],
            "expected_value": row["expected_value"],
        }
        for index, row in enumerate(predictions)
    }
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
        results.append(item)
    return {"race": dict(race_rows[0]), "results": results}


def run_lifecycle_step(conn, state: AppState) -> dict[str, object]:
    refresh_race_statuses(conn)
    race_id = active_refreshable_race_id(conn, state)
    if not race_id:
        active = state.active_race()
        return {
            "status": "idle",
            "message": "no_active_scheduled_race" if active else "no_active_race",
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
    next_race_id = current_refreshable_race_id(conn)
    return {
        "status": "done",
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
    rows = fetch_all(
        conn,
        """
        SELECT r.race_id
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE COALESCE(s.status, 'scheduled') = 'scheduled'
        ORDER BY r.date, r.race_id
        LIMIT 1
        """,
    )
    if rows:
        return rows[0]["race_id"]
    return None


def active_refreshable_race_id(conn, state: AppState) -> str | None:
    race_id = state.active_race()
    if not race_id:
        return None
    if race_lifecycle_status(conn, race_id) != "scheduled":
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


def query_bool(query: dict[str, list[str]], key: str, default: bool) -> bool:
    values = query.get(key)
    if not values or not values[0]:
        return default
    return values[0].strip().lower() in {"1", "true", "yes", "y", "on"}
