"""``flowmine twin run``: connect SensorThings to the hydraulic model.

* replay: read stored observations for a window, run the bridge as fast as it goes,
  publish the simulated, residual and virtual-sensor streams;
* live: subscribe to the boundary Datastreams over MQTT and advance one period each
  time every boundary stream has reported for it (or ``wait_steps`` periods later,
  in which case the step uses what is there and is marked degraded).
"""

from __future__ import annotations

import logging
import queue
import time
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.sta import mapping
from flowmine.sta.client import StaClient, observation_row
from flowmine.sta.mqtt import StaMqtt
from flowmine.sta.provision import StaMap, load_map
from flowmine.testbed import network
from flowmine.twin.bridge import Bridge, BridgeSensor, sensors_from_things
from flowmine.twin.runner import (
    StepBuilder,
    boundary_from,
    boundary_streams,
    inputs_from_frame,
    outputs_row,
    report_elements,
    run_twin,
)

log = logging.getLogger(__name__)


def station_things(client: StaClient) -> list[dict[str, Any]]:
    things = list(client.iterate("Things", {"$top": 1000}))
    return [t for t in things if (t.get("properties") or {}).get("sensors")]


def virtual_nodes_of(m: StaMap) -> list[str]:
    return sorted(n[len("VIRT-") :] for n in m.datastreams if n.startswith("VIRT-"))


def publish(
    client: StaClient, m: StaMap, start: datetime, sim: pd.DataFrame, res: pd.DataFrame
) -> int:
    bodies = []
    for frame in (sim, res):
        for col in frame.columns:
            ds = m.datastreams.get(col)
            if ds is None:
                continue
            col_v = frame[col].to_numpy(dtype=float)
            for t, v in zip(frame.index.to_numpy(dtype=np.int64), col_v, strict=True):
                if np.isfinite(v):
                    body = mapping.observation_body(
                        start, int(t), round(float(v), 4), datastream_id=ds
                    )
                    bodies.append(body)
    return client.post_observations(bodies)


def replay(client: StaClient, cfg: Config, start: datetime, t0: int, t1: int) -> int:
    m = load_map(client)
    sensors = sensors_from_things(station_things(client))
    frame = client.load_frame(
        {s.sensor_id: m.datastreams[s.sensor_id] for s in sensors}, start, t0, t1
    )
    measured = frame.pivot_table(index="time_s", columns="stream", values="value", aggfunc="last")
    wn = network.load_network(cfg.network.inp)
    run = run_twin(cfg, wn, sensors, frame, t0, t1, virtual_nodes_of(m), measured)
    n = publish(client, m, start, run.simulated, run.residual)
    log.info(
        "twin replay %d..%d: %d steps, %d degraded, %d resyncs, %d observations posted",
        t0,
        t1,
        len(run.simulated),
        int(run.degraded.sum()),
        run.resyncs,
        n,
    )
    return n


class LiveTwin:
    """Incremental bridge fed by observations as they arrive."""

    def __init__(
        self, cfg: Config, sensors: list[BridgeSensor], virtual: list[str], t0: int, duration_s: int
    ) -> None:
        self.cfg = cfg
        self.sensors = sensors
        self.virtual = virtual
        wn = network.load_network(cfg.network.inp)
        self.b = boundary_from(wn, sensors)
        self.streams = boundary_streams(self.b)
        self.builder = StepBuilder(self.b)
        nodes, links = report_elements(sensors, virtual)
        self.step_s = cfg.network.hydraulic_step_s
        self.bridge = Bridge(
            cfg.network.inp, self.step_s, duration_s, sorted(self.b.pumps), nodes, links, cfg
        )
        self.t = t0
        self.rows: list[dict[str, Any]] = []
        self.seen: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        self.seen.append(row)

    def ready(self, wait_steps: int) -> bool:
        if not self.seen:
            return False
        latest = max(r["time_s"] for r in self.seen)
        have = {r["stream"] for r in self.seen if r["time_s"] == self.t}
        return set(self.streams) <= have or latest >= self.t + wait_steps * self.step_s

    def step(self) -> tuple[int, dict[str, float], dict[str, float]]:
        """Advance one period; returns (t, simulated row, measured values at t)."""
        measured = {r["stream"]: float(r["value"]) for r in self.seen if r["time_s"] == self.t}
        frame = pd.DataFrame(self.seen)
        inp = inputs_from_frame(frame, self.streams, np.array([self.t]))
        out = self.bridge.step(self.builder.at(inp, self.t))
        row = outputs_row(out, self.sensors, self.virtual)
        t = self.t
        self.seen = [r for r in self.seen if r["time_s"] > t]
        self.t += self.step_s
        return t, row, measured


def live(
    client: StaClient,
    cfg: Config,
    start: datetime,
    host: str,
    port: int,
    t0: int,
    duration_s: int,
    wait_steps: int = 3,
    idle_timeout_s: float = 60.0,
) -> int:
    """Run until no observation arrives for ``idle_timeout_s`` seconds."""
    m = load_map(client)
    sensors = sensors_from_things(station_things(client))
    twin = LiveTwin(cfg, sensors, virtual_nodes_of(m), t0, duration_s)
    names = {m.datastreams[s]: s for s in twin.streams}
    inbox: queue.Queue[dict[str, Any]] = queue.Queue()
    mq = StaMqtt(host, port, client_id="flowmine-twin")
    mq.subscribe(list(names), lambda ds, body: inbox.put(observation_row(names[ds], body, start)))
    if not mq.start():
        raise RuntimeError(f"MQTT broker {host}:{port} not reachable")
    posted = 0
    last_msg = time.monotonic()
    try:
        while time.monotonic() - last_msg < idle_timeout_s:
            try:
                twin.add(inbox.get(timeout=1.0))
                last_msg = time.monotonic()
            except queue.Empty:
                continue
            while twin.ready(wait_steps):
                t, row, measured = twin.step()
                sim = pd.DataFrame([row], index=[t])
                res = pd.DataFrame(
                    {
                        f"RES-{k}": [measured[k] - row[f"SIM-{k}"]]
                        for k in measured
                        if f"SIM-{k}" in row
                    },
                    index=[t],
                )
                posted += publish(client, m, start, sim, res)
    finally:
        mq.stop()
        twin.bridge.close()
    return posted
