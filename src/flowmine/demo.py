"""``flowmine demo``: the whole system as separate processes against FROST-Server.

1. provision every entity; fit, calibrate and register the edge models;
2. post the laboratory results, maintenance records and weather of the window;
3. start the gateway, the dashboard, the live twin (MQTT), the central analytics and
   one process per station;
4. stations replay the window (default: the first two test days, faults included)
   at ``speed`` times real time; wait for them, then for the twin and the analytics.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from flowmine.config import Config
from flowmine.edge import calibrate, replay
from flowmine.edge.models import fit_sensor
from flowmine.edge.node import model_package
from flowmine.hybrid.registry import ModelRegistry
from flowmine.sta import mapping
from flowmine.sta.client import StaClient
from flowmine.sta.provision import StaMap, provision
from flowmine.testbed import build, network

log = logging.getLogger(__name__)


def virtual_nodes(cfg: Config, tb: build.Testbed, seed: int) -> list[str]:
    wn = network.load_network(cfg.network.inp)
    used = {s.element for s in tb.sensors}
    cands = sorted(j for j in wn.junction_name_list if j not in used)
    rng = np.random.default_rng([seed, 61])
    n = min(cfg.experiments.unmonitored_nodes, len(cands))
    return sorted(rng.choice(cands, size=n, replace=False).tolist())


def register_models(cfg: Config, tb: build.Testbed, root: Path) -> ModelRegistry:
    reg = ModelRegistry(root / f"{cfg.name}-{cfg.digest()}-seed{tb.seed}")
    if all(reg.active(s.sensor_id) for s in tb.sensors):
        return reg
    bundles = {s.sensor_id: fit_sensor(tb, s.sensor_id, cfg) for s in tb.sensors}
    bundles = calibrate.calibrate(tb, bundles, cfg, "hw")
    window = (0, tb.periods["train"][1])
    for sid, b in bundles.items():
        blob, _ = model_package(sid, 1, b.hw_copy(), meta=b.meta)
        e = reg.register(sid, "hw", blob, window, {"k": float(str(b.meta["k"]))})
        reg.promote(sid, e.version)
    return reg


def post_sources(client: StaClient, m: StaMap, tb: build.Testbed, t0: int, t1: int) -> int:
    start = replay.start_time(tb.cfg)
    bodies = []
    lab = tb.lab[(tb.lab.phenomenon_s >= t0 - 2 * 86400) & (tb.lab.result_s < t1)]
    for _, r in lab.iterrows():
        bodies.append(
            mapping.observation_body(
                start,
                int(r.phenomenon_s),
                float(r.value),
                result_t_s=int(r.result_s),
                datastream_id=m.datastreams[f"LAB-{r.sensor_id}"],
            )
        )
    station_of = {s.sensor_id: s.station for s in tb.sensors}
    station_of |= {s.element: s.station for s in tb.sensors}
    mt = tb.maintenance[(tb.maintenance.time_s >= t0 - 86400) & (tb.maintenance.time_s < t1)]
    for _, r in mt.iterrows():
        st = station_of.get(str(r.asset))
        if st is None:
            continue
        rec = {"type": r.type, "asset": str(r.asset), "note": r.note}
        bodies.append(
            mapping.observation_body(
                start, int(r.time_s), rec, datastream_id=m.datastreams[f"MAINT-{st}"]
            )
        )
    w = tb.weather[(tb.weather.time_s >= t0) & (tb.weather.time_s < t1)]
    for _, r in w.iterrows():
        bodies.append(
            mapping.observation_body(
                start,
                int(r.time_s),
                float(r.air_temperature_c),
                datastream_id=m.datastreams["WEATHER"],
            )
        )
    return client.post_observations(bodies)


def _spawn(args: list[str], log_dir: Path, name: str) -> subprocess.Popen[bytes]:
    out = open(log_dir / f"{name}.log", "wb")  # noqa: SIM115 - closed when the process ends
    return subprocess.Popen(
        [sys.executable, "-m", "flowmine.cli", *args], stdout=out, stderr=subprocess.STDOUT
    )


def run(
    cfg: Config,
    config_path: str,
    url: str,
    seed: int,
    hours: float,
    speed: float,
    gateway_port: int,
    dashboard_port: int,
    keep: bool,
    data: Path,
) -> int:
    client = StaClient(url)
    if not client.wait(180):
        raise RuntimeError(f"SensorThings server not reachable at {url}")
    tb = build.ensure(cfg, seed, data)
    virt = virtual_nodes(cfg, tb, seed)
    m = provision(client, cfg, tb.sensors, tb.stations, virt)
    reg = register_models(cfg, tb, data / "registry")
    t0 = tb.periods["test"][0]
    t1 = t0 + int(hours * 3600)
    n = post_sources(client, m, tb, t0, t1)
    log.info(
        "provisioned %d datastreams, posted %d lab/maintenance/weather observations",
        len(m.datastreams),
        n,
    )
    logs = data / "demo-logs"
    logs.mkdir(parents=True, exist_ok=True)
    gw_url = f"http://127.0.0.1:{gateway_port}"
    common = ["--config", config_path, "--seed", str(seed)]
    procs = {
        "gateway": _spawn(
            [
                "gateway",
                "--url",
                url,
                "--port",
                str(gateway_port),
                "--registry",
                str(reg.root),
                *common,
            ],
            logs,
            "gateway",
        ),
        "dashboard": _spawn(
            ["dashboard", "--url", url, "--port", str(dashboard_port), "--config", config_path],
            logs,
            "dashboard",
        ),
        "twin": _spawn(
            [
                "twin",
                "run",
                "--mode",
                "live",
                "--url",
                url,
                "--t0",
                str(t0),
                "--t1",
                str(t1),
                "--config",
                config_path,
            ],
            logs,
            "twin",
        ),
        "central": _spawn(["central", "run", "--url", url, *common], logs, "central"),
    }
    time.sleep(6.0)
    stations = {
        s.station_id: _spawn(
            [
                "edge",
                "run",
                "--station",
                s.station_id,
                "--gateway",
                gw_url,
                "--registry",
                str(reg.root),
                "--t0",
                str(t0),
                "--t1",
                str(t1),
                "--speed",
                str(speed),
                "--store",
                str(data / "stations"),
                *common,
            ],
            logs,
            s.station_id,
        )
        for s in tb.stations
    }
    print(f"dashboard: http://127.0.0.1:{dashboard_port}/  (logs in {logs})", flush=True)
    failed = [k for k, p in stations.items() if p.wait() != 0]
    for name in ("twin", "central"):
        try:
            if procs[name].wait(timeout=180) != 0:
                failed.append(name)
        except subprocess.TimeoutExpired:
            procs[name].terminate()
            failed.append(f"{name} (timed out)")
    if procs["gateway"].poll() is not None:
        failed.append("gateway (exited early)")
    counts = {}
    for name in ("P-15", "SIM-P-15", "EVENTS", "UPLINK-STATS"):
        ds = m.datastreams.get(name) or next(iter(m.datastreams.values()))
        page = client.get(f"Datastreams({ds})/Observations", {"$count": "true", "$top": 1})
        counts[name] = page.get("@iot.count")
    print("observations stored:", counts, "failed processes:", failed, flush=True)
    if keep:
        print("dashboard keeps running; Ctrl-C to stop", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            procs["dashboard"].wait()
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    return 1 if failed else 0
