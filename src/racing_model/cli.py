from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .app_server import run_server
from .backfill import (
    complete_repaired_runners,
    data_quality_report,
    load_hkjc_date_range,
    repair_orphan_result_runners,
    train_model_if_requested,
)
from .backtest import run_backtest
from .betting_ledger import betting_ledger_report, reconcile_betting_ledger
from .config import display_database_target, get_settings
from .db_migrate import database_counts, migrate_sqlite_to_database
from .evolution import evaluate_model_evolution, generate_codex_iteration, generate_openai_iteration
from .ev_blackbox import run_ev_blackbox_training, run_ev_daily_walk_forward, run_ev_final_all_training
from .exotic_dividends import exotic_dividend_report, upsert_exotic_dividends
from .exotic_live import build_exotic_dividend_provider, refresh_exotic_dividends
from .features import build_race_features, build_training_races
from .gpt_report import generate_report
from .html_report import export_race_report
from .model import RankingModel
from .model_registry import model_registry_report, promote_latest_model, run_and_record_model_registry
from .odds import backfill_final_place_odds, build_official_odds_provider
from .scrapers.base import PoliteHttpClient
from .scrapers.hkjc import HKJCSource, write_hkjc_csv_bundle
from .storage import connect, import_csv, init_db
from .walk_forward import run_walk_forward_versions


