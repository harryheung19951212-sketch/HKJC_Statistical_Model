from racing_model.scrapers.hkjc import (
    merge_runner_localization,
    parse_chinese_racecard_runners,
    parse_racecard_runners,
    parse_result_rows,
)


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


def test_merge_chinese_runner_names() -> None:
    english_rows = [
        {
            "race_id": "HK20260506-ST-01",
            "horse_no": 1,
            "horse_id": "J508",
            "horse_name": "HAIL TO THE VICTORS",
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
