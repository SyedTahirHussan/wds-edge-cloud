"""``flowmine twin run``: connect SensorThings to the hydraulic model.

* replay: read stored observations for a window, run the bridge as fast as it goes,
  publish the simulated, residual and virtual-sensor streams;
* live: subscribe to the measured Datastreams over MQTT and advance one period each
  time every boundary stream due at that period has reported for it or moved past it
  (see ``LiveTwin.ready``). The other measured streams give the residuals.
"""

from __future__ import annotations

import logging
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flowmine.central import leak
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
        self.period = {s.sensor_id: s.period_s for s in sensors}
        self.builder = StepBuilder(self.b)
        nodes, links = report_elements(sensors, virtual)
        self.step_s = cfg.network.hydraulic_step_s
        self.bridge = Bridge(
            cfg.network.inp,
            self.step_s,
            duration_s,
            sorted(self.b.pumps),
            nodes,
            links,
            cfg,
            level_tol_m=cfg.central.twin_level_tol_m,
        )
        self.t = t0
        self.seen: list[dict[str, Any]] = []
        self.latest: dict[str, int] = {}
        self.waiting_since: float | None = None

    def add(self, row: dict[str, Any]) -> None:
        self.seen.append(row)
        s = str(row["stream"])
        self.latest[s] = max(self.latest.get(s, -1), int(row["time_s"]))

    def ready(self, timeout_s: float) -> bool:
        """True when every measured stream due at the current period (boundary inputs and
        the streams that get residuals) has either reported for it or already reported a
        later time (its sample is missing). MQTT delivers the observations of one batch
        in no fixed order and stations run at different speeds, so this is decided per
        stream. If a due stream
        has not moved for ``timeout_s`` seconds of wall-clock time while data for later
        periods exist, the step goes ahead without it."""
        due = [s for s in self.period if self.t % self.period[s] == 0]
        have = {r["stream"] for r in self.seen if r["time_s"] == self.t}
        if all(s in have or self.latest.get(s, -1) > self.t for s in due):
            self.waiting_since = None
            return True
        if not self.latest or max(self.latest.values()) <= self.t:
            return False
        now = time.monotonic()
        if self.waiting_since is None:
            self.waiting_since = now
        if now - self.waiting_since > timeout_s:
            self.waiting_since = None
            return True
        return False

    def pending(self) -> bool:
        """True while observations for the current or a later period are held."""
        return any(r["time_s"] >= self.t for r in self.seen)

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


def leak_watcher(cfg: Config, sensors: list[BridgeSensor]) -> leak.LeakWatcher:
    """Watcher over the pressure sensors; the matrix uses the base demands (multiplier 1)."""
    press = {s.sensor_id: s.element for s in sensors if s.kind == "pressure"}
    sens = leak.ensure(
        cfg,
        sorted(set(press.values())),
        np.ones(leak.STEPS_PER_DAY),
        Path("data") / "twin",
        cfg.experiments.leak_nominal_l_s,
    )
    return leak.LeakWatcher(sens, press)


def live(
    client: StaClient,
    cfg: Config,
    start: datetime,
    host: str,
    port: int,
    t0: int,
    duration_s: int,
    timeout_s: float = 30.0,
    idle_timeout_s: float = 60.0,
) -> int:
    """Run until no observation arrives for ``idle_timeout_s`` seconds."""
    m = load_map(client)
    sensors = sensors_from_things(station_things(client))
    twin = LiveTwin(cfg, sensors, virtual_nodes_of(m), t0, duration_s)
    names = {m.datastreams[s.sensor_id]: s.sensor_id for s in sensors}
    inbox: queue.Queue[dict[str, Any]] = queue.Queue()
    mq = StaMqtt(host, port, client_id="flowmine-twin")
    mq.subscribe(list(names), lambda ds, body: inbox.put(observation_row(names[ds], body, start)))
    if not mq.start():
        raise RuntimeError(f"MQTT broker {host}:{port} not reachable")
    posted = 0
    watcher = leak_watcher(cfg, sensors) if "LEAK-CANDIDATES" in m.datastreams else None

    def advance(drain: bool) -> None:
        nonlocal posted
        while twin.ready(timeout_s) or (drain and twin.pending()):
            t, row, measured = twin.step()
            if watcher is not None:
                res_p = {k: measured[k] - row[f"SIM-{k}"] for k in measured if f"SIM-{k}" in row}
                hit = watcher.add(t, res_p)
                if hit is not None:
                    body = mapping.leak_candidates_body(
                        start,
                        t,
                        hit[0],
                        hit[1],
                        watcher.window_s,
                        watcher.method,
                        datastream_id=m.datastreams["LEAK-CANDIDATES"],
                    )
                    posted += client.post_observations([body])
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

    last_msg = time.monotonic()
    try:
        while time.monotonic() - last_msg < idle_timeout_s:
            try:
                twin.add(inbox.get(timeout=1.0))
                last_msg = time.monotonic()
            except queue.Empty:
                pass
            # Also checked while idle, so the wall-clock fallback fires when traffic stops.
            advance(drain=False)
        # Idle: the stations are done. Step through what is still held.
        while not inbox.empty():
            twin.add(inbox.get_nowait())
        advance(drain=True)
    finally:
        mq.stop()
        twin.bridge.close()
    return posted
