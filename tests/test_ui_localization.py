import re
from pathlib import Path

from racing_model.app_server import api_betting, api_predictions
from racing_model.backfill import normalize_hkjc_date
from racing_model.model import RankingModel
from racing_model.storage import connect, init_db, insert_rows


ROOT = Path(__file__).resolve().parents[1]


def test_hkjc_date_normalization_accepts_date_picker_and_legacy_formats() -> None:
    assert normalize_hkjc_date("2026-05-06") == "2026/05/06"
    assert normalize_hkjc_date("2026/05/06") == "2026/05/06"
    assert normalize_hkjc_date("2026-5-6") == "2026/05/06"


def test_all_ui_date_fields_use_browser_date_picker() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<input id="race-date" type="date"' in html
    assert '<input id="backfill-start" type="date"' in html
    assert '<input id="backfill-end" type="date"' in html


def test_sidebar_ingestion_forms_are_collapsible() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<details class="sidebar-action">' in html
    assert "<summary><span>載入賽日</span><b>賽日 / 馬場</b></summary>" in html
    assert "<summary><span>歷史回填</span><b>日期範圍 / 重訓</b></summary>" in html


def test_manual_lifecycle_button_is_named_global_update() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<button id="lifecycle-step" type="button">全域更新</button>' in html
    assert "流程一步" not in html


def test_model_reports_are_top_level_menu_pages() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")

    assert '<nav class="page-menu" aria-label="功能頁面">' in html
    assert 'data-view="race">賽事分析</button>' in html
    assert 'data-view="coverage">方程式覆蓋率 / 盲點報告</button>' in html
    assert 'data-view="analytics">回測 / 智能迭代中心</button>' in html
    assert '<section id="coverage-view" class="app-view">' in html
    assert "36 項覆蓋狀態" in html
    assert "32 項覆蓋狀態" not in html
    assert '<section id="analytics-view" class="app-view">' in html
    assert '<details class="panel coverage-panel">' not in html
    assert '<details class="panel analytics-panel">' not in html


def test_coverage_items_expand_on_click() -> None:
    js = (ROOT / "src/racing_model/web/app.js").read_text(encoding="utf-8")

    assert '<details class="coverage-card status-${item.status_key}">' in js
    assert '<summary class="coverage-card-head">' in js
    assert "<b>模型風險</b>" in js
    assert "<b>驗證門檻</b>" in js


def test_auto_refresh_preserves_existing_race_panels() -> None:
    js = (ROOT / "src/racing_model/web/app.js").read_text(encoding="utf-8")

    assert "preservePanels: true" in js
    assert "renderResultsLoading({ preserve: preservePanels })" in js
    assert "renderOddsHistoryLoading({ preserve: preservePanels })" in js
    assert "preserveDeferred: preservePanels" in js
    assert "function preservePanelDuringRefresh" in js
    assert "raceRefreshInFlight" in js
    assert "refresh_odds=1" in js
    assert "refresh_exotics=1" in js
    assert 'folder.open = Boolean(raceFolderState[key] ?? (key === "upcoming"))' in js


def test_browser_refresh_preserves_page_and_tab_state() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/racing_model/web/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/racing_model/web/styles.css").read_text(encoding="utf-8")

    assert '<button id="refresh-now" type="button">刷新賠率</button>' in html
    assert 'const UI_STATE_KEY = "hkjc-racing-ui-state"' in js
    assert 'savedUiValue("selectedRaceId", null)' in js
    assert 'savedUiValue("currentView", "race")' in js
    assert "saveUiState({ activeRaceTab })" in js
    assert "lastSixRunsLabel(row)" in js
    assert "節奏吻合" in js
    assert "row.pace_note" in js
    assert "現時估算" in js
    assert "新建議" not in js
    assert "items.slice(0, 10).map(renderSettlementCard)" not in js
    assert "items.slice(0, 10).map((row)" not in js
    assert "settings.kelly_label" in js
    assert "promotion-scorecard-summary" in html
    assert "promotion-scorecard-sections" in html
    assert "promotion-scorecard-slices" in html
    assert "function renderPromotionScorecard" in js
    assert "renderPromotionScorecard(dashboard.promotion_scorecard || {})" in js
    assert "function renderPromotionSectionMetrics" in js
    assert "function horseContextLabel" in js
    assert "function horsePaceLabel" in js
    assert "trip_luck_score" in js
    assert "ability_issue_score" in js
    assert "pace_advantage_score" in js
    assert "traffic_risk_score" in js
    assert "體重變化/趨勢" in js
    assert "走位際遇/能力疑點" in js
    assert "步速優勢/路程步速" in js
    assert "隱藏能力" in js
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in css