TABLE_FILES = {
    "races": "races.csv",
    "runners": "runners.csv",
    "results": "results.csv",
    "workouts": "workouts.csv",
    "odds_ticks": "odds.csv",
}


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    configure_console_encoding()
    parser = argparse.ArgumentParser(prog="racing-model")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")
    sub.add_parser("import-sample")

    migrate_parser = sub.add_parser("migrate-sqlite-to-db")
    migrate_parser.add_argument("--sqlite-path", default="data/racing.db")
    migrate_parser.add_argument("--target", default=None, help="DATABASE_URL or SQLite path. Defaults to current app DB.")
    migrate_parser.add_argument("--replace", action="store_true")

    import_parser = sub.add_parser("import-csv")
    import_parser.add_argument("--dir", default="data/import")

    train_parser = sub.add_parser("train")
    train_parser.add_argument("--model-path", default="models/baseline.json")
    train_parser.add_argument("--epochs", type=int, default=400)

    predict_parser = sub.add_parser("predict")
    predict_parser.add_argument("--race-id", required=True)
    predict_parser.add_argument("--model-path", default="models/baseline.json")

    backtest_parser = sub.add_parser("backtest")
    backtest_parser.add_argument("--model-path", default="models/baseline.json")
    backtest_parser.add_argument("--min-ev", type=float, default=0.05)

    evolution_parser = sub.add_parser("evolution")
    evolution_parser.add_argument("--model-path", default="models/baseline.json")
    evolution_parser.add_argument("--min-ev", type=float, default=0.05)
    evolution_parser.add_argument("--gpt", action="store_true")

    wf_parser = sub.add_parser("walk-forward")
    wf_parser.add_argument("--min-train-races", type=int, default=1)
    wf_parser.add_argument("--epochs", type=int, default=80)
    wf_parser.add_argument("--min-ev", type=float, default=0.05)

    ev_blackbox_parser = sub.add_parser("blackbox-ev-train")
    ev_blackbox_parser.add_argument("--output-dir", default="reports")
    ev_blackbox_parser.add_argument("--holdout-date", default="2026-05-09")
    ev_blackbox_parser.add_argument("--trials", type=int, default=80)
    ev_blackbox_parser.add_argument("--seed", type=int, default=20260509)
    ev_blackbox_parser.add_argument("--min-epochs", type=int, default=80)
    ev_blackbox_parser.add_argument("--max-epochs", type=int, default=260)
    ev_blackbox_parser.add_argument("--stake", type=float, default=10.0)
    ev_blackbox_parser.add_argument("--max-train-races", type=int, default=0)
    ev_blackbox_parser.add_argument("--progress-every", type=int, default=0)

    ev_daily_parser = sub.add_parser("blackbox-ev-walk-forward-days")
    ev_daily_parser.add_argument("--output-dir", default="reports")
    ev_daily_parser.add_argument("--start-date", default=None)
    ev_daily_parser.add_argument("--end-date", default=None)
    ev_daily_parser.add_argument("--trials-per-day", type=int, default=12)
    ev_daily_parser.add_argument("--seed", type=int, default=20260509)
    ev_daily_parser.add_argument("--min-epochs", type=int, default=40)
    ev_daily_parser.add_argument("--max-epochs", type=int, default=120)
    ev_daily_parser.add_argument("--stake", type=float, default=10.0)
    ev_daily_parser.add_argument("--validation-dates", type=int, default=4)
    ev_daily_parser.add_argument("--min-train-dates", type=int, default=8)
    ev_daily_parser.add_argument("--max-train-races", type=int, default=260)
    ev_daily_parser.add_argument("--max-test-dates", type=int, default=0)
    ev_daily_parser.add_argument("--progress-every", type=int, default=1)

    ev_final_parser = sub.add_parser("blackbox-ev-train-final")
    ev_final_parser.add_argument("--output-dir", default="reports")
    ev_final_parser.add_argument("--trials", type=int, default=80)
    ev_final_parser.add_argument("--seed", type=int, default=20260512)
    ev_final_parser.add_argument("--min-epochs", type=int, default=80)
    ev_final_parser.add_argument("--max-epochs", type=int, default=220)
    ev_final_parser.add_argument("--stake", type=float, default=10.0)
    ev_final_parser.add_argument("--validation-dates", type=int, default=4)
    ev_final_parser.add_argument("--max-candidate-train-races", type=int, default=0)
    ev_final_parser.add_argument("--progress-every", type=int, default=0)

    registry_parser = sub.add_parser("model-registry")
    registry_parser.add_argument("--run", action="store_true", help="Run and persist a new out-of-sample registry entry.")
    registry_parser.add_argument("--promote", action="store_true", help="Promote the latest gated candidate into the active model file.")
    registry_parser.add_argument("--model-path", default="models/baseline.json")
    registry_parser.add_argument("--min-train-races", type=int, default=1)
    registry_parser.add_argument("--epochs", type=int, default=80)
    registry_parser.add_argument("--min-ev", type=float, default=0.05)

    ledger_parser = sub.add_parser("betting-ledger")
    ledger_parser.add_argument("--race-id", default=None)
    ledger_parser.add_argument("--reconcile", action="store_true")

    exotic_parser = sub.add_parser("import-exotic-dividends")
    exotic_parser.add_argument("--race-id", required=True)
    exotic_parser.add_argument("--file", required=True, help="CSV or JSON rows with market, combination, dividend.")
    exotic_parser.add_argument("--source", default="manual")
    exotic_parser.add_argument("--status", default="probable", choices=["probable", "final", "estimated"])

    refresh_exotic_parser = sub.add_parser("refresh-exotic-dividends")
    refresh_exotic_parser.add_argument("--race-id", required=True)

    final_place_parser = sub.add_parser("backfill-final-place-odds")
    final_place_parser.add_argument("--race-id", default=None, help="Defaults to every resulted race.")

    quality_parser = sub.add_parser("data-quality")

    repair_parser = sub.add_parser("repair-data")
    repair_parser.add_argument("--model-path", default="models/baseline.json")
    repair_parser.add_argument("--epochs", type=int, default=120)

    complete_parser = sub.add_parser("complete-runners")
    complete_parser.add_argument("--model-path", default="models/baseline.json")
    complete_parser.add_argument("--epochs", type=int, default=120)

    backfill_parser = sub.add_parser("backfill-hkjc")
    backfill_parser.add_argument("--start", required=True)
    backfill_parser.add_argument("--end", required=True)
    backfill_parser.add_argument("--venue", required=True, choices=["ST", "HV"])
    backfill_parser.add_argument("--races", type=int, default=10)
    backfill_parser.add_argument("--model-path", default="models/baseline.json")
    backfill_parser.add_argument("--epochs", type=int, default=120)

    report_parser = sub.add_parser("gpt-report")
    report_parser.add_argument("--race-id", required=True)
    report_parser.add_argument("--model-path", default="models/baseline.json")

    html_parser = sub.add_parser("export-html")
    html_parser.add_argument("--race-id", required=True)
    html_parser.add_argument("--model-path", default="models/baseline.json")
    html_parser.add_argument("--out", required=True)

    hkjc_parser = sub.add_parser("fetch-hkjc")
    hkjc_parser.add_argument("kind", choices=["racecard", "results", "trackwork"])
    hkjc_parser.add_argument("--date", required=True, help="Race date, e.g. 2026/05/06")
    hkjc_parser.add_argument("--venue", required=True, choices=["ST", "HV"])
    hkjc_parser.add_argument("--race-no", required=True, type=int)
    hkjc_parser.add_argument("--out-dir", required=True)

    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--model-path", default="models/baseline.json")
    serve_parser.add_argument("--odds-interval", type=int, default=30)

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "init-db":
        init_db(settings.db_path)
        print(f"Initialized {display_database_target(settings.db_path)}")
        return

    if args.command == "migrate-sqlite-to-db":
        target = args.target or settings.db_path
        result = migrate_sqlite_to_database(args.sqlite_path, target, replace=args.replace)
        result["counts"] = database_counts(target)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.command == "serve":
        run_server(args.host, args.port, Path(args.model_path), args.odds_interval)
        return

    if args.command == "fetch-hkjc":
        client = PoliteHttpClient(settings.user_agent, settings.backfill_request_delay_seconds)
        source = HKJCSource(client)
        if args.kind == "racecard":
            fetched = source.fetch_racecard_page(args.date, args.venue, args.race_no)
            chinese = source.fetch_chinese_racecard_page(args.date, args.venue, args.race_no)
            parsed = source.parse_racecard(fetched.body, args.date, args.venue, args.race_no, chinese.body)
        elif args.kind == "results":
            fetched = source.fetch_results_page(args.date, args.venue, args.race_no)
            parsed = source.parse_results(fetched.body, args.date, args.venue, args.race_no)
        else:
            fetched = source.fetch_trackwork_page(args.date, args.venue, args.race_no)
            parsed = source.parse_trackwork(fetched.body, args.date, args.venue, args.race_no)
        files = write_hkjc_csv_bundle(parsed, args.out_dir)
        print(
            json.dumps(
                {
                    "url": fetched.url,
                    "fetched_at": fetched.fetched_at,
                    "files": {key: str(path) for key, path in files.items()},
                    "rows": {key: len(value) for key, value in parsed.items()},
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if args.command in {
        "import-sample",
        "import-csv",
        "model-registry",
        "betting-ledger",
        "import-exotic-dividends",
        "refresh-exotic-dividends",
        "backfill-final-place-odds",
    }:
        init_db(settings.db_path)

    with connect(settings.db_path) as conn:
        if args.command == "import-sample":
            imported = import_directory(conn, Path("data/sample"))
            print(json.dumps(imported, indent=2, sort_keys=True))
        elif args.command == "import-csv":
            imported = import_directory(conn, Path(args.dir))
            print(json.dumps(imported, indent=2, sort_keys=True))
        elif args.command == "train":
            model = RankingModel.new()
            races = build_training_races(conn)
            model.fit(races, epochs=args.epochs)
            model.save(args.model_path)
            print(f"Trained on {len(races)} races. Saved {args.model_path}")
        elif args.command == "predict":
            model = RankingModel.load(args.model_path)
            runners = build_race_features(conn, args.race_id)
            print(json.dumps(model.predict_race(runners), indent=2, ensure_ascii=False))
        elif args.command == "backtest":
            model = RankingModel.load(args.model_path)
            result = run_backtest(conn, model, min_expected_value=args.min_ev)
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        elif args.command == "evolution":
            model = RankingModel.load(args.model_path)
            report = evaluate_model_evolution(conn, model, min_expected_value=args.min_ev)
            if args.gpt:
                try:
                    report["ai_engine"] = "codex_cli"
                    report["gpt_notes"] = generate_codex_iteration(
                        settings.codex_cli_command,
                        settings.codex_model,
                        settings.codex_reasoning_effort,
                        report,
                        settings.codex_timeout_seconds,
                    )
                except Exception:
                    if not settings.openai_api_key:
                        raise
                    report["ai_engine"] = "openai_api"
                    report["gpt_notes"] = generate_openai_iteration(
                        settings.openai_api_key,
                        settings.openai_model,
                        report,
                    )
            print(json.dumps(report, indent=2, ensure_ascii=False))
        elif args.command == "walk-forward":
            result = run_walk_forward_versions(
                conn,
                min_train_races=args.min_train_races,
                epochs=args.epochs,
                min_expected_value=args.min_ev,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "blackbox-ev-train":
            result = run_ev_blackbox_training(
                conn,
                output_dir=args.output_dir,
                holdout_date=args.holdout_date,
                trials=args.trials,
                seed=args.seed,
                min_epochs=args.min_epochs,
                max_epochs=args.max_epochs,
                stake=args.stake,
                max_train_races=args.max_train_races or None,
                progress_every=args.progress_every,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "blackbox-ev-walk-forward-days":
            result = run_ev_daily_walk_forward(
                conn,
                output_dir=args.output_dir,
                start_date=args.start_date,
                end_date=args.end_date,
                trials_per_day=args.trials_per_day,
                seed=args.seed,
                min_epochs=args.min_epochs,
                max_epochs=args.max_epochs,
                stake=args.stake,
                validation_dates=args.validation_dates,
                min_train_dates=args.min_train_dates,
                max_train_races=args.max_train_races or None,
                max_test_dates=args.max_test_dates or None,
                progress_every=args.progress_every,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "blackbox-ev-train-final":
            result = run_ev_final_all_training(
                conn,
                output_dir=args.output_dir,
                trials=args.trials,
                seed=args.seed,
                min_epochs=args.min_epochs,
                max_epochs=args.max_epochs,
                stake=args.stake,
                validation_dates=args.validation_dates,
                max_candidate_train_races=args.max_candidate_train_races or None,
                progress_every=args.progress_every,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "model-registry":
            if args.run:
                result = run_and_record_model_registry(
                    conn,
                    args.model_path,
                    trigger="cli",
                    min_train_races=args.min_train_races,
                    epochs=args.epochs,
                    min_expected_value=args.min_ev,
                )
            elif args.promote:
                result = promote_latest_model(conn, args.model_path, epochs=args.epochs)
            else:
                result = model_registry_report(conn)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "betting-ledger":
            if args.reconcile:
                result = reconcile_betting_ledger(conn, race_id=args.race_id)
                result["ledger"] = betting_ledger_report(conn, race_id=args.race_id)
            else:
                result = betting_ledger_report(conn, race_id=args.race_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "import-exotic-dividends":
            rows = load_structured_rows(Path(args.file))
            result = upsert_exotic_dividends(
                conn,
                args.race_id,
                rows,
                source=args.source,
                dividend_status=args.status,
            )
            result["report"] = exotic_dividend_report(conn, args.race_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "refresh-exotic-dividends":
            result = refresh_exotic_dividends(conn, args.race_id, build_exotic_dividend_provider(settings))
            result["report"] = exotic_dividend_report(conn, args.race_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "backfill-final-place-odds":
            if args.race_id:
                result = backfill_final_place_odds(conn, args.race_id, build_official_odds_provider(settings))
            else:
                race_ids = [
                    row["race_id"]
                    for row in conn.execute(
                        """
                        SELECT DISTINCT race_id
                        FROM results
                        ORDER BY race_id
                        """
                    )
                ]
                result = {
                    "status": "done",
                    "races": [
                        backfill_final_place_odds(conn, race_id, build_official_odds_provider(settings))
                        for race_id in race_ids
                    ],
                }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "data-quality":
            print(json.dumps(data_quality_report(conn), indent=2, ensure_ascii=False))
        elif args.command == "repair-data":
            repair = repair_orphan_result_runners(conn)
            training = train_model_if_requested(conn, args.model_path, args.epochs)
            result = {
                "repair": repair,
                "training": training,
                "quality": data_quality_report(conn),
                "model_versions": run_walk_forward_versions(conn),
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "complete-runners":
            completion = complete_repaired_runners(conn, settings.user_agent, settings.backfill_request_delay_seconds)
            training = train_model_if_requested(conn, args.model_path, args.epochs)
            result = {
                "completion": completion,
                "training": training,
                "quality": data_quality_report(conn),
                "model_versions": run_walk_forward_versions(conn),
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "backfill-hkjc":
            result = load_hkjc_date_range(
                conn,
                args.start,
                args.end,
                args.venue,
                args.races,
                settings.user_agent,
                settings.backfill_request_delay_seconds,
                model_path=args.model_path,
                train_epochs=args.epochs,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "gpt-report":
            if not settings.openai_api_key:
                raise SystemExit("OPENAI_API_KEY is not configured.")
            model = RankingModel.load(args.model_path)
            runners = build_race_features(conn, args.race_id)
            predictions = model.predict_race(runners)
            print(generate_report(settings.openai_api_key, settings.openai_model, args.race_id, predictions))
        elif args.command == "export-html":
            race_rows = conn.execute("SELECT * FROM races WHERE race_id = ?", (args.race_id,)).fetchall()
            if not race_rows:
                raise SystemExit(f"Race not found: {args.race_id}")
            model = RankingModel.load(args.model_path)
            runners = build_race_features(conn, args.race_id)
            predictions = model.predict_race(runners)
            target = export_race_report(dict(race_rows[0]), predictions, args.out)
            print(f"Exported {target}")


def import_directory(conn, directory: Path) -> dict[str, int]:
    if not directory.exists():
        raise SystemExit(f"Import directory not found: {directory}")
    imported = {}
    for table, filename in TABLE_FILES.items():
        path = directory / filename
        if path.exists():
            imported[table] = import_csv(conn, table, path)
    conn.commit()
    return imported


def load_structured_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        raise SystemExit(f"Import file not found: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("items", [])
        return list(data)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    main()
