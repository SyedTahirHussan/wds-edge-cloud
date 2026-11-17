"""Per-sample timing of the validation pipeline, runnable under MicroPython and CPython.

``micropython -c "import sys; sys.path.insert(0,'src'); from flowmine.edge.core import bench;
bench.main('/path/to/dir')"`` prints one ``kind mean_us`` line per forecaster found in the
directory (hw.json, mlp.fmw). Timings on a laptop say nothing about an ESP32.
"""

import json
import math
import time

from .holtwinters import HoltWinters
from .mlp import MLPForecaster
from .pipeline import SensorPipeline
from .snaive import SeasonalNaive


def _now_us():
    if hasattr(time, "ticks_us"):
        return time.ticks_us()
    return int(time.perf_counter() * 1000000)


def _elapsed(t0, t1):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(t1, t0)
    return t1 - t0


def run(model, meta, n, k0):
    """Mean microseconds per sample for ``n`` synthetic samples after slot ``k0``."""
    p = SensorPipeline(meta, model)
    period = meta["period"]
    base = model.forecast_at((k0 + 1) * period)
    xs = [base + 0.05 * math.sin(i * 0.7) for i in range(n)]
    t0 = _now_us()
    for i in range(n):
        p.process((k0 + 1 + i) * period, xs[i])
    return _elapsed(t0, _now_us()) / n


def load_models(path):
    with open(path + "/hw.json") as f:
        st = json.load(f)
    hw = HoltWinters(st["period"], st["s1"], st["s2"], 0.0, 0.0, 0.0, 0.0)
    hw.load(st)
    sn = SeasonalNaive(st["period"], st["s2"])
    for k in range(st["s2"]):
        sn.update(
            hw.level + hw.d[k % st["s1"]] + hw.w[k], (st["k"] - st["s2"] + 1 + k) * st["period"]
        )
    with open(path + "/mlp.fmw", "rb") as f:
        mlp = MLPForecaster(f.read())
    with open(path + "/mlp_state.json") as f:
        mlp.load(json.load(f))
    with open(path + "/meta.json") as f:
        meta = json.load(f)
    return {"snaive": sn, "hw": hw, "mlp": mlp}, meta, st["k"]


def main(path, n=2000):
    models, meta, k0 = load_models(path)
    for kind in ("snaive", "hw", "mlp"):
        print(kind, run(models[kind], meta, n, k0))