def test_race_page_prioritizes_prediction_betting_and_collapsible_diagnostics() -> None:
    html = (ROOT / "src/racing_model/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/racing_model/web/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/racing_model/web/styles.css").read_text(encoding="utf-8")

    prediction_pos = re.search(r'<div class="panel predictions-panel"[^>]*>', html).start()
    detail_pos = re.search(r'<div class="panel detail-panel"[^>]*>', html).start()
    situation_pos = re.search(r'<div class="panel situation-panel"[^>]*>', html).start()
    betting_pos = re.search(r'<div class="panel betting-panel"[^>]*>', html).start()
    feed_pos = re.search(r'<details class="panel feed-panel fold-panel"[^>]*>', html).start()
    comparison_pos = re.search(r'<details class="panel comparison-panel fold-panel"[^>]*>', html).start()

    assert prediction_pos < detail_pos < situation_pos < betting_pos < feed_pos < comparison_pos
    assert 'class="race-tab active"' in html
    assert 'data-race-tab-target="betting"' in html
    assert 'data-race-tab="betting"' in html
    assert '<h3>馬匹詳情 / 單匹賠率走勢</h3>' in html
    assert 'aria-label="單匹賠率走勢圖"' in html
    assert '<div class="panel odds-panel">' not in html
    assert '<table class="prediction-table">' in html
    assert ">人馬配搭<span" in html
    assert ">市場價值<span" in html
    assert "<th>獨贏市場</th>" not in html
    assert 'data-sort-key="rank"' in html
    assert 'data-sort-key="value"' in html
    assert "grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);" in css
    assert ".prediction-table {\n  min-width: 0;" in css
    assert ".sort-button.active" in css
    assert ".predictions-panel {\n  grid-column: 1;\n  grid-row: 1;" in css
    assert ".detail-panel {\n  grid-column: 2;\n  grid-row: 1;" in css
    assert "align-items: stretch;" in css
    assert "function renderRaceSituationCharts" in js
    assert "function renderSelectedHorseOddsChart" in js
    assert 'const predictionSort = { key: "rank", direction: "asc" }' in js
    assert "function sortedPredictions" in js
    assert "setupPredictionSorting();" in js
    assert "row.horse_id === selectedHorseId" in js


def test_predictions_and_betting_payloads_prefer_chinese_names(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "date": "2026/05/06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 4",
                    "prize": 1000000,
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "ENGLISH HORSE",
                    "horse_name_zh": "中文馬",
                    "jockey": "English Jockey",
                    "jockey_zh": "中文騎師",
                    "trainer": "English Trainer",
                    "trainer_zh": "中文練馬師",
                    "draw": 1,
                    "weight_lbs": 120,
                    "official_rating": 50,
                    "age": 4,
                    "sex": "G",
                    "running_style": "pace",
                    "gear": "",
                }
            ],
        )
        insert_rows(
            conn,
            "odds_ticks",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "timestamp": "2026-05-06T12:00:00+00:00",
                    "win_odds": 3.0,
                    "place_odds": 1.5,
                    "source": "hkjc_mqtt",
                }
            ],
        )
        conn.commit()

        predictions = api_predictions(conn, RankingModel.new(), "HK20260506-ST-01")["predictions"]
        assert predictions[0]["display_name"] == "中文馬"
        assert predictions[0]["display_jockey"] == "中文騎師"
        assert predictions[0]["display_trainer"] == "中文練馬師"

        betting = api_betting(conn, RankingModel.new(), "HK20260506-ST-01", 10000, "aggressive")
        assert {row["horse_name"] for row in betting["decisions"]} == {"中文馬"}
        assert {row["jockey"] for row in betting["decisions"]} == {"中文騎師"}
        assert {row["trainer"] for row in betting["decisions"]} == {"中文練馬師"}
