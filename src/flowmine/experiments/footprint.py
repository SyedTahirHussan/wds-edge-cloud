"""Edge footprint: parameters, serialised size, buffer memory and per-sample time.

Times are measured on the machine running this command (a laptop), under CPython and,
when the ``micropython`` executable is found, under the MicroPython Unix port. They
are not ESP32 timings. Because timings change from run to run this file is produced
by ``flowmine experiments footprint`` and is not part of ``make reproduce``.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.edge import models as edge_models
from flowmine.edge.core import bench
from flowmine.testbed import build

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
