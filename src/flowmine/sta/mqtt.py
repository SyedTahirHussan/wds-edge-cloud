"""SensorThings MQTT extension: publish and subscribe to Datastream Observation topics.

Topics follow SensorThings v1.1, which prefixes them with the version:
``v1.1/Datastreams(<id>)/Observations``. Publishing an Observation body (without the
Datastream link) on that topic creates it; subscribing delivers every new
Observation of the Datastream. FROST-Server ships a broker (Moquette) on port 1883.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)
TOPIC = re.compile(r"v1\.1/Datastreams\((\d+)\)/Observations")


def topic(ds_id: int) -> str:
    return f"v1.1/Datastreams({ds_id})/Observations"


class StaMqtt:
    def __init__(self, host: str = "localhost", port: int = 1883, client_id: str = "") -> None:
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.connected = threading.Event()
        self.handlers: list[Callable[[int, dict[str, Any]], None]] = []
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.host, self.port = host, port
        self.topics: list[str] = []

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason: Any, properties: Any
    ) -> None:
        for t in self.topics:
            client.subscribe(t, qos=1)
        self.connected.set()

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        m = TOPIC.fullmatch(msg.topic)
        if m is None:
            return
        try:
            body = json.loads(msg.payload)
        except ValueError:
            log.warning("non-JSON payload on %s", msg.topic)
            return
        for h in self.handlers:
            h(int(m.group(1)), body)

    def start(self, timeout: float = 10.0) -> bool:
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        return self.connected.wait(timeout)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def subscribe(self, ds_ids: list[int], handler: Callable[[int, dict[str, Any]], None]) -> None:
        self.handlers.append(handler)
        for ds in ds_ids:
            t = topic(ds)
            self.topics.append(t)
            if self.connected.is_set():
                self.client.subscribe(t, qos=1)

    def publish(self, ds_id: int, body: dict[str, Any]) -> None:
        body = {k: v for k, v in body.items() if k != "Datastream"}
        info = self.client.publish(topic(ds_id), json.dumps(body), qos=1)
        info.wait_for_publish(timeout=10.0)
