from racing_model.scrapers.hkjc import (
    HKJCSource,
    merge_runner_localization,
    parse_chinese_racecard_runners,
    parse_chinese_result_runners,
    parse_final_exotic_dividends,
    parse_horse_profile_last_six_runs,
    parse_racecard_runners,
    parse_result_rows,
)
from racing_model.scrapers.base import PoliteHttpClient


def test_parse_hkjc_tokenized_racecard_rows() -> None:
    lines = [
        "MY Race Card LIST",
        "Horse No.",
        "Last 6 Runs",
        "Colour",
        "Horse",
        "Brand No.",
        "Wt.",
        "Jockey",
        "Draw",
        "Trainer",
        "1",
        "4/1/8/4/7/7",
        "HAILTOTHEVICTORS",
        "J508",
        "135",
        "L Ferraris",
        "7",
        "D Eustace",
        "-",
        "40",
        "0",
        "1121",
        "+8",
        "1.39.06",
        "5",
        "-",
        "g",
        "778,750",
        "+ 1",
        "17",
        "B/TT",
        "Graham Kot Li Heng & Amy Chau",
        "Stand-by Starter",
    ]

    rows = parse_racecard_runners(lines, "HK20260506-ST-01")

    assert rows == [
        {
            "race_id": "HK20260506-ST-01",
            "horse_no": 1,
            "horse_id": "J508",
            "horse_name": "HAIL TO THE VICTORS",
            "last_six_runs": "4/1/8/4/7/7",
            "horse_name_zh": "",
            "jockey": "L Ferraris",
            "jockey_zh": "",
            "trainer": "D Eustace",
            "trainer_zh": "",
            "draw": 7,
            "weight_lbs": 135.0,
            "official_rating": 40.0,
            "age": 5,
            "sex": "G",
            "running_style": "unknown",
            "gear": "B/TT",
        }
    ]


def test_parse_horse_profile_last_six_runs_from_three_season_record() -> None:
    lines = [
        "北地烈馬 (J488)",
        "馬匹近三季往績紀錄 - 北地烈馬",
        "場次",
        "名次",
        "日期",
        "25/26",
        "馬季",
        "610",
        "WV-A",
        "15/04/26",
        '跑馬地草地"A"',
        "1200",
        "558",
        "12",
        "29/03/26",
        '沙田草地"A+3"',
        "1000",
        "512",
        "04",
        "11/03/26",
        '跑馬地草地"C+3"',
        "1200",
        "467",
        "01",
        "25/02/26",
        '跑馬地草地"B"',
        "1200",
        "419",
        "04",
        "08/02/26",
        '沙田草地"C"',
        "1200",
        "348",
        "08",
        "14/01/26",
        '跑馬地草地"B"',
        "1650",
        "319",
        "06",
        "04/01/26",
    ]

    assert parse_horse_profile_last_six_runs(lines) == "WV-A/12/4/1/4/8"


def test_parse_hkjc_tokenized_result_rows() -> None:
    lines = [
        "Pla.",
        "Horse No.",
        "Horse",
        "Jockey",
        "Trainer",
        "Act. Wt.",
        "Declar. Horse Wt.",
        "Dr.",
        "LBW",
        "Running",
        "Position",
        "Finish Time",
        "Win Odds",
        "1",
        "9",
        "MEEPMEEP",
        "(H234)",
        "H Bowman",
        "J Size",
        "128",
        "1101",
        "2",
        "---",
        "7",
        "7",
        "5",
        "1",
        "1:40.31",
        "3.8",
        "2",
        "13",
        "ORIENTAL SURPRISE",
        "(K028)",
        "P N Wong",
        "D A Hayes",
        "111",
        "1059",
        "13",
        "1-1/4",
        "2",
        "2",
        "1",
        "2",
        "1:40.51",
        "59",
        "Dividend",
        "Pool",
        "Winning Combination",
        "Dividend (HK$)",
        "WIN",
        "9",
        "38.00",
        "PLACE",
        "9",
        "16.00",
        "13",
        "89.50",
        "QUINELLA",
    ]

    rows = parse_result_rows(lines, "HK20260506-ST-01")

    assert len(rows) == 2
    assert rows[0]["horse_id"] == "H234"
    assert rows[0]["horse_no"] == 9
    assert rows[0]["finish_position"] == 1
    assert rows[0]["margin_lengths"] == 0.0
    assert rows[0]["place_odds"] == 1.6
    assert rows[1]["horse_id"] == "K028"
    assert rows[1]["horse_no"] == 13
    assert rows[1]["finish_position"] == 2
    assert rows[1]["margin_lengths"] == 1.25
    assert rows[1]["place_odds"] == 8.95


