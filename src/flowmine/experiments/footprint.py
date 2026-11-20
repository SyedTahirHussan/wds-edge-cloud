"""Edge footprint: parameters, serialised size, buffer memory and per-sample time.

Times are measured on the machine running this command (a laptop), under CPython and,
when the ``micropython`` executable is found, under the MicroPython Unix port. They
are not ESP32 timings. Because timings change from run to run this file is produced
by ``flowmine experiments footprint`` and is not part of ``make reproduce``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.edge import calibrate, node, replay, v2
from flowmine.edge import models as edge_models
from flowmine.edge.core import bench, memprobe
from flowmine.testbed import build
from flowmine.testbed.sensors import StationSpec

log = logging.getLogger(__name__)
SRC = Path(__file__).resolve().parents[2]


def _micropython_times(path: Path, n: int) -> dict[str, float]:
    exe = shutil.which("micropython")
    if exe is None:
        return {}
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r}); "
        f"from flowmine.edge.core import bench; bench.main({str(path)!r}, {n})"
    )
    out = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=600, check=True)
    return {line.split()[0]: float(line.split()[1]) for line in out.stdout.splitlines()}


def measure(cfg: Config, out_csv: Path, seed: int, n: int = 2000) -> pd.DataFrame:
    tb = build.ensure(cfg, seed)
    sensor = next(s for s in tb.sensors if s.kind == "pressure")
    bundle = edge_models.fit_sensor(tb, sensor.sensor_id, cfg)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "hw.json").write_text(json.dumps(bundle.hw.state()))
        (d / "mlp.fmw").write_bytes(bundle.mlp_bytes)
        mlp = bundle.mlp()
        (d / "mlp_state.json").write_text(json.dumps(mlp.state()))
        (d / "meta.json").write_text(json.dumps(bundle.meta))
        models, meta, k0 = bench.load_models(str(d))
        cpy = {
            k: float(np.median([bench.run(models[k], meta, n, k0) for _ in range(3)]))
            for k in models
        }
        models, meta, k0 = bench.load_models(str(d))
        mpy = _micropython_times(d, n)
        hw_state_bytes = len(json.dumps(bundle.hw.state()).encode())
    s1, s2 = bundle.hw.s1, bundle.hw.s2
    window = int(str(bundle.meta["flat_window"]))
    rows = [
        {
            "forecaster": "seasonal_naive",
            "parameters": 0,
            "model_bytes": 4 * s2,
            "buffer_samples": s2 + window,
        },
        {
            "forecaster": "holt_winters",
            "parameters": 4,
            "model_bytes": 4 * (s1 + s2) + 6 * 8,
            "buffer_samples": s1 + s2 + window,
        },
        {
            "forecaster": "mlp",
            "parameters": bundle.mlp_params,
            "model_bytes": len(bundle.mlp_bytes),
            "buffer_samples": s1 + window,
        },
    ]
    keys = {"seasonal_naive": "snaive", "holt_winters": "hw", "mlp": "mlp"}
    for r in rows:
        r["us_per_sample_cpython"] = round(cpy[keys[str(r["forecaster"])]], 1)
        mp = mpy.get(keys[str(r["forecaster"])])
        r["us_per_sample_micropython"] = round(mp, 1) if mp is not None else float("nan")
        r["sensor"] = sensor.sensor_id
        r["machine"] = "laptop (not an ESP32)"
    df = pd.DataFrame(rows)
    log.info("holt-winters state as JSON: %d bytes", hw_state_bytes)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


STATIONS = ("PS-10", "TK-1", "LG-131", "LG-101")


def _station_spec(tb: build.Testbed, spec: StationSpec, b: dict[str, Any], cfg: Config) -> dict:
    pipes = {}
    for sid in spec.sensors:
        s = tb.sensor(sid)
        pipes[str(s.local_id)] = {"meta": b[sid].meta, "state": b[sid].hw.state()}
    out: dict[str, Any] = {"number": spec.number, "pipelines": pipes}
    if spec.station_class == "tank" and spec.tank_area_m2:
        lvl = next(tb.sensor(s) for s in spec.sensors if tb.sensor(s).kind == "level")
        flw = next(tb.sensor(s) for s in spec.sensors if tb.sensor(s).kind == "flow")
        out["tank"] = {
            "level": lvl.local_id,
            "flow": flw.local_id,
            "area_m2": spec.tank_area_m2,
            "tol_m": cfg.edge.tank_tol_m,
            "sign": spec.inflow_sign,
        }
    return out


def _package_bytes(spec: StationSpec, b: dict[str, Any]) -> int:
    return sum(
        len(node.model_package(sid, 1, b[sid].hw, meta=b[sid].meta)[0]) for sid in spec.sensors
    )


def _mpy_probe(path: Path) -> dict[str, float]:
    exe = shutil.which("micropython")
    if exe is None:
        return {}
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r}); "
        f"from flowmine.edge.core import memprobe; memprobe.main({str(path)!r})"
    )
    out = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=900, check=True)
    return {line.split()[0]: float(line.split()[1]) for line in out.stdout.splitlines()}


def measure_stations(cfg: Config, out_csv: Path, seed: int, hours: int = 24) -> pd.DataFrame:
    """Heap and time per station for the v0.1 and v0.2 packages (and v0.2 with int8 MLPs)."""
    tb = build.ensure(cfg, seed)
    bundles = {s.sensor_id: edge_models.fit_sensor(tb, s.sensor_id, cfg) for s in tb.sensors}
    v1 = calibrate.calibrate(tb, bundles, cfg, "hw")
    cfg32 = dataclasses.replace(cfg, edge=dataclasses.replace(cfg.edge, xmodel_int8=False))
    fit32 = v2.calibrate(tb, bundles, cfg32)
    q8 = v2.quantised(fit32.bundles)
    t0 = tb.periods["calib"][0]
    rows = []
    for name in STATIONS:
        spec = next(s for s in tb.stations if s.station_id == name)
        local = {sid: tb.sensor(sid).local_id for sid in spec.sensors}
        samples = [
            [local[sid], t, v]
            for t, sid, v in replay.station_samples(tb, spec, t0, t0 + hours * 3600)
        ]
        for version, b in (("v0.1", v1), ("v0.2 float32", fit32.bundles), ("v0.2 int8", q8)):
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                (d / "station.json").write_text(json.dumps(_station_spec(tb, spec, b, cfg)))
                (d / "samples.json").write_text(json.dumps(samples))
                cpy = memprobe.probe(str(d))
                mpy = _mpy_probe(d)
            rows.append(
                {
                    "station": name,
                    "station_class": spec.station_class,
                    "version": version,
                    "sensors": len(spec.sensors),
                    "samples": len(samples),
                    "package_bytes": _package_bytes(spec, b),
                    "heap_built_bytes": mpy.get("built", float("nan")),
                    "heap_resident_bytes": mpy.get("resident", float("nan")),
                    "max_alloc_one_sample_bytes": mpy.get("peak", float("nan"))
                    - mpy.get("resident", float("nan")),
                    "us_per_sample_micropython": round(mpy.get("us_per_sample", float("nan")), 1),
                    "us_per_sample_cpython": round(cpy["us_per_sample"], 1),
                    "machine": "laptop (not an ESP32)",
                }
            )
    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df
