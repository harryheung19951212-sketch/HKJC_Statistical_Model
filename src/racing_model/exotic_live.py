from __future__ import annotations

import json
import gzip
import random
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from .exotic_dividends import upsert_exotic_dividends
from .live import parse_hkjc_race_id
from .odds import HORSE_ODDS_QUERY, browser_compatible_user_agent, flatten_json, mqtt_reason_success, parse_odds_value
from .storage import race_status


GRAPHQL_TO_INTERNAL_MARKET = {
    "QIN": "QIN",
    "QPL": "QPL",
    "FCT": "FCT",
    "TRI": "TRIO",
    "TRIO": "TRIO",
    "TCE": "TCE",
    "FF": "FIRST4",
    "FIRST4": "FIRST4",
    "QTT": "QUARTET",
    "QUARTET": "QUARTET",
}

EXOTIC_GRAPHQL_BATCHES = [
    ["QIN", "QPL"],
    ["FCT", "TRI", "TCE"],
    ["FF", "QTT"],
]


class ExoticDividendProvider(Protocol):
    source_name: str
    last_error: str | None

    def fetch_dividends(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        ...


@dataclass
class HKJCGraphQLExoticDividendProvider:
    endpoint: str
    user_agent: str
    source_name: str = "hkjc_graphql"
    last_error: str | None = None

    def fetch_dividends(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        ref = parse_hkjc_race_id(race_id)
        if not ref:
            raise ValueError(f"Not an HKJC race id: {race_id}")

        rows: list[dict[str, object]] = []
        errors = []
        for batch in EXOTIC_GRAPHQL_BATCHES:
            payload = {
                "operationName": "racing",
                "query": HORSE_ODDS_QUERY,
                "variables": {
                    "date": ref.race_date.replace("/", "-"),
                    "venueCode": ref.venue,
                    "oddsTypes": batch,
                    "raceNo": ref.race_no,
                },
            }
            request = Request(
                self.endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": browser_compatible_user_agent(self.user_agent),
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://bet.hkjc.com",
                    "Referer": f"https://bet.hkjc.com/ch/racing/wp/{ref.race_date.replace('/', '-')}/{ref.venue}/{ref.race_no}",
                },
            )
            try:
                with urlopen(request, timeout=10) as response:
                    raw_bytes = response.read()
                    if raw_bytes.startswith(b"\x1f\x8b"):
                        raw_bytes = gzip.decompress(raw_bytes)
                    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{','.join(batch)}: {exc}")
                continue
            if data.get("errors"):
                message = data["errors"][0].get("message", str(data["errors"][0]))
                errors.append(f"{','.join(batch)}: {message}")
                continue
            rows.extend(graphql_exotic_payload_to_rows(data, race_id, self.source_name))

        if not rows:
            self.last_error = " | ".join(errors) if errors else "HKJC GraphQL returned no exotic dividend rows."
            raise RuntimeError(self.last_error)
        self.last_error = None
        return rows


@dataclass
class HKJCMQTTExoticDividendProvider:
    host: str
    port: int
    username: str
    password: str
    wait_seconds: float
    source_name: str = "hkjc_mqtt"
    last_error: str | None = None

    def fetch_dividends(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        try:
            import paho.mqtt.client as mqtt
            from paho.mqtt.packettypes import PacketTypes
            from paho.mqtt.properties import Properties
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is not installed. Run: pip install -e .[live]") from exc

        ref = parse_hkjc_race_id(race_id)
        if not ref:
            raise ValueError(f"Not an HKJC race id: {race_id}")

        race_no = f"{ref.race_no:02d}"
        date_token = ref.race_date.replace("/", "")
        venue = ref.venue.lower()
        topic_markets = {
            "qin": "QIN",
            "qpl": "QPL",
            "fct": "FCT",
            "tri": "TRIO",
            "tce": "TCE",
            "ff": "FIRST4",
            "qtt": "QUARTET",
        }
        topics = [
            f"hk/d/prdt/wager/evt/01/upd/racing/{date_token}/{venue}/{race_no}/{topic}/odds/full"
            for topic in topic_markets
        ]
        reply_topic = "hk/d/prdt/wager/rpy/02/recovery/client/#"
        request_topic = "hk/d/prdt/wager/req/02/recovery/client"
        client_id = f"jcbw2_4e_exotic_{random.randint(100000, 999999)}_{int(time.time() * 1000)}"
        messages: list[tuple[str, bytes]] = []
        connected = False

        def on_connect(client, userdata, flags, reason_code, properties=None):
            nonlocal connected
            if mqtt_reason_success(reason_code):
                connected = True
            client.subscribe([(topic, 0) for topic in [*topics, reply_topic]])
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("Trace-Id", f"racing_model_{client_id}")]
            payload = json.dumps(
                {
                    "systemCode": "JCBW2_4E",
                    "senderId": client_id,
                    "topics": [
                        {"recoveryTopic": topic, "requestAllIndicator": 0}
                        for topic in topics
                    ],
                    "latestMessageInSeconds": 120,
                }
            )
            client.publish(request_topic, payload, qos=0, retain=False, properties=props)

        def on_message(client, userdata, msg):
            messages.append((msg.topic, bytes(msg.payload)))

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            transport="websockets",
            protocol=mqtt.MQTTv5,
        )
        client.username_pw_set(self.username, self.password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.ws_set_options(path="/")
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(self.host, self.port, keepalive=60)
        client.loop_start()
        time.sleep(self.wait_seconds)
        client.loop_stop()
        client.disconnect()

        if not connected:
            raise RuntimeError("MQTT did not connect.")

        rows = mqtt_exotic_messages_to_rows(messages, race_id, topic_markets, self.source_name)
        if not rows:
            raise RuntimeError("MQTT connected but returned no exotic dividend messages.")
        self.last_error = None
        return rows


@dataclass
class AutoExoticDividendProvider:
    providers: list[ExoticDividendProvider]
    source_name: str = "auto"
    last_error: str | None = None
    active_source: str = ""

    def fetch_dividends(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        errors = []
        for provider in self.providers:
            try:
                rows = provider.fetch_dividends(conn, race_id)
                self.active_source = provider.source_name
                self.last_error = None
                return rows
            except Exception as exc:
                errors.append(f"{provider.source_name}: {exc}")
        self.last_error = " | ".join(errors)
        raise RuntimeError(self.last_error)


def build_exotic_dividend_provider(settings) -> ExoticDividendProvider:
    provider = (settings.odds_provider or "auto").lower()
    graphql = HKJCGraphQLExoticDividendProvider(settings.hkjc_graphql_url, settings.user_agent)
    mqtt = HKJCMQTTExoticDividendProvider(
        settings.hkjc_mqtt_host,
        settings.hkjc_mqtt_port,
        settings.hkjc_mqtt_username,
        settings.hkjc_mqtt_password,
        settings.hkjc_mqtt_wait_seconds,
    )
    if provider == "hkjc":
        return graphql
    if provider == "mqtt":
        return mqtt
    if provider == "dev":
        return NoopExoticDividendProvider()
    return AutoExoticDividendProvider([graphql, mqtt])


def refresh_exotic_dividends(
    conn: sqlite3.Connection,
    race_id: str,
    provider: ExoticDividendProvider | None = None,
) -> dict[str, object]:
    status = race_status(conn, race_id)
    current_status = str(status["status"]) if status else "scheduled"
    if current_status not in {"scheduled", "live"}:
        return {"race_id": race_id, "inserted": 0, "status": "frozen", "source": None}
    if provider is None:
        from .config import get_settings

        provider = build_exotic_dividend_provider(get_settings())
    rows = provider.fetch_dividends(conn, race_id)
    result = upsert_exotic_dividends(conn, race_id, rows, source=getattr(provider, "active_source", provider.source_name))
    return {
        "race_id": race_id,
        "inserted": result["imported"],
        "status": "ok",
        "source": getattr(provider, "active_source", provider.source_name),
    }


@dataclass
class NoopExoticDividendProvider:
    source_name: str = "dev_no_exotic_dividend"
    last_error: str | None = None

    def fetch_dividends(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        self.last_error = "No official live exotic dividend provider is enabled in dev mode."
        raise RuntimeError(self.last_error)


def graphql_exotic_payload_to_rows(data: dict[str, Any], race_id: str, source: str) -> list[dict[str, object]]:
    rows = []
    meetings = data.get("data", {}).get("raceMeetings") or []
    timestamp = datetime.now(timezone.utc).isoformat()
    for meeting in meetings:
        for pool in meeting.get("pmPools") or []:
            market = GRAPHQL_TO_INTERNAL_MARKET.get(str(pool.get("oddsType") or "").upper())
            if not market:
                continue
            fetched_at = pool.get("lastUpdateTime") or timestamp
            for node in pool.get("oddsNodes") or []:
                row = exotic_node_to_row(race_id, market, node, source, fetched_at)
                if row:
                    rows.append(row)
                for banker_node in node.get("bankerOdds") or []:
                    banker_row = exotic_node_to_row(race_id, market, banker_node, source, fetched_at)
                    if banker_row:
                        rows.append(banker_row)
    return rows


def mqtt_exotic_messages_to_rows(
    messages: list[tuple[str, bytes]],
    race_id: str,
    topic_markets: dict[str, str],
    source: str,
) -> list[dict[str, object]]:
    rows = []
    timestamp = datetime.now(timezone.utc).isoformat()
    for topic, payload in messages:
        market = market_from_topic(topic, topic_markets)
        if not market:
            continue
        try:
            data = json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        for item in flatten_json(data):
            row = exotic_node_to_row(race_id, market, item, source, timestamp)
            if row:
                rows.append(row)
    return rows


def market_from_topic(topic: str, topic_markets: dict[str, str]) -> str | None:
    parts = [part.lower() for part in topic.split("/")]
    for topic_token, market in topic_markets.items():
        if topic_token in parts:
            return market
    return None


def exotic_node_to_row(
    race_id: str,
    market: str,
    node: dict[str, Any],
    source: str,
    fetched_at: str,
) -> dict[str, object] | None:
    combination = (
        node.get("combString")
        or node.get("combination")
        or node.get("combinationString")
        or node.get("comb")
    )
    dividend = (
        node.get("oddsValue")
        or node.get("odds")
        or node.get("currentOdds")
        or node.get("dividend")
    )
    value = parse_odds_value(dividend)
    if combination in {None, ""} or value is None or value <= 0:
        return None
    return {
        "race_id": race_id,
        "market": market,
        "combination": str(combination),
        "dividend": value,
        "dividend_status": "probable",
        "source": source,
        "fetched_at": fetched_at,
        "notes": "live_exotic_dividend",
    }