def test_parse_results_builds_fallback_race_and_runners() -> None:
    lines = [
        "RACE 1 (665)",
        "Class 5 - 1650M - (40-0)",
        "Going :",
        "GOOD",
        "OSMANTHUS HANDICAP",
        "Course :",
        "ALL WEATHER TRACK",
        "HK$ 875,000",
        "Pla.",
        "Horse No.",
        "Horse",
        "Jockey",
        "Trainer",
        "Act. Wt.",
        "Declar. Horse Wt.",
        "Dr.",
        "LBW",
        "Running",
        "Position",
        "Finish Time",
        "Win Odds",
        "1",
        "9",
        "MEEPMEEP",
        "(H234)",
        "H Bowman",
        "J Size",
        "128",
        "1101",
        "2",
        "---",
        "7",
        "7",
        "5",
        "1",
        "1:40.31",
        "3.8",
        "Dividend",
        "Pool",
        "Winning Combination",
        "Dividend (HK$)",
        "WIN",
        "9",
        "38.00",
    ]
    source = HKJCSource(PoliteHttpClient("test", 0))

    parsed = source.parse_results("\n".join(f"<div>{line}</div>" for line in lines), "2026/05/06", "ST", 1)

    assert parsed["races"][0]["race_id"] == "HK20260506-ST-01"
    assert parsed["races"][0]["course"] == "ALL WEATHER TRACK"
    assert parsed["races"][0]["distance_m"] == 1650
    assert parsed["runners"][0]["horse_id"] == "H234"
    assert parsed["runners"][0]["horse_no"] == 9
    assert parsed["runners"][0]["horse_name"] == "MEEPMEEP"
    assert parsed["runners"][0]["draw"] == 2
    assert parsed["runners"][0]["weight_lbs"] == 128.0
    assert parsed["results"][0]["finish_position"] == 1
    assert parsed["odds_ticks"][0]["source"] == "hkjc_results_final"


def test_parse_result_dividend_table_imports_final_exotic_dividends() -> None:
    lines = [
        "Dividend",
        "Pool",
        "Winning Combination",
        "Dividend (HK$)",
        "QUINELLA",
        "9,13",
        "1,490.00",
        "QUINELLA PLACE",
        "9,13",
        "408.00",
        "9,11",
        "28.00",
        "FORECAST",
        "9,13",
        "2,215.00",
        "TIERCE",
        "9,13,11",
        "10,103.00",
        "TRIO",
        "9,11,13",
        "1,397.00",
        "FIRST 4",
        "1,9,11,13",
        "1,314.00",
        "QUARTET",
        "9,13,11,1",
        "49,644.00",
        "Dividend Note: For Winning Combination",
    ]

    rows = parse_final_exotic_dividends(lines, "HK20260506-ST-01")

    by_market = {row["market"]: row for row in rows}
    assert by_market["TCE"]["combination_key"] == "9>13>11"
    assert by_market["TCE"]["dividend"] == 1010.3
    assert by_market["QUARTET"]["combination_key"] == "9>13>11>1"
    assert by_market["QUARTET"]["dividend"] == 4964.4
    assert by_market["FIRST4"]["combination_key"] == "1+9+11+13"


def test_merge_chinese_runner_names() -> None:
    english_rows = [
        {
            "race_id": "HK20260506-ST-01",
            "horse_no": 1,
            "horse_id": "J508",
            "horse_name": "HAIL TO THE VICTORS",
            "last_six_runs": "4/1/8/4/7/7",
            "horse_name_zh": "",
            "jockey": "L Ferraris",
            "jockey_zh": "",
            "trainer": "D Eustace",
            "trainer_zh": "",
            "draw": 7,
        }
    ]
    chinese_lines = [
        "我 的 排 位 表",
        "馬匹編號",
        "6次近績",
        "綵衣",
        "馬名",
        "烙號",
        "負磅",
        "騎師",
        "檔位",
        "練馬師",
        "1",
        "4/1/8/4/7/7",
        "光輝歲月",
        "J508",
        "135",
        "霍宏聲",
        "7",
        "游達榮",
        "-",
        "40",
        "0",
        "後 備 馬 匹",
    ]

    zh_rows = parse_chinese_racecard_runners(chinese_lines, "HK20260506-ST-01")
    merged = merge_runner_localization(english_rows, zh_rows)

    assert merged[0]["horse_name_zh"] == "光輝歲月"
    assert merged[0]["jockey_zh"] == "霍宏聲"
    assert merged[0]["trainer_zh"] == "游達榮"


def test_parse_chinese_result_runner_localization() -> None:
    lines = [
        "名次",
        "馬號",
        "馬名",
        "騎師",
        "練馬師",
        "實際",
        "負磅",
        "排位",
        "體重",
        "檔位",
        "頭馬",
        "距離",
        "沿途",
        "走位",
        "完成",
        "時間",
        "獨贏",
        "賠率",
        "1",
        "7",
        "熾烈神駒",
        "(J157)",
        "潘頓",
        "沈集成",
        "122",
        "1268",
        "2",
        "---",
        "2",
        "2",
        "1",
        "1:08.88",
        "3.2",
        "派彩",
    ]

    rows = parse_chinese_result_runners(lines, "HK20260506-ST-09")

    assert rows == [
        {
            "race_id": "HK20260506-ST-09",
            "horse_no": 7,
            "horse_id": "J157",
            "horse_name_zh": "熾烈神駒",
            "jockey_zh": "潘頓",
            "trainer_zh": "沈集成",
        }
    ]
