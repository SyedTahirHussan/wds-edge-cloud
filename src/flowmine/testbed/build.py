"""Generate, save and load a complete labelled testbed for one seed.

Output directory layout (Parquet, tidy, one row per sample):

    truth.parquet        sensor_id, time_s, value              noise-free sensor values
    observed.parquet     sensor_id, time_s, value              noisy, quantised, faulty (NaN = none)
    labels.parquet       sensor_id, time_s, fault_type, fault_id, event_id
    faults.parquet       one row per injected fault interval
    events.parquet       one row per process event (leak, surge, operations)
    nodes.parquet        time_s x junction: true pressure (m); nodes_cl.parquet: chlorine (mg/L)
    demand.parquet       time_s x junction: true demand (L/s)
    lab.parquet  maintenance.parquet  weather.parquet
    sensors.json  stations.json  manifest.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import wntr

from flowmine import __version__
from flowmine.config import Config
from flowmine.testbed import faults as faults_mod
from flowmine.testbed import network, simulate, sources
from flowmine.testbed.sensors import Sensor, StationSpec, layout

log = logging.getLogger(__name__)
DAY = 86400
STREAMS = {
    "demand": 1,
    "events": 2,
    "faults": 3,
    "noise": 4,
    "lab": 5,
    "maint": 6,
    "fault_noise": 7,
}


def rng_for(seed: int, stream: str) -> np.random.Generator:
    """Independent generator per component, so changing one leaves the others fixed."""
    return np.random.default_rng([seed, STREAMS[stream]])


def periods_of(cfg: Config) -> dict[str, tuple[int, int]]:
    n = cfg.network
    a = n.train_days * DAY
    b = a + n.calib_days * DAY
    return {"train": (0, a), "calib": (a, b), "test": (b, b + n.test_days * DAY)}


@dataclass
class Testbed:
    cfg: Config
    seed: int
    sensors: list[Sensor]
    stations: list[StationSpec]
    truth: pd.DataFrame
    observed: pd.DataFrame
    labels: pd.DataFrame
    faults: pd.DataFrame
    events: pd.DataFrame
    nodes: pd.DataFrame
    nodes_cl: pd.DataFrame
    demand: pd.DataFrame
    lab: pd.DataFrame
    maintenance: pd.DataFrame
    weather: pd.DataFrame
    base_demand: dict[str, float]
    manifest: dict[str, object] = field(default_factory=dict)

    @property
    def periods(self) -> dict[str, tuple[int, int]]:
        return periods_of(self.cfg)

    def sensor(self, sid: str) -> Sensor:
        return next(s for s in self.sensors if s.sensor_id == sid)

    def wide(self, frame: str = "observed") -> pd.DataFrame:
        """time_s x sensor_id table of the chosen frame (NaN where no sample)."""
        df: pd.DataFrame = getattr(self, frame)
        return df.pivot(index="time_s", columns="sensor_id", values="value").sort_index()


def _sensor_truth(res: wntr.sim.SimulationResults, s: Sensor) -> pd.Series:
    if s.kind == "flow":
        return res.link["flowrate"][s.element] * 1000.0
    if s.kind == "chlorine":
        return res.node["quality"][s.element] * 1000.0
    return res.node["pressure"][s.element]


def _sample(series: pd.Series, period: int, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    times = np.arange(0, n_days * DAY, period)
    return times, series.reindex(times).to_numpy(dtype=float)


def generate(cfg: Config, seed: int) -> Testbed:
    n_days = cfg.total_days
    per = periods_of(cfg)
    labelled = {k: v for k, v in per.items() if k != "train"}
    weeks = {k: (v[1] - v[0]) / (7 * DAY) for k, v in labelled.items()}

    wn, base_demand, temp, _mult = simulate.build_network(cfg, rng_for(seed, "demand"), n_days)
    g = network.graph(wn)
    sens, stations = layout(wn, g, cfg)
    sensor_nodes = [s.element for s in sens if s.kind in ("pressure", "chlorine")]
    events = simulate.plan_events(wn, rng_for(seed, "events"), cfg, labelled, sensor_nodes)

    base_res = simulate.run(wn)
    n_steps = n_days * DAY // cfg.network.hydraulic_step_s + 1
    simulate.apply_events(wn, events, base_res.node["pressure"], cfg, n_steps)
    res = simulate.run(wn) if events else base_res

    truth_rows, obs_rows, lab_rows = [], [], []
    spans: dict[str, float] = {}
    series: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    noise_rng = rng_for(seed, "noise")
    train_end = per["train"][1]
    for s in sens:
        c = cfg.sensors[s.kind]
        times, tv = _sample(_sensor_truth(res, s), s.period_s, n_days)
        _, bv = _sample(_sensor_truth(base_res, s), s.period_s, n_days)
        sd = c.noise_sd + c.noise_rel * np.abs(tv)
        ov = (
            np.round((tv + noise_rng.normal(0.0, 1.0, size=len(tv)) * sd) / c.resolution)
            * c.resolution
        )
        tr = tv[times < train_end]
        spans[s.sensor_id] = (
            float(np.quantile(tr, 0.95) - np.quantile(tr, 0.05)) or c.resolution * 10
        )
        series[s.sensor_id] = (times, tv, ov)
        ev_id = np.full(len(times), -1)
        thr = 3 * sd + c.resolution
        for e in events:
            tail = e.end_s if e.event_type == "leak" else e.end_s + 6 * 3600
            win = (times >= e.start_s) & (times <= tail) & (np.abs(tv - bv) > thr)
            ev_id[win & (ev_id < 0)] = e.event_id
        lab_rows.append(
            pd.DataFrame({"sensor_id": s.sensor_id, "time_s": times, "event_id": ev_id})
        )

    sdicts = [s.to_dict() for s in sens]
    fault_events = faults_mod.schedule(
        rng_for(seed, "faults"), sdicts, spans, labelled, cfg.faults, weeks
    )
    frng = rng_for(seed, "fault_noise")
    fault_type: dict[str, np.ndarray] = {
        sid: np.full(len(v[0]), "", dtype=object) for sid, v in series.items()
    }
    fault_id: dict[str, np.ndarray] = {sid: np.full(len(v[0]), -1) for sid, v in series.items()}
    for fe in fault_events:
        faults_mod.inject(series[fe.sensor_id][2], fe, frng)
        sl = slice(fe.start_idx, fe.start_idx + fe.n)
        fault_type[fe.sensor_id][sl] = fe.fault_type
        fault_id[fe.sensor_id][sl] = fe.fault_id

    for s in sens:
        times, tv, ov = series[s.sensor_id]
        truth_rows.append(pd.DataFrame({"sensor_id": s.sensor_id, "time_s": times, "value": tv}))
        obs_rows.append(pd.DataFrame({"sensor_id": s.sensor_id, "time_s": times, "value": ov}))
    labels = pd.concat(lab_rows, ignore_index=True)
    labels["fault_type"] = np.concatenate([fault_type[s.sensor_id] for s in sens])
    labels["fault_id"] = np.concatenate([fault_id[s.sensor_id] for s in sens])
    labels = labels[["sensor_id", "time_s", "fault_type", "fault_id", "event_id"]]

    junctions = sorted(network.load_network(cfg.network.inp).junction_name_list)
    nodes = res.node["pressure"][junctions].astype(float)
    nodes_cl = (res.node["quality"][junctions] * 1000.0).astype(float)
    dem_cols = [c for c in res.node["demand"].columns if c in junctions or c.startswith("LEAK-")]
    dem = (res.node["demand"][dem_cols] * 1000.0).astype(float)
    for frame in (nodes, nodes_cl, dem):
        frame.index.name = "time_s"

    cl_nodes = {s.element: s.sensor_id for s in sens if s.kind == "chlorine"}
    lab = sources.lab_samples(rng_for(seed, "lab"), nodes_cl, cl_nodes, n_days, cfg)
    maint = sources.maintenance_log(
        rng_for(seed, "maint"),
        fault_events,
        events,
        {s.sensor_id: s.period_s for s in sens},
        [s.sensor_id for s in sens],
        labelled,
    )

    manifest = {
        "seed": seed,
        "config": cfg.name,
        "config_hash": cfg.digest(),
        "inp": Path(cfg.network.inp).name if cfg.network.inp else "Net3.inp (WNTR library)",
        "start": cfg.network.start,
        "periods": {k: list(v) for k, v in per.items()},
        "versions": {
            "flowmine": __version__,
            "wntr": wntr.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "epanet": "2.2 (bundled with WNTR)",
        },
        "n_sensors": len(sens),
        "n_stations": len(stations),
        "n_faults": len(fault_events),
        "n_events": len(events),
    }
    return Testbed(
        cfg,
        seed,
        sens,
        stations,
        pd.concat(truth_rows, ignore_index=True),
        pd.concat(obs_rows, ignore_index=True),
        labels,
        pd.DataFrame([f.to_dict() for f in fault_events]),
        pd.DataFrame([e.to_dict() for e in events]),
        nodes,
        nodes_cl,
        dem,
        lab,
        maint,
        sources.weather_frame(temp),
        base_demand,
        manifest,
    )


FRAMES = ("truth", "observed", "labels", "faults", "events", "lab", "maintenance", "weather")
WIDE = ("nodes", "nodes_cl", "demand")


def save(tb: Testbed, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in FRAMES:
        getattr(tb, name).to_parquet(out / f"{name}.parquet", index=False)
    for name in WIDE:
        getattr(tb, name).to_parquet(out / f"{name}.parquet")
    (out / "sensors.json").write_text(json.dumps([s.to_dict() for s in tb.sensors], indent=1))
    (out / "stations.json").write_text(json.dumps([s.to_dict() for s in tb.stations], indent=1))
    (out / "base_demand.json").write_text(json.dumps(tb.base_demand, indent=1, sort_keys=True))
    (out / "manifest.json").write_text(json.dumps(tb.manifest, indent=1, sort_keys=True))


def load(cfg: Config, out: Path) -> Testbed:
    manifest = json.loads((out / "manifest.json").read_text())
    sens = [Sensor(**d) for d in json.loads((out / "sensors.json").read_text())]
    stations = []
    for d in json.loads((out / "stations.json").read_text()):
        d["sensors"] = tuple(d["sensors"])
        stations.append(StationSpec(**d))
    frames = {name: pd.read_parquet(out / f"{name}.parquet") for name in FRAMES}
    wide = {name: pd.read_parquet(out / f"{name}.parquet") for name in WIDE}
    base = json.loads((out / "base_demand.json").read_text())
    return Testbed(
        cfg,
        int(manifest["seed"]),
        sens,
        stations,
        **frames,
        **wide,
        base_demand=base,
        manifest=manifest,
    )


def ensure(cfg: Config, seed: int, root: Path = Path("data")) -> Testbed:
    """Load the testbed for (config, seed) from ``root`` or generate and cache it."""
    out = root / f"{cfg.name}-{cfg.digest()}" / f"seed{seed}"
    if (out / "manifest.json").exists():
        return load(cfg, out)
    log.info("generating testbed %s seed %d", cfg.name, seed)
    tb = generate(cfg, seed)
    save(tb, out)
    return tb
