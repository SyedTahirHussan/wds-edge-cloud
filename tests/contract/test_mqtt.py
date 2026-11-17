"""MQTT extension against FROST's broker (the fake server has no broker)."""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

import pytest

from flowmine.sta import mapping
from flowmine.sta.client import StaClient
from flowmine.sta.mqtt import StaMqtt, topic

from .conftest import FROST_URL, frost_reachable
from .test_sta_contract import START, _stream

pytestmark = pytest.mark.frost


@pytest.fixture
def frost() -> StaClient:
    if not frost_reachable():
        pytest.skip(f"FROST-Server not reachable at {FROST_URL}")
    return StaClient(FROST_URL)


def _broker() -> tuple[str, int]:
    host = urlsplit(FROST_URL).hostname or "localhost"
    return host, int(os.environ.get("FROST_MQTT_PORT", "1883"))


def test_topic_format() -> None:
    assert topic(12) == "v1.1/Datastreams(12)/Observations"


def test_subscribe_receives_http_posted_observation(frost: StaClient) -> None:
    ds = _stream(frost)
    got: list[dict] = []
    done = threading.Event()

    def handler(ds_id: int, body: dict) -> None:
        if ds_id == ds:
            got.append(body)
            done.set()

    m = StaMqtt(*_broker())
    m.subscribe([ds], handler)
    assert m.start()
    time.sleep(1.0)
    frost.post_observations([mapping.observation_body(START, 600, 12.5, datastream_id=ds)])
    assert done.wait(15.0)
    m.stop()
    assert got[0]["result"] == pytest.approx(12.5)


def test_publish_creates_observation(frost: StaClient) -> None:
    ds = _stream(frost)
    m = StaMqtt(*_broker())
    assert m.start()
    m.publish(ds, mapping.observation_body(START, 1200, 7.25))
    m.stop()
    deadline = time.monotonic() + 15
    obs: list[dict] = []
    while time.monotonic() < deadline and not obs:
        obs = list(frost.observations(ds))
        time.sleep(0.5)
    assert obs and obs[0]["result"] == pytest.approx(7.25)
