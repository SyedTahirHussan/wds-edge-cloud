"""``flowmine central run``: the cloud analytics loop used by the live demo.

Every ``interval_s`` seconds it reads the latest two hours of hydraulic observations
from SensorThings, runs the spatial model and the triage, and posts what it finds to
the EVENTS Datastream: ``sensor_fault`` (one sensor inconsistent with the others) or
``process_event`` (several sensors changing coherently). In the demo the spatial
model is trained from the cached testbed training weeks rather than from
SensorThings, so that start-up does not need six weeks of history uploaded first;
the experiments train it the same way.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import numpy as np

from flowmine.central import homogenise, spatial, triage
from flowmine.config import Config
from flowmine.edge.core import quality
from flowmine.sta import mapping
from flowmine.sta.client import StaClient
from flowmine.sta.provision import load_map
from flowmine.testbed import build

log = logging.getLogger(__name__)
WINDOW_S = 2 * 3600


def trained_model(cfg: Config, tb: build.Testbed, streams: list[str]) -> spatial.SpatialModel:
    per = {s.sensor_id: s.period_s for s in tb.sensors}
    c = cfg.central
    tr = homogenise.align(
        homogenise.raw_frame(tb.observed, *tb.periods["train"]),
        streams,
        *tb.periods["train"],
        300,
        per,
    )
    m = spatial.SpatialModel(streams, c.ridge_lags, c.ridge_alpha, c.pca_variance).fit(tr.values)
    cal = homogenise.align(
        homogenise.raw_frame(tb.observed, *tb.periods["calib"]),
        streams,
        *tb.periods["calib"],
        300,
        per,
    )
    m.calibrate(cal.values, cal.suspect, ~cal.suspect, c.alarm_quantile)
    return m


def analyse(
    model: spatial.SpatialModel, g: homogenise.Grid, window: int
) -> list[dict[str, object]]:
    r = model.apply(g.values, g.suspect)
    flags = model.flags(r)
    temporal = (g.quality & (quality.SHIFT | quality.REJECTING)) != 0
    tri = triage.triage(temporal, flags, window)
    events: list[dict[str, object]] = []
    for w0 in range(0, len(g.times), window):
        sl = slice(w0, w0 + window)
        faulty = [g.streams[j] for j in np.nonzero(tri.fault[sl].any(axis=0))[0]]
        if faulty:
            events.append({"type": "sensor_fault", "sensors": faulty, "t0": int(g.times[w0])})
        if tri.event[sl].any():
            part = [g.streams[j] for j in np.nonzero(tri.event_sensors[sl].any(axis=0))[0]]
            events.append({"type": "process_event", "sensors": part, "t0": int(g.times[w0])})
    return events


def run(
    client: StaClient,
    cfg: Config,
    seed: int,
    start: datetime,
    interval_s: float,
    idle_rounds: int = 10,
) -> int:
    tb = build.ensure(cfg, seed)
    streams = [s.sensor_id for s in tb.sensors if s.kind != "chlorine"]
    model = trained_model(cfg, tb, streams)
    m = load_map(client)
    per = {s.sensor_id: s.period_s for s in tb.sensors}
    posted: set[tuple[str, int]] = set()
    idle = 0
    latest_seen = -1
    while idle < idle_rounds:
        time.sleep(interval_s)
        last = client.get(
            f"Datastreams({m.datastreams[streams[0]]})/Observations",
            {"$top": 1, "$orderby": "phenomenonTime desc", "$select": "phenomenonTime"},
        )
        if not last["value"]:
            idle += 1
            continue
        t_latest = mapping.seconds(start, last["value"][0]["phenomenonTime"])
        if t_latest == latest_seen:
            idle += 1
            continue
        idle, latest_seen = 0, t_latest
        # Start on a whole triage window so the windows, and the (type, t0) keys used to
        # avoid re-posting an event, stay the same from one round to the next.
        span = cfg.central.triage_window * 300
        t0 = max(0, (t_latest - WINDOW_S) // span * span)
        frame = client.load_frame(m.measured(streams), start, t0, t_latest + 1)
        g = homogenise.align(frame, streams, t0, t_latest + 1, 300, per)
        bodies = []
        for ev in analyse(model, g, cfg.central.triage_window):
            key = (str(ev["type"]), int(str(ev["t0"])))
            if key in posted:
                continue
            posted.add(key)
            bodies.append(
                mapping.observation_body(start, key[1], ev, datastream_id=m.datastreams["EVENTS"])
            )
        if bodies:
            client.post_observations(bodies)
            log.info("posted %d events up to t=%d", len(bodies), t_latest)
    return len(posted)
