"""Everything one seed's experiments share, computed once.

The cloud's view is built the way the deployed system builds it: edge nodes run on
the observed samples, their uplink frames are decoded by the gateway into
SensorThings Observation bodies, and those bodies are turned into the same tidy frame
that ``StaClient.load_frame`` returns. The HTTP round trip is the only step left out
(the contract tests cover it); with five seeds and eight weeks it would dominate
the run time.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from flowmine.central import chlorine, homogenise, spatial
from flowmine.config import Config
from flowmine.edge import calibrate, replay
from flowmine.edge.core import quality
from flowmine.edge.models import SensorModels, fit_sensor
from flowmine.edge.node import MemoryLink
from flowmine.sta.client import frame_from_bodies
from flowmine.sta.gateway import Gateway
from flowmine.testbed import build

log = logging.getLogger(__name__)
GRID = 300


@dataclass
class EdgeRun:
    records: pd.DataFrame  # the station's own view: sensor_id, time_s, raw, value, q, score
    frame: pd.DataFrame  # the cloud's view after the gateway (tidy SensorThings frame)
    nodes: dict[str, object] = field(default_factory=dict)


@dataclass
class SeedContext:
    cfg: Config
    tb: build.Testbed
    bundles: dict[str, SensorModels]
    calibrated: dict[str, dict[str, SensorModels]]
    edge: dict[str, EdgeRun]
    hydraulic: list[str]
    chlorine_ids: list[str]
    spatial_raw: spatial.SpatialModel
    spatial_val: spatial.SpatialModel
    lab_base: dict[str, tuple[float, float]]

    @property
    def seed(self) -> int:
        return self.tb.seed

    @property
    def periods(self) -> dict[str, tuple[int, int]]:
        return self.tb.periods

    def grid(self, frame: pd.DataFrame, period: str) -> homogenise.Grid:
        t0, t1 = self.periods[period]
        per = {s.sensor_id: s.period_s for s in self.tb.sensors}
        return homogenise.align(frame, self.hydraulic, t0, t1, GRID, per)

    def raw(self, period: str) -> pd.DataFrame:
        return homogenise.raw_frame(self.tb.observed, *self.periods[period])


def run_edge(
    tb: build.Testbed,
    bundles: dict[str, SensorModels],
    forecaster: str,
    policy: str,
    t0: int,
    t1: int,
) -> EdgeRun:
    rows, bodies = [], []
    start = replay.start_time(tb.cfg)
    ds = replay.datastream_ids(tb)
    gw = Gateway(tb.stations, tb.sensors, ds, start, summary_s=tb.cfg.edge.summary_s)
    for spec in tb.stations:
        link = MemoryLink()
        node = replay.make_node(tb, spec, bundles, policy, forecaster, link=link)
        replay.run_station(node, tb, t0, t1)
        rows.extend(node.records)
        for _, fr in link.frames:
            bodies.extend(gw.decode(fr))
    rec = pd.DataFrame(rows, columns=["sensor_id", "time_s", "raw", "value", "q", "score"])
    names = {v: k for k, v in ds.items()}
    return EdgeRun(rec, frame_from_bodies(bodies, names, start))


def rules_only(b: dict[str, SensorModels]) -> dict[str, SensorModels]:
    """Same pipelines with the residual test switched off (huge threshold)."""
    out = {}
    for sid, m in b.items():
        nb = copy.copy(m)
        nb.meta = {**m.meta, "k": 1e12}
        out[sid] = nb
    return out


def clean_mask(tb: build.Testbed, g: homogenise.Grid) -> np.ndarray:
    lab = tb.labels
    sub = lab[lab.sensor_id.isin(g.streams) & lab.time_s.isin(g.times)]
    ok = sub[(sub.fault_type == "") & (sub.event_id < 0)]
    mask = np.zeros_like(g.suspect)
    col = {s: j for j, s in enumerate(g.streams)}
    rows = ((ok.time_s.to_numpy() - g.times[0]) // GRID).astype(int)
    mask[rows, [col[s] for s in ok.sensor_id]] = True
    return mask


def build_context(cfg: Config, seed: int) -> SeedContext:
    tb = build.ensure(cfg, seed)
    bundles = {s.sensor_id: fit_sensor(tb, s.sensor_id, cfg) for s in tb.sensors}
    calibrated = {
        "hw": calibrate.calibrate(tb, bundles, cfg, "hw"),
        "mlp": calibrate.calibrate(tb, bundles, cfg, "mlp"),
    }
    t0, t1 = tb.periods["calib"][0], tb.periods["test"][1]
    edge = {
        "edge_hw": run_edge(tb, calibrated["hw"], "hw", "validated", t0, t1),
        "edge_mlp": run_edge(tb, calibrated["mlp"], "mlp", "validated", t0, t1),
        "rules": run_edge(tb, rules_only(bundles), "hw", "validated", t0, t1),
    }
    hyd = [s.sensor_id for s in tb.sensors if s.kind != "chlorine"]
    cl = [s.sensor_id for s in tb.sensors if s.kind == "chlorine"]
    c = cfg.central
    per = {s.sensor_id: s.period_s for s in tb.sensors}
    train = homogenise.align(
        homogenise.raw_frame(tb.observed, *tb.periods["train"]),
        hyd,
        *tb.periods["train"],
        GRID,
        per,
    )
    sp_raw = spatial.SpatialModel(hyd, c.ridge_lags, c.ridge_alpha, c.pca_variance).fit(
        train.values
    )
    sp_val = copy.deepcopy(sp_raw)
    ctx = SeedContext(cfg, tb, bundles, calibrated, edge, hyd, cl, sp_raw, sp_val, {})
    g_raw = ctx.grid(ctx.raw("calib"), "calib")
    sp_raw.calibrate(g_raw.values, g_raw.suspect, clean_mask(tb, g_raw), c.alarm_quantile)
    g_val = ctx.grid(edge["edge_hw"].frame, "calib")
    sp_val.calibrate(g_val.values, g_val.suspect, clean_mask(tb, g_val), c.alarm_quantile)
    train_obs = homogenise.raw_frame(tb.observed, *tb.periods["train"])
    lab_train = tb.lab[tb.lab.phenomenon_s < tb.periods["train"][1]]
    ctx.lab_base = chlorine.baseline(chlorine.differences(lab_train, train_obs), 0.01)
    log.info("seed %d context ready", seed)
    return ctx


def drift_flags(ctx: SeedContext, frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Chlorine samples the laboratory CUSUM marks as drifted: (sensor_id, time_s) rows.

    A sample is flagged from the alarm's result time until the next recalibration or
    replacement record of that analyser.
    """
    t0, t1 = ctx.periods[period]
    tb = ctx.tb
    lab = tb.lab[(tb.lab.phenomenon_s >= ctx.periods["calib"][0]) & (tb.lab.result_s < t1)]
    an = frame[frame.stream.isin(ctx.chlorine_ids)]
    diffs = chlorine.differences(lab, an)
    c = ctx.cfg.central
    alarms = chlorine.cusum(diffs, ctx.lab_base, c.cusum_k, c.cusum_h, tb.maintenance)
    rows = []
    for a in alarms:
        m = tb.maintenance
        resets = m[
            (m.asset == a.sensor_id) & m.type.isin(chlorine.RESET_TYPES) & (m.time_s > a.sample_s)
        ].time_s
        end = int(resets.min()) if len(resets) else t1
        per = tb.sensor(a.sensor_id).period_s
        for t in range(max(a.alarm_s, t0) // per * per, min(end, t1), per):
            if t >= t0:
                rows.append((a.sensor_id, t))
    return pd.DataFrame(rows, columns=["sensor_id", "time_s"]).drop_duplicates()


def edge_flags(run: EdgeRun) -> pd.DataFrame:
    """Per record: rejected by a test (or cross-check, or missing) and level shift."""
    r = run.records
    return pd.DataFrame(
        {
            "sensor_id": r.sensor_id,
            "time_s": r.time_s,
            "reject": (r.q & (quality.REJECTING | quality.CROSSCHECK | quality.MISSING)) != 0,
            "shift": (r.q & quality.SHIFT) != 0,
            "reconstructed": (r.q & quality.RECONSTRUCTED) != 0,
        }
    )


def routine_mask(ctx: SeedContext, g: homogenise.Grid) -> np.ndarray:
    """Grid steps where a source meter jumps by more than ``routine_step_frac`` of its
    training range (a pump starting or stopping), widened by one step each side."""
    wn_sources = [
        s.sensor_id for s in ctx.tb.sensors if s.kind == "flow" and s.station.startswith("PS-")
    ]
    frac = ctx.cfg.central.routine_step_frac
    mask = np.zeros(len(g.times), dtype=bool)
    train = ctx.tb.wide("observed")
    for sid in wn_sources:
        tr = train.loc[train.index < ctx.periods["train"][1], sid]
        span = float(tr.quantile(0.95) - tr.quantile(0.05)) or 1.0
        x = pd.Series(g.values[:, g.col(sid)]).ffill().to_numpy()
        step = np.abs(np.diff(x, prepend=x[0])) > frac * span
        mask |= step | np.roll(step, 1) | np.roll(step, -1)
    return mask
